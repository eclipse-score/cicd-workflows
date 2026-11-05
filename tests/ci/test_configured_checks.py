# Copyright (c) 2026 Contributors to the Eclipse Foundation
# SPDX-License-Identifier: Apache-2.0
"""The POC must prove execution, including failures and empty Bazel selections."""

from copy import deepcopy
import json
import subprocess

import pytest
import yaml

from configured_checks import artifact_names, execute, plan, verify


@pytest.fixture
def workspace(tmp_path):
    """Use an actual checkout so evidence records an observed Git revision."""
    root = tmp_path / 'consumer'
    root.mkdir()
    (root / 'MODULE.bazel').touch()
    (root / 'MODULE.bazel.lock').write_text('{}')
    subprocess.run(['git', 'init', '-q', str(root)], check=True)
    subprocess.run(['git', '-C', str(root), '-c', 'user.name=Test',
                    '-c', 'user.email=test@example.org', 'commit',
                    '--allow-empty', '-qm', 'fixture'], check=True)
    return root


@pytest.fixture
def document():
    # A build and test in the same row is intentional: a failed compiler run
    # must prevent its test invocation and fail completeness verification.
    return {'schema-version': 2, 'variants': [{
        'name': 'native', 'architecture': [{'name': 'x86_64'}],
        'os': [{'name': 'linux'}], 'checks': {'tests': [{
            'test-type': 'unit', 'bazel-configs': 'linux, debug',
            'bazel-build-targets': '//lib:library',
            'bazel-test-targets': '//lib/... -//lib:integration',
        }]},
    }]}


def write_config(workspace, document):
    (workspace / 'ci.yml').write_text(yaml.safe_dump(document))
    return 'ci.yml'


def test_legacy_schema_and_new_schema_produce_same_commands(workspace, document):
    current = plan(workspace, write_config(workspace, document))
    legacy = deepcopy(document)
    legacy['schema-version'] = 1
    variant = legacy['variants'][0]
    variant['quality-gates'] = variant.pop('checks')
    adapted = plan(workspace, write_config(workspace, legacy))
    assert current[0]['commands'] == adapted[0]['commands']
    assert current[0]['id'] == adapted[0]['id']
    assert current[0]['commands'][0] == [
        'bazel', 'build', '--keep_going', '--verbose_failures',
        '--lockfile_mode=error', '--config=linux', '--config=debug',
        '--', '//lib:library',
    ]
    assert current[0]['commands'][1][-3:] == ['--', '//lib/...', '-//lib:integration']


@pytest.mark.parametrize('mutation, message', [
    (lambda d: d.update({'schema-version': True}), 'schema-version'),
    (lambda d: d.update({'commands': ['echo unsafe']}), 'unsupported fields'),
    (lambda d: d['variants'][0]['checks']['tests'][0].pop('bazel-configs'), 'missing fields'),
    (lambda d: d['variants'][0]['checks']['tests'][0].update({'workflow-config': None}), 'mapping'),
    (lambda d: d['variants'][0]['checks']['tests'][0].update({'workflow-config': {'pre-test': 'echo'}}), 'unsupported fields'),
    (lambda d: d['variants'][0]['checks']['tests'][0].update({'workflow-config': {'bazel-test-custom-cli-args': '--remote_executor=elsewhere'}}), 'tag-filter'),
    (lambda d: d['variants'][0]['checks']['tests'][0].update({'test-type': []}), 'supported type'),
    (lambda d: d['variants'][0]['architecture'][0].update({'name': 'aarch64'}), 'native x86_64'),
    (lambda d: d['variants'].append(deepcopy(d['variants'][0])), 'duplicate name'),
    (lambda d: d['variants'][0]['checks']['tests'].append(deepcopy(d['variants'][0]['checks']['tests'][0])), 'duplicate job'),
    (lambda d: d['variants'][0]['checks']['tests'][0].update({'bazel-test-targets': '//...; echo oops'}), 'invalid value'),
])
def test_unsupported_configuration_fails_before_execution(workspace, document, mutation, message):
    mutation(document)
    with pytest.raises(ValueError, match=message):
        plan(workspace, write_config(workspace, document))


