# Copyright (c) 2026 Contributors to the Eclipse Foundation
# SPDX-License-Identifier: Apache-2.0
"""Typed documentation, Cargo, commit-policy and Bazel CodeQL profiles.

Module YAML selects checks and analysis policy. Recipes, prerequisites and
report verification belong to the shared workflow; shell hooks are not accepted.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tomllib
import uuid

import yaml

from configured_checks import (CONFIG, Loader, commit, evidence, fields, inside,
                               output, words, TARGET)
from configured_coverage import label
from configured_profiles import result_metadata

CODEQL_DEFAULTS = {
    'bazel-target': '//quality/static_analysis:codeql_lint',
    'target-universe': '//score/...',
    'reports': ['cert-cpp-l1', 'cert-c-l1', 'cpp-code-scanning', 'code-complexity'],
    'fail-on-findings': True,
}


def plan(workspace, document, digest, *, only_codeql=False, fetch_only=False, fail_override=''):
    """Validate all opted-in profiles, including prerequisites, before execution."""
    workspace = Path(workspace).resolve()
    rows = []
    if 'clippy' in document:
        fields(document['clippy'], set(), set(), 'clippy')
        for file in ('Cargo.toml', 'Cargo.lock', 'rust-toolchain.toml'):
            if not inside(workspace, file).is_file():
                raise ValueError(f'clippy requires {file}')
        channel = tomllib.loads((workspace / 'rust-toolchain.toml').read_text())['toolchain']['channel']
        if not isinstance(channel, str) or not re.fullmatch(r'\d+\.\d+\.\d+', channel):
            raise ValueError('clippy requires a pinned Rust version in rust-toolchain.toml')
        rows.append({'id': 'clippy', 'toolchain': channel})
    docs = document.get('docs')
    if docs is not None:
        for key, target in (('check', '//:docs_check'), ('link-check', '//:docs_link_check')):
            if key in docs:
                enabled = docs[key]
                if type(enabled) is not bool:
                    raise ValueError(f'docs.{key}: expected boolean')
                if enabled:
                    rows.append({'id': f'docs-{key}', 'bazel-target': target})
    if 'gitlint' in document:
        if type(document['gitlint']) is not bool:
            raise ValueError('gitlint: expected boolean')
        if document['gitlint']:
            selected = os.environ.get('SOURCE_REPOSITORY', '')
            if (os.environ.get('GITHUB_EVENT_NAME') == 'pull_request' and selected and
                    selected != os.environ.get('GITHUB_REPOSITORY')):
                raise ValueError('Gitlint PR context belongs to another repository')
            rows.append({'id': 'gitlint'})
    if 'codeql' in document:
        fields(document['codeql'], set(CODEQL_DEFAULTS) | {'bazel-configs'}, {'bazel-configs'}, 'codeql')
        options = {**CODEQL_DEFAULTS, **document['codeql']}
        configs = options['bazel-configs']
        if not isinstance(configs, str) or not all(CONFIG.fullmatch(c.strip()) for c in configs.split(',')):
            raise ValueError('codeql.bazel-configs: expected nonempty config names')
        options['bazel-configs'] = [c.strip() for c in configs.split(',')]
        options['bazel-target'] = label(options['bazel-target'], 'codeql.bazel-target')
        universe = words(options['target-universe'], TARGET, 'codeql.target-universe')
        if len(universe) != 1 or not re.fullmatch(r'//[A-Za-z0-9_/]+/\.\.\.', universe[0]):
            raise ValueError('codeql.target-universe: expected //package/...')
        reports = options['reports']
        if (not isinstance(reports, list) or not reports or
                any(not isinstance(r, str) or not CONFIG.fullmatch(r) for r in reports)):
            raise ValueError('codeql.reports: expected nonempty list of report names')
        if type(options['fail-on-findings']) is not bool:
            raise ValueError('codeql.fail-on-findings: expected boolean')
        if fail_override:
            if fail_override not in ('true', 'false'):
                raise ValueError('Invalid fail-on-findings override')
            options['fail-on-findings'] = fail_override == 'true'
        rows.append({'id': 'codeql', **options, 'fetch-only': fetch_only})
    rows.extend(additional_profiles(workspace, document))
    if only_codeql:
        rows = [row for row in rows if row['id'] == 'codeql']
        if not rows:
            raise ValueError('No CodeQL profile configured')
    result = []
    for row in rows:
        platform = row.get('platform', {'os': 'linux', 'architecture': 'x86_64'})
        metadata = row.get('metadata', result_metadata(row['id'], platform, row['id']))
        result.append({'platform': platform, **row, 'metadata': metadata, 'config-sha256': digest})
    return result


def load_plan(workspace, config):
    # Use the Bazel planner too: the shared entry point rejects misspellings
    # anywhere in the document, even when this invocation selects CodeQL only.
    from configured_checks import plan as bazel_plan
    workspace = Path(workspace).resolve()
    bazel_plan(workspace, config)
    content = inside(workspace, config).read_text()
    document = yaml.load(content, Loader=Loader)
    return plan(workspace, document, hashlib.sha256(content.encode()).hexdigest(),
                only_codeql=os.environ.get('ONLY_CODEQL') == 'true',
                fetch_only=os.environ.get('FETCH_ONLY') == 'true',
                fail_override=os.environ.get('FAIL_ON_FINDINGS', ''))


def recipes(row, destination, *, targets=(), policy='', base='', head=''):
    """One command contract is used by execution and completeness verification."""
    if row['id'].startswith('docs-'):
        return [['bazel', 'run', '--lockfile_mode=error', row['bazel-target']]]
    if row['id'] == 'clippy':
        channel = row['toolchain']
        return [
            ['rustup', 'toolchain', 'install', channel, '--profile', 'minimal', '--component', 'clippy'],
            ['cargo', f'+{channel}', 'clippy', '--locked', '--all-features', '--all-targets',
             '--workspace', '--message-format=json', '--', '-D', 'warnings'],
        ]
    if row['id'] == 'gitlint':
        return [['git', 'rev-list', f'{base}..{head}'],
                ['gitlint', '--config', policy, '--commits', f'{base}..{head}']]
    if row.get('kind') == 'sbom':
        return [['bazel', 'build', '--lockfile_mode=error',
                 *[f'--config={c}' for c in row['bazel-configs']], '--', row['bazel-target']]]
    if row.get('kind') == 'sca':
        args = [f'--config={c}' for c in row['bazel-configs']]
        args += [f'--platform={row["platform"]["os"]}']
        if row.get('safety-level'):
            args.append(f'--safety-level={row["safety-level"]}')
        args += [f'--report={r}' for r in row['reports']]
        environment = ['--action_env=QNX_HOST', '--action_env=QNX_TARGET'] if row['platform']['os'] == 'qnx' else []
        return [['bazel', 'run', '--lockfile_mode=error', *environment, row['bazel-target'], '--', *args, *row['bazel-targets']]]
    flags = ['--lockfile_mode=error', *[f'--config={c}' for c in row['bazel-configs']]]
    if row['fetch-only']:
        return [['bazel', 'build', *flags, '--nobuild', '//...']]
    linux, merged = destination / 'linux', destination / 'publish/codeql-nightly.sarif'
    return [
        ['bazel', 'query', '--lockfile_mode=error', f'kind("cc_library|cc_binary", {row["target-universe"]})'],
        ['bazel', 'run', *flags, row['bazel-target'], '--', '--output-dir', str(linux),
         '--output-prefix', 'codeql-nightly', '--report', *row['reports'], '--target', *targets],
        ['bazel', 'run', *flags, '@sarif_multitool//:sarif_multitool_cli', '--', 'merge',
         str(linux / 'codeql-nightly.sarif'), '--merge-empty-logs', '--output-file', str(merged)],
    ]


def findings(path):
    """Preserve the module's rule: any SARIF suppression suppresses a result."""
    sarif = json.loads(path.read_text())
    if sarif.get('version') != '2.1.0' or not isinstance(sarif.get('runs'), list) or not sarif['runs']:
        raise ValueError('CodeQL did not produce valid nonempty SARIF runs')
    results = []
    for run in sarif['runs']:
        if not isinstance(run, dict) or not isinstance(run.get('tool', {}).get('driver', {}).get('name'), str):
            raise ValueError('SARIF run is missing its analysis tool')
        results_in_run = run.get('results', [])
        if not isinstance(results_in_run, list) or any(not isinstance(r, dict) for r in results_in_run):
            raise ValueError('SARIF results must be a list of findings')
        for result in results_in_run:
            if not result.get('suppressions'):
                results.append(result)
    return results


