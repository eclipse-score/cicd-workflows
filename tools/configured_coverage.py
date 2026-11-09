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


# Only execution/reporting conventions have shared defaults. Module-owned Bazel
# configs, target scopes and input paths must be supplied by each profile.
SCOPE_FIELDS = {'scope-target', 'universe', 'exclusions'}
DOCS_DEFAULTS = {'bazel-target': '//:docs', 'retention-days': 5}


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
    """Plan a coverage profile with explicit module inputs and shared reporting.

    A module may run its own complete Bazel pipeline, supply a separate Bazel
    report generator, or use the shared LCOV/genhtml reporter. This choice does
    not select a compiler or a target system; those remain explicit profile facts.
    """
    from configured_checks import CONFIG, DEFAULT_FLAGS, TARGET, fields, inside, words
    from configured_profiles import platform_profile, result_metadata

    # Schema 1 compatibility translates field names only. It does not invent
    # module-specific configs, target scopes or files that the caller omitted.
    entry = dict(entry) if isinstance(entry, dict) else entry
    if family in ('llvm-coverage', 'gcov-coverage') and isinstance(entry, dict):
        for old, new in (('coverage-html-bazel-target', 'report-bazel-target'),
                         ('coverage-html-bazel-configs', 'report-bazel-configs')):
            if old in entry:
                if new in entry:
                    raise ValueError(f'coverage: duplicate {new}')
                entry[new] = entry.pop(old)
    common = {'coverage-threshold', 'workflow-config'}
    direct = {'bazel-configs', 'bazel-targets'}
    reporting = {'report-bazel-target', 'report-bazel-configs', 'justifications-yaml',
                 'generate-artifacts-archive', 'testlogs-subdir', 'scope-check'}
    module = isinstance(entry, dict) and 'bazel-target' in entry
    reporter = 'module' if module else ('bazel' if family == 'llvm-coverage' or (isinstance(entry, dict) and 'report-bazel-target' in entry) else 'genhtml')
    required = {'bazel-target', 'output-directory'} if module else direct
    if reporter == 'bazel':
        required |= {'report-bazel-target', 'justifications-yaml'}
    fields(entry, common | ({'bazel-target', 'output-directory'} if module else direct | reporting), required, 'coverage')
    if reporter == 'genhtml' and set(entry) & (reporting - {'scope-check'}):
        raise ValueError('coverage: report generator options require report-bazel-target')
    options = entry.get('workflow-config', {})
    fields(options, {'working-directory', 'execution', 'artifact-name', 'retention-days'}, set(), 'coverage.workflow-config')
    execution = platform_profile(platform, 'tests', options)
    if platform == {'os': 'linux', 'architecture': 'aarch64'} and execution != 'qemu-user':
        raise ValueError('coverage: Linux aarch64 requires explicit qemu-user execution')
    directory = inside(workspace, options.get('working-directory', '.'))
    if not all((directory / file).is_file() for file in ('MODULE.bazel', 'MODULE.bazel.lock')):
        raise ValueError('Coverage requires MODULE.bazel and its lockfile')
    threshold = entry.get('coverage-threshold', 0)
    if type(threshold) not in (int, float) or not 0 <= threshold <= 100:
        raise ValueError('coverage-threshold: expected number between 0 and 100')
    artifact = options.get('artifact-name', '')
    if not isinstance(artifact, str) or (artifact and not re.fullmatch(r'[A-Za-z0-9_.-]+', artifact.replace('{repository}', 'repository'))):
        raise ValueError('artifact-name: expected a simple name with optional {repository}')
    metadata = {'threshold': threshold, 'artifact-name': artifact, 'reporter': reporter,
                'retention-days': retention(options.get('retention-days', 10))}

    def configs(key, *, optional=False):
        value = entry.get(key, '')
        if not isinstance(value, str) or (not value and not optional) or (value and not all(CONFIG.fullmatch(c.strip()) for c in value.split(','))):
            raise ValueError(f'coverage.{key}: expected config names')
        return [f'--config={c.strip()}' for c in value.split(',')] if value else []

    if module:
        output = inside(directory, entry['output-directory'])
        if output == directory:
            raise ValueError('output-directory must be a dedicated subdirectory')
        metadata['output-directory'] = output.relative_to(directory).as_posix()
        commands = [['bazel', 'run', '--lockfile_mode=error', label(entry['bazel-target'], 'coverage'), '--']]
    else:
        flags = [*DEFAULT_FLAGS, *configs('bazel-configs')]
        extra = ['--test_timeout=180,900,2700,10800'] if execution == 'qemu-user' else []
        report_flags = ['--combined_report=lcov'] if reporter == 'genhtml' else ['--build_tests_only']
        commands = [['bazel', 'coverage', *flags, *report_flags, *extra, '--',
                     *words(entry['bazel-targets'], TARGET, 'coverage.bazel-targets')]]
        metadata['output-directory'] = 'coverage-report'
        if reporter == 'bazel':
            justification = inside(directory, entry['justifications-yaml'])
            if not justification.is_file():
                raise ValueError('justifications-yaml: file does not exist')
            archive = entry.get('generate-artifacts-archive', True)
            if type(archive) is not bool:
                raise ValueError('generate-artifacts-archive: expected boolean')
            args = ['--yaml', justification.relative_to(directory).as_posix()]
            if 'testlogs-subdir' in entry:
                subdir = inside(directory, entry['testlogs-subdir']).relative_to(directory).as_posix()
                args += ['--testlogs-subdir', subdir]
            args += ['--archive-dir', metadata['output-directory']] if archive else [metadata['output-directory']]
            # Coverage-only rc sections cannot be passed to a Bazel run target.
            # Report tool configs are separately selected by the module.
            commands.append(['bazel', 'run', '--lockfile_mode=error', *configs('report-bazel-configs', optional=True),
                             label(entry['report-bazel-target'], 'coverage.report-bazel-target'), '--', *args])
        if 'scope-check' in entry:
            scope = entry['scope-check']
            fields(scope, SCOPE_FIELDS, SCOPE_FIELDS, 'scope-check')
            label(scope['scope-target'], 'scope-target')
            universe = words(scope['universe'], TARGET, 'universe')
            if len(universe) != 1 or not re.fullmatch(r'//[A-Za-z0-9_/]+/\.\.\.', universe[0]):
                raise ValueError('scope-check.universe: expected //package/...')
            if not inside(directory, scope['exclusions']).is_file():
                raise ValueError('scope-check.exclusions: file does not exist')
            metadata['scope-check'] = dict(scope)
    return {'id': f'{name}-coverage' if family == 'coverage' else f'{name}-{family}', 'config-sha256': digest,
            'metadata': result_metadata(name, platform, 'coverage',
                                        working_directory=directory.relative_to(workspace).as_posix()),
            'working-directory': directory.relative_to(workspace).as_posix(),
            'execution': execution, 'platform': platform,
            'prerequisites': {'bazel': 'llvm-coverage', 'genhtml': 'gcov-coverage', 'module': ''}[reporter],
            'has-tests': not module, 'commands': commands, 'coverage': metadata}


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
    data = normalized_lcov(report)
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
    fields(entry, {'bazel-target', 'tests', 'coverage', 'retention-days', 'github-context', 'check', 'link-check'}, set(), 'docs')
    entry = {**DOCS_DEFAULTS, **entry}
    result = {'bazel-target': label(entry['bazel-target'], 'docs.bazel-target'),
              'retention-days': retention(entry['retention-days'])}
    # Accept the deprecated boolean for old callers, but never emit unused CLI
    # options. The Docs workflow always supplies repository context via env.
    if 'github-context' in entry and type(entry['github-context']) is not bool:
        raise ValueError('docs.github-context: expected boolean')
    for key in ('tests', 'coverage'):
        # Root documentation can consume one native test row and one coverage
        # row. Multiple candidates require an explicit source selection.
        # Ambiguity requires an explicit selection instead of relying on
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
        if isinstance(selected, list) and any(not value for value in selected):
            raise ValueError(f'docs.{key}: list entries must be nonempty row IDs')
        selections = selected if key == 'tests' and isinstance(selected, list) else [selected]
        if any(not isinstance(value, str) for value in selections) or len(set(selections)) != len(selections):
            raise ValueError(f'docs.{key}: expected unique row IDs')
        for selection in selections:
            candidates = [row for row in rows if row['id'] == selection]
            if selection and (len(candidates) != 1 or candidates[0]['working-directory'] != '.' or
                              not (candidates[0]['has-tests'] if key == 'tests' else 'coverage' in candidates[0])):
                raise ValueError(f'docs.{key}: select a root workspace row with {key}')
        result[key] = selected
    return result


def normalized_lcov(report):
    """Bazel toolchains may emit either plain LCOV or a ZIP container."""
    if zipfile.is_zipfile(report):
        with zipfile.ZipFile(report) as archive:
            if 'lcov_report/lcov.dat' not in archive.namelist():
                raise ValueError('Coverage ZIP does not contain lcov_report/lcov.dat')
            data = archive.read('lcov_report/lcov.dat')
    else:
        data = report.read_bytes()
    if b'SF:' not in data or b'end_of_record' not in data:
        raise ValueError('Coverage report does not contain LCOV records')
    return data