def test_cross_build_does_not_implicitly_schedule_tests(workspace, document):
    variant = document['variants'][0]
    variant['architecture'] = [{'name': 'aarch64'}]
    variant['checks'] = {'builds': [{'bazel-configs': 'arm64', 'bazel-build-targets': '//lib:library'}]}
    rows = plan(workspace, write_config(workspace, document))
    assert [command[1] for command in rows[0]['commands']] == ['build']


def test_nested_workspace_is_rejected_until_cache_action_supports_it(workspace, document):
    (workspace / 'nested').mkdir()
    document['variants'][0]['checks']['tests'][0]['workflow-config'] = {'working-directory': 'nested'}
    with pytest.raises(ValueError, match='cache-action support'):
        plan(workspace, write_config(workspace, document))


def test_configuration_must_stay_inside_checkout_even_through_symlinks(workspace, tmp_path):
    (tmp_path / 'external.yml').write_text('schema-version: 2')
    (workspace / 'link.yml').symlink_to(tmp_path / 'external.yml')
    for path in ('../external.yml', 'link.yml', str(tmp_path / 'external.yml')):
        with pytest.raises(ValueError, match='checkout'):
            plan(workspace, path)


def test_duplicate_yaml_keys_and_non_string_keys_are_rejected(workspace):
    for content in ('schema-version: 2\nschema-version: 1\n', 'true: 2\n'):
        (workspace / 'ci.yml').write_text(content)
        with pytest.raises(ValueError):
            plan(workspace, 'ci.yml')


@pytest.fixture
def fake_bazel(tmp_path, monkeypatch):
    """Model the Bazel process boundary, never replace the executor under test."""
    binary = tmp_path / 'bin'
    binary.mkdir()
    executable = binary / 'bazel'
    executable.write_text('''#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys
mode = os.environ.get('FAKE_BAZEL', 'pass')
if mode == 'fail':
    sys.exit(1)
bep = next(a.split('=', 1)[1] for a in sys.argv if a.startswith('--build_event_json_file='))
event = ({'id': {'targetCompleted': {}}, 'completed': {'success': True}}
         if sys.argv[1] == 'build' else {'testSummary': {'overallStatus': 'PASSED'}})
Path(bep).write_text('' if mode == 'empty' else json.dumps(event) + '\\n')
logs = Path('bazel-testlogs/lib/test')
logs.mkdir(parents=True, exist_ok=True)
(logs / 'test.xml').write_text('<testsuite/>')
''')
    executable.chmod(0o755)
    import os
    monkeypatch.setenv('PATH', str(binary) + os.pathsep + os.environ['PATH'])


def run_fixture(workspace, document, tmp_path):
    config = write_config(workspace, document)
    rows = plan(workspace, config)
    reports = tmp_path / 'reports'
    status = execute(workspace, config, rows[0]['id'], reports)
    sha = subprocess.check_output(['git', '-C', str(workspace), 'rev-parse', 'HEAD'], text=True).strip()
    return rows, reports, status, sha


def test_execution_collects_bazel_evidence_and_test_logs(workspace, document, tmp_path, fake_bazel):
    rows, reports, status, sha = run_fixture(workspace, document, tmp_path)
    assert status == 0
    result = verify(rows, reports, sha)
    assert len(result['jobs'][rows[0]['id']]['commands']) == 2
    assert (reports / rows[0]['id'] / 'testlogs/lib/test/test.xml').is_file()


