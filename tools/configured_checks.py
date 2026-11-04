# Copyright (c) 2026 Contributors to the Eclipse Foundation
# SPDX-License-Identifier: Apache-2.0
"""Plan and execute the deliberately small configured Bazel POC.

The workflow owns setup, cache policy, diagnostic flags and evidence collection.
Consumers supply only the platform, named Bazel configs and target selection.
Unsupported legacy options fail during planning rather than becoming shell hooks
or silently disappearing from the resulting build/test jobs.
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
import uuid

import yaml


NAME = re.compile(r'[a-z0-9][a-z0-9_-]{0,63}')
CONFIG = re.compile(r'[A-Za-z0-9_][A-Za-z0-9_.+-]*')
TARGET = re.compile(r'-?(?:@[A-Za-z0-9_.+-]+)?//[^\s\x00-\x1f;`$|&<>]+')
TYPES = {'unit', 'component', 'integration', 'sanitizer'}
DEFAULT_FLAGS = ['--keep_going', '--verbose_failures', '--lockfile_mode=error']


class Loader(yaml.SafeLoader):
    """Reject duplicate keys before a hidden override can change the test plan."""


def unique_mapping(loader, node):
    result = {}
    for key, value in node.value:
        key = loader.construct_object(key)
        if not isinstance(key, str):
            raise ValueError('YAML keys must be strings')
        if key in result:
            raise ValueError(f'Duplicate YAML key: {key}')
        result[key] = loader.construct_object(value)
    return result


Loader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping)


def fields(value, allowed, required, path):
    """Report unsupported fields at their YAML location, including legacy hooks."""
    if not isinstance(value, dict):
        raise ValueError(f'{path}: expected a mapping')
    if missing := set(required) - value.keys():
        raise ValueError(f'{path}: missing fields {sorted(missing)}')
    if unknown := value.keys() - set(allowed):
        raise ValueError(f'{path}: unsupported fields {sorted(unknown)}')


def inside(workspace, value):
    """Resolve workspace paths without accepting absolute paths or escaping links."""
    if not isinstance(value, str) or not value.strip() or Path(value).is_absolute():
        raise ValueError('Expected a nonempty checkout-relative path')
    path = (workspace / value).resolve()
    if not path.is_relative_to(workspace):
        raise ValueError(f'Path escapes checkout: {value}')
    return path


def words(value, pattern, path):
    """Convert declarative labels to argv; values are never evaluated by a shell."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{path}: expected a nonempty string')
    result = shlex.split(value)
    if not result or any(not pattern.fullmatch(word) for word in result):
        raise ValueError(f'{path}: invalid value')
    return result


