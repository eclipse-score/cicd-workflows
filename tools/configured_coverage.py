# Copyright (c) 2026 Contributors to the Eclipse Foundation
# SPDX-License-Identifier: Apache-2.0
"""Declarative coverage contracts, scope completeness and Docs artifact selection.

Imports of planner validators are local to avoid a module initialization cycle.
Coverage remains module-owned Bazel configuration; no shell hooks are accepted.
"""

import re
import shutil
import subprocess
import zipfile


# Defaults describe shared SCORE conventions, not consumer-specific policies.
# Explicit values are merged only after validation of field names; null values
# remain explicit and are rejected by the normal type/path validators.
LLVM_DEFAULTS = {
    'bazel-configs': 'llvm_cov',
    'bazel-targets': '//score/...',
    'justifications-yaml': 'tools/coverage/coverage_justifications.yaml',
    'coverage-html-bazel-target': '@score_coverage//:generate_coverage_html',
    'generate-artifacts-archive': True,
}
SCOPE_DEFAULTS = {
    'scope-target': '//tools/coverage:coverage_scope',
    'universe': '//score/...',
    'exclusions': 'tools/coverage/scope_exclusions.txt',
}
DOCS_DEFAULTS = {'bazel-target': '//:docs', 'retention-days': 5, 'github-context': False}


def label(value, path):
    from configured_checks import TARGET, words
    labels = words(value, TARGET, path)
    if len(labels) != 1 or labels[0].startswith('-') or '...' in labels[0]:
        raise ValueError(f'{path}: expected one positive Bazel target')
    return labels[0]


def retention(value):
    if type(value) is not int or not 1 <= value <= 90:
        raise ValueError('retention-days: expected integer between 1 and 90')
    return value


def coverage_row(workspace, entry, family, name, platform, digest):
    """Keep the LLVM template fields; permit a module Bazel orchestrator separately."""
    from configured_checks import CONFIG, DEFAULT_FLAGS, TARGET, fields, inside, words
    if platform != {'os': 'linux', 'architecture': 'x86_64'}:
        raise ValueError('Coverage requires native Linux x86_64')
    common = {'coverage-threshold', 'workflow-config'}
    llvm = {'bazel-configs', 'bazel-targets', 'justifications-yaml',
            'coverage-html-bazel-target', 'generate-artifacts-archive',
            'testlogs-subdir', 'scope-check'}
    required = set() if family == 'llvm-coverage' else {'bazel-target'}
    fields(entry, common | (llvm if family == 'llvm-coverage' else {'bazel-target', 'output-directory'}), required, family)
    entry = {**(LLVM_DEFAULTS if family == 'llvm-coverage' else {'output-directory': 'coverage_artifacts'}), **entry}
    options = entry.get('workflow-config', {})
    fields(options, {'working-directory', 'artifact-name', 'retention-days'}, set(), f'{family}.workflow-config')
    directory = inside(workspace, options.get('working-directory', '.'))
    if not all((directory / file).is_file() for file in ('MODULE.bazel', 'MODULE.bazel.lock')):
        raise ValueError('Coverage requires MODULE.bazel and its lockfile')
    threshold = entry.get('coverage-threshold', 0)
    if type(threshold) not in (int, float) or not 0 <= threshold <= 100:
        raise ValueError('coverage-threshold: expected number between 0 and 100')
    artifact = options.get('artifact-name', '')
    if not isinstance(artifact, str) or (artifact and not re.fullmatch(r'[A-Za-z0-9_.-]+', artifact.replace('{repository}', 'repository'))):
        raise ValueError('artifact-name: expected a simple name with optional {repository}')
    metadata = {'threshold': threshold, 'artifact-name': artifact,
                'retention-days': retention(options.get('retention-days', 10))}
    if family == 'coverage':
        output = inside(directory, entry['output-directory'])
        if output == directory:
            raise ValueError('output-directory must be a dedicated subdirectory')
        metadata['output-directory'] = output.relative_to(directory).as_posix()
        commands = [['bazel', 'run', '--lockfile_mode=error', label(entry['bazel-target'], family), '--']]
    else:
        configs = entry['bazel-configs']
        if not isinstance(configs, str) or not all(CONFIG.fullmatch(c.strip()) for c in configs.split(',')):
            raise ValueError('bazel-configs: expected nonempty config names')
        flags = [*DEFAULT_FLAGS, *[f'--config={c.strip()}' for c in configs.split(',')]]
        justification = inside(directory, entry['justifications-yaml'])
        if not justification.is_file():
            raise ValueError('justifications-yaml: file does not exist')
        metadata['output-directory'] = 'coverage-report'
        archive = entry['generate-artifacts-archive']
        if type(archive) is not bool:
            raise ValueError('generate-artifacts-archive: expected boolean')
        args = ['--yaml', justification.relative_to(directory).as_posix()]
        if 'testlogs-subdir' in entry:
            subdir = inside(directory, entry['testlogs-subdir']).relative_to(directory).as_posix()
            args += ['--testlogs-subdir', subdir]
        if archive:
            args += ['--archive-dir', metadata['output-directory']]
        else:
            args += [metadata['output-directory']]
        commands = [
            ['bazel', 'coverage', *flags, '--build_tests_only', '--', *words(entry['bazel-targets'], TARGET, family)],
            ['bazel', 'run', '--lockfile_mode=error',
             label(entry['coverage-html-bazel-target'], family), '--', *args],
        ]
        if 'scope-check' in entry:
            scope = entry['scope-check']
            fields(scope, set(SCOPE_DEFAULTS), set(), 'scope-check')
            scope = {**SCOPE_DEFAULTS, **scope}
            label(scope['scope-target'], 'scope-target')
            universe = words(scope['universe'], TARGET, 'universe')
            if len(universe) != 1 or not re.fullmatch(r'//[A-Za-z0-9_/]+/\.\.\.', universe[0]):
                raise ValueError('scope-check.universe: expected //package/...')
            if not inside(directory, scope['exclusions']).is_file():
                raise ValueError('scope-check.exclusions: file does not exist')
            metadata['scope-check'] = scope
    return {'id': f'{name}-{family}', 'config-sha256': digest,
            'working-directory': directory.relative_to(workspace).as_posix(),
            'execution': 'native', 'has-tests': family == 'llvm-coverage',
            'commands': commands, 'coverage': metadata}