@pytest.mark.parametrize('mode, commands', [('fail', 1), ('empty', 1)])
def test_failure_or_no_evidence_retains_report_and_stops_row(workspace, document, tmp_path, fake_bazel, monkeypatch, mode, commands):
    monkeypatch.setenv('FAKE_BAZEL', mode)
    rows, reports, status, sha = run_fixture(workspace, document, tmp_path)
    assert status == 1
    observed = json.loads((reports / rows[0]['id'] / 'report.json').read_text())
    assert len(observed['commands']) == commands
    with pytest.raises(ValueError, match='skipped or changed'):
        verify(rows, reports, sha)


@pytest.mark.parametrize('field, value', [
    ('commit', 'different'), ('config-sha256', 'different'),
    ('commands', []),
])
def test_verification_rejects_different_checkout_config_or_missing_commands(workspace, document, tmp_path, fake_bazel, field, value):
    rows, reports, _, sha = run_fixture(workspace, document, tmp_path)
    path = reports / rows[0]['id'] / 'report.json'
    report = json.loads(path.read_text())
    report[field] = value
    path.write_text(json.dumps(report))
    with pytest.raises(ValueError):
        verify(rows, reports, sha)


def test_verification_rejects_missing_and_duplicate_reports(workspace, document, tmp_path, fake_bazel):
    rows, reports, _, sha = run_fixture(workspace, document, tmp_path)
    path = reports / rows[0]['id'] / 'report.json'
    duplicate = reports / 'duplicate/report.json'
    duplicate.parent.mkdir()
    duplicate.write_text(path.read_text())
    with pytest.raises(ValueError, match='Duplicate report'):
        verify(rows, reports, sha)
    duplicate.unlink()
    path.unlink()
    with pytest.raises(ValueError, match='Missing or unexpected'):
        verify(rows, reports, sha)


@pytest.mark.parametrize('count', [0, True, '1'])
def test_green_report_requires_positive_integer_bazel_evidence(workspace, document, tmp_path, fake_bazel, count):
    """A successful exit or truthy value alone cannot prove a Bazel invocation."""
    rows, reports, _, sha = run_fixture(workspace, document, tmp_path)
    path = reports / rows[0]['id'] / 'report.json'
    report = json.loads(path.read_text())
    report['commands'][0]['evidence-count'] = count
    path.write_text(json.dumps(report))
    with pytest.raises(ValueError, match='Bazel evidence'):
        verify(rows, reports, sha)


def test_legacy_tag_filters_are_adapted_without_shell_evaluation(workspace, document):
    document['schema-version'] = 1
    variant = document['variants'][0]
    variant['quality-gates'] = variant.pop('checks')
    entry = variant['quality-gates']['tests'][0]
    entry['bazel-test-custom-cli-args'] = '--test_tag_filters=unit,-manual'
    rows = plan(workspace, write_config(workspace, document))
    assert '--test_tag_filters=unit,-manual' in rows[0]['commands'][1]
    entry['workflow-config'] = {'bazel-test-custom-cli-args': '--test_tag_filters=unit'}
    with pytest.raises(ValueError, match='duplicate CLI option'):
        plan(workspace, write_config(workspace, document))


def test_no_reports_cannot_verify_an_empty_plan(tmp_path):
    """A missing workflow output must never turn into vacuous success."""
    with pytest.raises(ValueError, match='nonempty plan'):
        verify([], tmp_path, 'unused')


def test_artifact_manifest_separates_reports_from_docs_test_logs(workspace, document):
    """Docs consume the package tree; aggregation consumes only execution reports."""
    rows = plan(workspace, write_config(workspace, document))
    native = rows[0]
    cross = {**native, 'id': 'cross-build', 'has-tests': False}
    manifest = artifact_names([native, cross], 'invocation')
    assert manifest[native['id']] == {
        'report': f"invocation-report-{native['id']}",
        'testlogs': f"invocation-testlogs-{native['id']}",
    }
    assert manifest['cross-build'] == {
        'report': 'invocation-report-cross-build', 'testlogs': None,
    }
    other = artifact_names(rows, 'another-invocation')
    assert manifest[native['id']]['testlogs'] != other[native['id']]['testlogs']