def execute(workspace, row, reports):
    """Keep evidence and partial analysis artifacts on every execution failure."""
    workspace = Path(workspace).resolve()
    destination = Path(reports).resolve() / row['id']
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    report = {'id': row['id'], 'plan': row, 'metadata': row['metadata'], 'commit': commit(workspace),
              'destination': str(destination),
              'outcome': 'failure', 'commands': []}

    def run(argv, *, bazel=False, capture=False):
        observed = {'argv': argv, 'exit-code': None}
        report['commands'].append(observed)
        actual = list(argv)
        bep = destination / f'{len(report["commands"])}.bep.jsonl'
        if bazel:
            # Bazel run has program arguments after --; evidence is a Bazel flag.
            actual.insert(2, f'--build_event_json_file={bep}')
        print(shlex.join(actual), flush=True)
        environment = dict(os.environ)
        if row['id'].startswith('docs-') and environment.get('SOURCE_REPOSITORY'):
            environment['GITHUB_REPOSITORY'] = environment['SOURCE_REPOSITORY']
        result = subprocess.run(actual, cwd=workspace, text=True, env=environment,
                                stdout=subprocess.PIPE if capture else None, check=False)
        observed['exit-code'] = result.returncode
        if capture:
            (destination / f'{len(report["commands"])}.stdout').write_text(result.stdout)
            if row['id'] == 'clippy':
                (destination / 'cargo.jsonl').write_text(result.stdout)
                # Cargo sends diagnostics as JSON on stdout in this mode. Keep
                # rendered diagnostics visible even when compilation fails.
                for line in result.stdout.splitlines():
                    item = json.loads(line)
                    if item.get('reason') == 'compiler-message':
                        print(item.get('message', {}).get('rendered', ''), end='', flush=True)
        if result.returncode:
            raise ValueError(f'{argv[0]} {argv[1]} failed ({result.returncode})')
        if bazel:
            observed['evidence-count'] = evidence(bep, argv[1])
        return result.stdout

    try:
        if row['id'].startswith('docs-'):
            run(recipes(row, destination)[0], bazel=True)
        elif row['id'] == 'clippy':
            setup, lint = recipes(row, destination)
            run(setup)
            text = run(lint, capture=True)
            (destination / 'cargo.jsonl').write_text(text)
            manifests = {path.resolve() for path in workspace.rglob('Cargo.toml')}
            artifacts = [json.loads(line) for line in text.splitlines() if line.strip()]
            # A green Cargo exit with no module compilation is insufficient.
            count = sum(item.get('reason') == 'compiler-artifact' and
                        Path(item.get('manifest_path', '/')).resolve() in manifests for item in artifacts)
            if not count or not any(item.get('reason') == 'build-finished' and item.get('success') for item in artifacts):
                raise ValueError('Clippy did not check any workspace targets')
            report['workspace-artifacts'] = count
        elif row['id'] == 'gitlint':
            if os.environ.get('GITHUB_EVENT_NAME') != 'pull_request':
                report['outcome'] = 'not-applicable'
                return 0
            policy = Path(os.environ['GITLINT_POLICY'])
            if not policy.is_file():
                raise ValueError('Missing shared SCORE gitlint policy')
            base, head = os.environ['PR_BASE_SHA'], os.environ['PR_HEAD_SHA']
            if any(not re.fullmatch(r'[0-9a-f]{40}', value) for value in (base, head)):
                raise ValueError('Gitlint requires observed PR base and head SHAs')
            report['policy'] = str(policy)
            query, lint = recipes(row, destination, policy=str(policy), base=base, head=head)
            revisions = run(query, capture=True).splitlines()
            if not revisions:
                raise ValueError('Gitlint found no PR commits')
            run(lint)
            report.update({'policy-sha256': hashlib.sha256(policy.read_bytes()).hexdigest(),
                           'commits': revisions, 'base': base, 'head': head})
        elif row.get('kind') in ('sbom', 'sca'):
            execute_additional(workspace, row, destination, report, run)
        elif row['id'] == 'codeql':
            commands = recipes(row, destination)
            if row['fetch-only']:
                # --nobuild intentionally has no completed-target evidence.
                # It is a dependency-fetch operation, never analysis evidence.
                run(commands[0])
            else:
                targets = run(commands[0], capture=True).splitlines()
                if not targets or any(not TARGET.fullmatch(t) or t.startswith('-') for t in targets):
                    raise ValueError('CodeQL query selected no valid production targets')
                report['targets'] = targets
                (destination / 'publish').mkdir()
                _, analysis, merge = recipes(row, destination, targets=targets)
                run(analysis, bazel=True)
                run(merge, bazel=True)
                merged = destination / 'publish/codeql-nightly.sarif'
                unsuppressed = findings(merged)
                report['unsuppressed-findings'] = len(unsuppressed)
                for finding in unsuppressed:
                    locations = finding.get('locations') or [{}]
                    location = locations[0].get('physicalLocation', {})
                    uri = location.get('artifactLocation', {}).get('uri', '?')
                    line = location.get('region', {}).get('startLine', '?')
                    print(f'{finding.get("ruleId", "unknown-rule")}: '
                          f'{finding.get("message", {}).get("text", "")} ({uri}:{line})', flush=True)
                if row['fail-on-findings'] and unsuppressed:
                    raise ValueError(f'CodeQL reported {len(unsuppressed)} unsuppressed finding(s)')
        else:
            raise ValueError('Unknown quality profile')
        report['outcome'] = 'success'
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        report['error'] = str(error)
        print(str(error), file=sys.stderr)
    finally:
        (destination / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    return 0 if report['outcome'] == 'success' else 1


def verify(expected, reports, source_commit):
    """Reject incomplete, duplicate, stale and failed quality evidence."""
    if not isinstance(expected, list) or len({r['id'] for r in expected}) != len(expected):
        raise ValueError('Expected unique quality profiles')
    actual = {}
    for path in Path(reports).rglob('report.json'):
        report = json.loads(path.read_text())
        if report['id'] in actual:
            raise ValueError('Duplicate quality report')
        actual[report['id']] = report
    if set(actual) != {row['id'] for row in expected}:
        raise ValueError('Missing or unexpected quality reports')
    for row in expected:
        report = actual[row['id']]
        if report.get('metadata') != row['metadata']:
            raise ValueError('Quality producer metadata differs from preparation')
        if report.get('plan') != row or report.get('commit') != source_commit:
            raise ValueError('Quality checkout or config differs from preparation')
        if report.get('outcome') == 'not-applicable' and row['id'] == 'gitlint' and os.environ.get('GITHUB_EVENT_NAME') != 'pull_request':
            continue
        commands = report.get('commands', [])
        if report.get('outcome') != 'success' or not commands or any(c.get('exit-code') != 0 for c in commands):
            raise ValueError(f'{row["id"]}: quality check did not succeed')
        expected_commands = recipes(row, Path(report['destination']), targets=report.get('targets', []),
                                    policy=report.get('policy', ''), base=report.get('base', ''),
                                    head=report.get('head', ''))
        if [c.get('argv') for c in commands] != expected_commands:
            raise ValueError('Planned quality commands were skipped or changed')
        if row['id'].startswith('docs-') or (row['id'] == 'codeql' and not row['fetch-only']):
            count = 1 if row['id'].startswith('docs-') else 2
            if sum(type(c.get('evidence-count')) is int and c['evidence-count'] > 0 for c in commands) != count:
                raise ValueError('Missing Bazel analysis evidence')
        if row.get('kind') in ('sbom', 'sca'):
            if len(commands) != 1 or commands[0].get('evidence-count', 0) < 1:
                raise ValueError('Missing SBOM/SCA Bazel evidence')
            if row['kind'] == 'sbom' and report.get('formats') != sorted(row['outputs']):
                raise ValueError('Missing SBOM format evidence')
            if row['kind'] == 'sca':
                count = report.get('unsuppressed-findings')
                if (not report.get('sarif-files') or type(count) is not int or count < 0 or
                        (row['fail-on-findings'] and count)):
                    raise ValueError('Missing SCA report evidence or unsuppressed findings')
        if row['id'] == 'clippy' and (type(report.get('workspace-artifacts')) is not int or report['workspace-artifacts'] < 1):
            raise ValueError('Missing Clippy workspace evidence')
        if row['id'] == 'gitlint' and (not report.get('commits') or not report.get('policy-sha256') or
                                       report.get('base') != os.environ.get('PR_BASE_SHA') or
                                       report.get('head') != os.environ.get('PR_HEAD_SHA')):
            raise ValueError('Missing or mismatched PR commit evidence')
        if row['id'] == 'codeql' and not row['fetch-only']:
            count = report.get('unsuppressed-findings')
            if not report.get('targets') or type(count) is not int or count < 0 or (row['fail-on-findings'] and count):
                raise ValueError('Missing SARIF evidence or unsuppressed findings')
    return {'commit': source_commit, 'jobs': actual}



def additional_profiles(workspace, document):
    """Port SCA/SBOM job selection without organization-specific publication."""
    from configured_checks import NAME
    rows = []
    for kind in ('sca', 'sbom'):
        if kind not in document:
            continue
        profiles = document[kind]
        if not isinstance(profiles, dict) or not profiles:
            raise ValueError(f'{kind}: expected named profiles')
        for name, entry in profiles.items():
            if not isinstance(name, str) or not NAME.fullmatch(name):
                raise ValueError(f'{kind}: invalid profile name')
            common = {'bazel-target', 'bazel-configs', 'os', 'architecture'}
            fields(entry, common | ({'bazel-targets', 'reports', 'safety-level', 'fail-on-findings'}
                                   if kind == 'sca' else {'cyclonedx', 'spdx'}),
                   {'bazel-target', 'bazel-configs'} | ({'bazel-targets'} if kind == 'sca' else set()), kind)
            configs = entry['bazel-configs']
            if not isinstance(configs, str) or not all(CONFIG.fullmatch(c.strip()) for c in configs.split(',')):
                raise ValueError(f'{kind}.bazel-configs: expected nonempty config names')
            platform = {'os': entry.get('os', 'linux'), 'architecture': entry.get('architecture', 'x86_64')}
            if platform['os'] not in ('linux', 'qnx') or platform['architecture'] not in ('x86_64', 'aarch64'):
                raise ValueError(f'{kind}: unsupported platform')
            if not all((workspace / f).is_file() for f in ('MODULE.bazel', 'MODULE.bazel.lock')):
                raise ValueError(f'{kind} requires MODULE.bazel and its lockfile')
            row = {'id': f'{kind}-{name}', 'kind': kind, 'platform': platform,
                   'metadata': result_metadata(name, platform, kind),
                   'bazel-target': label(entry['bazel-target'], kind),
                   'bazel-configs': [c.strip() for c in configs.split(',')]}
            if kind == 'sbom':
                outputs = {key: entry[key] for key in ('cyclonedx', 'spdx') if key in entry}
                if not outputs:
                    raise ValueError('sbom: declare cyclonedx and/or spdx output paths')
                # bazel-bin is a generated symlink outside the checkout. Validate
                # its lexical suffix here, and resolve it only after the build.
                for value in outputs.values():
                    if (not isinstance(value, str) or not value.startswith('bazel-bin/') or
                            any(part in ('..', '') for part in value.split('/')) or not value.endswith('.json')):
                        raise ValueError('SBOM output must be a bazel-bin/*.json path without traversal')
                row['outputs'] = outputs
            else:
                reports = entry.get('reports', [])
                if not isinstance(reports, list) or any(not isinstance(r, str) or not CONFIG.fullmatch(r) for r in reports):
                    raise ValueError('sca.reports: expected report names')
                safety = entry.get('safety-level', '')
                if safety not in ('', 'QM', 'ASIL'):
                    raise ValueError('sca.safety-level: expected QM or ASIL')
                fail = entry.get('fail-on-findings', True)
                if type(fail) is not bool:
                    raise ValueError('sca.fail-on-findings: expected boolean')
                row.update({'bazel-targets': words(entry['bazel-targets'], TARGET, 'sca.bazel-targets'),
                            'reports': reports, 'safety-level': safety, 'fail-on-findings': fail})
            rows.append(row)
    return rows


def execute_additional(workspace, row, destination, report, run):
    """Keep module tools behind Bazel targets and retain their real output files."""
    if row['kind'] == 'sbom':
        run(recipes(row, destination)[0], bazel=True)
        staged = destination / 'sbom'
        staged.mkdir()
        for format_name, value in row['outputs'].items():
            source = workspace / value
            document = json.loads(source.read_text())
            if not isinstance(document, dict):
                raise ValueError(f'Invalid {format_name} SBOM document')
            if format_name == 'cyclonedx':
                valid = document.get('bomFormat') == 'CycloneDX' and document.get('specVersion')
            else:
                valid = str(document.get('spdxVersion', '')).startswith('SPDX-') and document.get('SPDXID')
            if not valid:
                raise ValueError(f'Invalid {format_name} SBOM document')
            shutil.copyfile(source, staged / f'{format_name}.json')
        report['formats'] = sorted(row['outputs'])
    else:
        source = workspace / '_sca'
        # Reports from an earlier invocation cannot satisfy this analysis.
        if source.exists():
            if source.is_symlink():
                raise ValueError('SCA output directory must not be a symlink')
            shutil.rmtree(source)
        try:
            run(recipes(row, destination)[0], bazel=True)
            files = sorted(source.rglob('*.sarif'))
            if not files:
                raise ValueError('SCA did not produce SARIF reports')
            count = sum(len(findings(path)) for path in files)
            report.update({'sarif-files': [p.relative_to(source).as_posix() for p in files],
                           'unsuppressed-findings': count})
            if row['fail-on-findings'] and count:
                raise ValueError(f'SCA reported {count} unsuppressed finding(s)')
        finally:
            if source.is_dir():
                shutil.copytree(source, destination / 'analysis', dirs_exist_ok=True)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('plan', 'run', 'verify'))
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--reports', type=Path, required=True)
    args = parser.parse_args()
    if args.mode == 'verify':
        result = verify(json.loads(os.environ['EXPECTED']), args.reports, os.environ['SOURCE_COMMIT'])
        output('results', json.dumps(result, separators=(',', ':')))
    else:
        rows = load_plan(args.workspace, os.environ['CONFIG_PATH'])
        if args.mode == 'plan':
            output('matrix', json.dumps({'include': rows}, separators=(',', ':')))
            output('expected', json.dumps(rows, separators=(',', ':')))
            output('has-checks', 'true' if rows else 'false')
            output('has-codeql', 'true' if any((r['id'] == 'codeql' and not r['fetch-only']) or r.get('kind') == 'sca' for r in rows) else 'false')
            prefix = 'quality-' + uuid.uuid4().hex
            output('artifact-prefix', prefix)
            output('artifacts', json.dumps({r['id']: {'report': f'{prefix}-report-{r["id"]}',
                   **({'sbom': f'{prefix}-sbom-{r["id"]}'} if r.get('kind') == 'sbom' else {}),
                   **({'analysis': f'{prefix}-analysis-{r["id"]}'} if r.get('kind') == 'sca' else {})}
                   for r in rows}, separators=(',', ':')))
            print(json.dumps(rows, indent=2))
        else:
            row = next(r for r in rows if r['id'] == os.environ['JOB_ID'])
            return execute(args.workspace, row, args.reports)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, StopIteration, yaml.YAMLError) as error:
        print(f'Configured quality: {error}', file=sys.stderr)
        sys.exit(1)