def scope_difference(universe, scoped, excluded):
    """An exclusion becomes stale as soon as its production target is reachable."""
    missing, stale = universe - scoped - excluded, scoped & excluded
    if missing or stale:
        raise ValueError(f'Coverage scope incomplete: missing={sorted(missing)}, stale exclusions={sorted(stale)}')


def check_scope(directory, scope):
    """Compare unconfigured production targets with configured scope dependencies.

    Query avoids contradictory config_setting stubs in the production universe.
    cquery resolves the actual scope deps, excluding implicit toolchain deps.
    """
    from configured_checks import inside
    universe = scope['universe']
    def query(command, expression):
        result = subprocess.check_output(['bazel', command, '--lockfile_mode=error',
                                          *(['--noimplicit_deps'] if command == 'cquery' else []),
                                          expression, '--output=label'], cwd=directory, text=True)
        return {line.split()[0] for line in result.splitlines() if line.strip()}
    all_targets = query('query', f'kind("cc_library|rust_library", {universe}) except attr(testonly, 1, {universe}) except attr("target_compatible_with", "qnx", {universe})')
    scoped = query('cquery', f'kind("cc_library|rust_library", deps(labels(deps, {scope["scope-target"]})))')
    prefix = universe.removesuffix('...')
    scoped = {target for target in scoped if target.startswith(prefix) or target.startswith(prefix.rstrip('/') + ':')}
    exclusions = inside(directory, scope['exclusions']).read_text()
    excluded = set(re.findall(r'^//[^ #\s]+', exclusions, re.MULTILINE))
    scope_difference(all_targets, scoped, excluded)


def collect_coverage(directory, destination, metadata):
    """Stage HTML and normalize LLVM ZIP/plain reports for the Docs contract."""
    from configured_checks import inside
    source = inside(directory, metadata['output-directory'])
    if not source.is_dir() or not any(source.rglob('*.html')):
        raise ValueError('Coverage did not produce an HTML report')
    shutil.copytree(source, destination, dirs_exist_ok=True)
    candidates = [source / 'coverage.lcov', source / 'coverage_report.dat',
                  directory / 'bazel-out/_coverage/_coverage_report.dat']
    report = next((path for path in candidates if path.is_file()), None)
    if report is None:
        raise ValueError('Coverage did not produce an LCOV report')
    if zipfile.is_zipfile(report):
        with zipfile.ZipFile(report) as archive:
            if 'lcov_report/lcov.dat' not in archive.namelist():
                raise ValueError('Coverage ZIP does not contain lcov_report/lcov.dat')
            data = archive.read('lcov_report/lcov.dat')
    else:
        data = report.read_bytes()
    if b'SF:' not in data or b'end_of_record' not in data:
        raise ValueError('Coverage report does not contain LCOV records')
    (destination / 'coverage_report.dat').write_bytes(data)
    # Both supported names refer to plain LCOV, even if a source archive used ZIP.
    (destination / 'coverage.lcov').write_bytes(data)
    # Keep helper-generated Cobertura reports, otherwise derive one from the
    # same normalized LCOV used by Docs. The converter is centrally locked.
    if not (destination / 'coverage.xml').is_file():
        from lcov_cobertura import LcovCobertura
        (destination / 'coverage.xml').write_text(LcovCobertura(data.decode(), base_dir=str(directory)).convert())


def docs_plan(workspace, entry, rows):
    """Resolve row IDs once, before launching any expensive matrix work."""
    from configured_checks import fields
    if entry is None:
        return {}
    fields(entry, {'bazel-target', 'tests', 'coverage', 'retention-days', 'github-context'}, set(), 'docs')
    entry = {**DOCS_DEFAULTS, **entry}
    result = {'bazel-target': label(entry['bazel-target'], 'docs.bazel-target'),
              'retention-days': retention(entry['retention-days']),
              'github-context': entry['github-context']}
    if type(result['github-context']) is not bool:
        raise ValueError('docs.github-context: expected boolean')
    for key in ('tests', 'coverage'):
        # Root documentation can consume one native test row and one coverage
        # row. Ambiguity requires an explicit selection instead of relying on
        # variant order; an explicit empty string disables that report.
        if key not in entry:
            eligible = [row for row in rows if row['working-directory'] == '.' and
                        (row['has-tests'] and row['execution'] == 'native' and 'coverage' not in row
                         if key == 'tests' else 'coverage' in row)]
            if len(eligible) > 1:
                raise ValueError(f'docs.{key}: ambiguous report sources; select a row ID')
            selected = eligible[0]['id'] if eligible else ''
        else:
            selected = entry[key]
        if not isinstance(selected, str):
            raise ValueError(f'docs.{key}: expected a row ID')
        candidates = [row for row in rows if row['id'] == selected]
        if selected and (len(candidates) != 1 or candidates[0]['working-directory'] != '.' or
                         not (candidates[0]['has-tests'] if key == 'tests' else 'coverage' in candidates[0])):
            raise ValueError(f'docs.{key}: select a root workspace row with {key}')
        result[key] = selected
    return result