def plan(workspace, config):
    """Translate schema 1's supported subset and schema 2 into identical job rows."""
    workspace = Path(workspace).resolve()
    source = inside(workspace, config)
    content = source.read_text(encoding='utf-8')
    document = yaml.load(content, Loader=Loader)
    fields(document, {'schema-version', 'variants'}, {'schema-version', 'variants'}, '$')
    version = document['schema-version']
    if type(version) is not int or version not in (1, 2):
        raise ValueError('schema-version: expected 1 or 2')
    variants = document['variants']
    if not isinstance(variants, list) or not variants:
        raise ValueError('variants: expected a nonempty list')

    # The hash records the actual local/checked-out configuration, including
    # uncommitted POC edits. The commit alone cannot identify a local trial.
    digest = hashlib.sha256(content.encode()).hexdigest()
    rows, variant_names = [], set()
    gate_key = 'quality-gates' if version == 1 else 'checks'

    # Keep dimensions explicit rather than producing a Cartesian product. One
    # variant describes one platform; each entry describes one independent row.
    for index, variant in enumerate(variants):
        path = f'variants[{index}]'
        fields(variant, {'name', 'architecture', 'os', gate_key},
               {'name', 'architecture', 'os', gate_key}, path)
        name = variant['name']
        if not isinstance(name, str) or not NAME.fullmatch(name) or name in variant_names:
            raise ValueError(f'{path}.name: invalid or duplicate name')
        variant_names.add(name)
        platform = {}
        for dimension in ('architecture', 'os'):
            value = variant[dimension]
            if not isinstance(value, list) or len(value) != 1:
                raise ValueError(f'{path}.{dimension}: expected exactly one named value')
            fields(value[0], {'name'}, {'name'}, f'{path}.{dimension}')
            platform[dimension] = value[0]['name']
        if platform['os'] != 'linux' or platform['architecture'] not in ('x86_64', 'aarch64'):
            raise ValueError(f'{path}: POC supports Linux x86_64 and aarch64 builds only')

        families = variant[gate_key]
        fields(families, {'tests'} if version == 1 else {'builds', 'tests'}, set(), f'{path}.{gate_key}')
        if not families:
            raise ValueError(f'{path}.{gate_key}: no jobs configured')
        for family, entries in families.items():
            if not isinstance(entries, list) or not entries:
                raise ValueError(f'{path}.{family}: expected a nonempty list')
            for offset, entry in enumerate(entries):
                where = f'{path}.{gate_key}.{family}[{offset}]'
                allowed = {'bazel-configs', 'bazel-build-targets', 'workflow-config'}
                required = {'bazel-configs'}
                if version == 2:
                    allowed.add('name')
                if family == 'tests':
                    allowed |= {'test-type', 'bazel-test-targets'}
                    required |= {'test-type', 'bazel-test-targets'}
                else:
                    required.add('bazel-build-targets')
                if version == 1:
                    allowed.add('bazel-test-custom-cli-args')
                fields(entry, allowed, required, where)
                options = entry.get('workflow-config', {})
                fields(options, {'working-directory', 'bazel-test-custom-cli-args'} if version == 2
                       else {'bazel-test-custom-cli-args'}, set(), f'{where}.workflow-config')
                options = dict(options)
                # Legacy schemas permit this option directly on the gate too.
                # Never resolve a duplicate by silently choosing one location.
                if 'bazel-test-custom-cli-args' in entry:
                    if 'bazel-test-custom-cli-args' in options:
                        raise ValueError(f'{where}: duplicate CLI option')
                    options['bazel-test-custom-cli-args'] = entry['bazel-test-custom-cli-args']

                directory = inside(workspace, options.get('working-directory', '.'))
                if not directory.is_dir():
                    raise ValueError(f'{where}: working directory does not exist')
                # The current public setup-bazel-cache action discovers its
                # workspace from GITHUB_WORKSPACE and has no directory input.
                # Reject nested workspaces until that contract is supported;
                # do not fake a different GITHUB_WORKSPACE or cache the wrong lock.
                if directory != workspace:
                    raise ValueError(f'{where}: nested workspace needs cache-action support; POC is root-only')
                if not (directory / 'MODULE.bazel').is_file() or not (directory / 'MODULE.bazel.lock').is_file():
                    raise ValueError(f'{where}: configured builds require MODULE.bazel and its lockfile')

                # A missing config cannot fall back to a bare //... build.
                # Preserve config order because later Bazel configs may refine
                # toolchain or sanitizer settings selected by earlier configs.
                if not isinstance(entry['bazel-configs'], str):
                    raise ValueError(f'{where}.bazel-configs: expected config names')
                configs = [value.strip() for value in entry['bazel-configs'].split(',')]
                if not all(CONFIG.fullmatch(value) for value in configs):
                    raise ValueError(f'{where}.bazel-configs: expected nonempty config names')
                kind = entry.get('test-type', 'build')
                if family == 'tests' and (not isinstance(kind, str) or kind not in TYPES or platform['architecture'] != 'x86_64'):
                    raise ValueError(f'{where}: tests need a supported type and a native x86_64 runner')
                job_name = entry.get('name', kind)
                if not isinstance(job_name, str) or not NAME.fullmatch(job_name):
                    raise ValueError(f'{where}.name: invalid job name')
                identity = f'{name}-{family}-{job_name}'
                if any(row['id'] == identity for row in rows):
                    raise ValueError(f'{where}: duplicate job identity; supply explicit names')

                # A row keeps build and test in the same runner/cache context.
                # Build-only rows deliberately have no synthetic test command.
                commands = []
                flags = [*DEFAULT_FLAGS, *[f'--config={value}' for value in configs]]
                if 'bazel-build-targets' in entry:
                    targets = words(entry['bazel-build-targets'], TARGET, f'{where}.bazel-build-targets')
                    commands.append(['bazel', 'build', *flags, '--', *targets])
                if family == 'tests':
                    raw = options.get('bazel-test-custom-cli-args', '')
                    if not isinstance(raw, str):
                        raise ValueError(f'{where}: test CLI arguments must be a string')
                    extra = shlex.split(raw)
                    if any(not re.fullmatch(r'--(?:test|build)_tag_filters=[A-Za-z0-9_,+.-]+', value) for value in extra):
                        raise ValueError(f'{where}: POC supports tag-filter arguments only')
                    targets = words(entry['bazel-test-targets'], TARGET, f'{where}.bazel-test-targets')
                    commands.append(['bazel', 'test', *flags, '--test_output=errors', *extra, '--', *targets])
                elif options.get('bazel-test-custom-cli-args'):
                    raise ValueError(f'{where}: test arguments on a build-only job')
                rows.append({'id': identity, 'config-sha256': digest,
                             'working-directory': '.', 'commands': commands})
    if not 1 <= len(rows) <= 256:
        raise ValueError('Expected between 1 and 256 configured jobs')
    return rows


def commit(workspace):
    """Observe the checkout instead of echoing a requested branch or SHA."""
    return subprocess.check_output(['git', '-C', str(workspace), 'rev-parse', 'HEAD'], text=True).strip()


def evidence(path, command):
    """A green Bazel exit must include actual targets, or actual test summaries.

    Cached passes are valid evidence. All-skipped/incompatible target sets and
    a wrapper that returns zero without invoking Bazel are not successful jobs.
    """
    completed, passed = 0, 0
    for line in path.read_text().splitlines():
        event = json.loads(line)
        if 'targetCompleted' in event.get('id', {}) and event.get('completed', {}).get('success'):
            completed += 1
        if event.get('testSummary', {}).get('overallStatus') in ('PASSED', 'FLAKY'):
            passed += 1
    count = passed if command == 'test' else completed
    if not count:
        raise ValueError(f'{command}: no successful target/test evidence')
    return count


def execute(workspace, config, job_id, reports):
    """Replan the checkout, execute one selected row, and always retain its report."""
    workspace = Path(workspace).resolve()
    rows = plan(workspace, config)
    selected = [row for row in rows if row['id'] == job_id]
    if len(selected) != 1:
        raise ValueError(f'Unknown job: {job_id}')
    row = selected[0]
    destination = Path(reports).resolve() / job_id
    destination.mkdir(parents=True, exist_ok=True)
    report = {'commit': commit(workspace), 'id': job_id, 'config-sha256': row['config-sha256'], 'commands': []}
    success = True
    try:
        for number, command in enumerate(row['commands']):
            observed = {'argv': command, 'outcome': 'failure', 'evidence-count': 0}
            report['commands'].append(observed)
            bep = destination / f'{number}.bep.jsonl'
            # Pass the BEP flag before the target separator, so it remains an
            # option even for target exclusions. No command is run through sh.
            separator = command.index('--')
            argv = [*command[:separator], f'--build_event_json_file={bep}', *command[separator:]]
            print(shlex.join(argv), flush=True)
            observed['exit-code'] = subprocess.run(argv, cwd=workspace, check=False).returncode
            if observed['exit-code']:
                success = False
                break
            observed['evidence-count'] = evidence(bep, command[1])
            observed['outcome'] = 'success'
    except (OSError, ValueError) as error:
        report['error'] = str(error)
        success = False
    finally:
        # Keep the report outside the consumer checkout. Uploading it on failure
        # lets the parent verify missing/skipped commands rather than trusting a
        # matrix's last reusable-workflow output.
        (destination / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
        logs = workspace / 'bazel-testlogs'
        if logs.is_dir():
            for source in logs.rglob('*'):
                if source.is_file() and source.name in ('test.xml', 'test.log'):
                    target = destination / 'testlogs' / source.relative_to(logs)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, target)
    return 0 if success else 1


def verify(expected, reports, source_commit):
    """Require one matching report and every planned command for each matrix row."""
    # An empty or duplicated expectation cannot establish complete execution,
    # even if the artifact download also happens to contain no reports.
    if (not isinstance(expected, list) or not expected or
            len({row['id'] for row in expected}) != len(expected)):
        raise ValueError('Expected a nonempty plan with unique job identities')

    # Matrix jobs can fail before the executor starts (checkout/setup errors).
    # Requiring the exact row set also makes those absent reports fail the gate.
    actual = {}
    for path in Path(reports).rglob('report.json'):
        report = json.loads(path.read_text())
        identity = report['id']
        if identity in actual:
            raise ValueError(f'Duplicate report: {identity}')
        actual[identity] = report
    if set(actual) != {row['id'] for row in expected}:
        raise ValueError('Missing or unexpected configured-job reports')
    for row in expected:
        report = actual[row['id']]
        if report['commit'] != source_commit or report['config-sha256'] != row['config-sha256']:
            raise ValueError(f'{row["id"]}: checkout or config differs from preparation')
        observations = report['commands']
        if [entry['argv'] for entry in observations] != row['commands']:
            raise ValueError(f'{row["id"]}: planned commands were skipped or changed')
        if any(entry['outcome'] != 'success' or entry.get('exit-code') != 0 or
               type(entry.get('evidence-count')) is not int or entry['evidence-count'] < 1
               for entry in observations):
            raise ValueError(f'{row["id"]}: commands did not succeed with Bazel evidence')
    return {'commit': source_commit, 'jobs': actual}


def output(name, value):
    """Write compact JSON outputs without multiline workflow-command injection."""
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a') as stream:
            stream.write(f'{name}={value}\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('plan', 'run', 'verify'))
    parser.add_argument('--workspace', type=Path, default=Path.cwd())
    parser.add_argument('--config', default=os.environ.get('CONFIG_PATH'))
    parser.add_argument('--job', default=os.environ.get('JOB_ID'))
    parser.add_argument('--reports', type=Path, required=True)
    args = parser.parse_args()
    if args.mode == 'plan':
        rows = plan(args.workspace, args.config)
        output('matrix', json.dumps({'include': rows}, separators=(',', ':')))
        output('expected', json.dumps(rows, separators=(',', ':')))
        output('artifact-prefix', f'configured-{uuid.uuid4().hex}')
        print(json.dumps(rows, indent=2))
        return 0
    if args.mode == 'run':
        return execute(args.workspace, args.config, args.job, args.reports)
    result = verify(json.loads(os.environ['EXPECTED']), args.reports, os.environ['SOURCE_COMMIT'])
    output('results', json.dumps(result, separators=(',', ':')))
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, yaml.YAMLError) as error:
        print(f'Configured checks: {error}', file=sys.stderr)
        sys.exit(1)
