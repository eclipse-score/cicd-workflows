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


def test_nested_workspace_uses_its_own_module_and_lockfile(workspace, document):
    nested = workspace / 'examples/integration'
    nested.mkdir(parents=True)
    (nested / 'MODULE.bazel').touch()
    entry = document['variants'][0]['checks']['tests'][0]
    entry['workflow-config'] = {'working-directory': 'examples/integration'}
    with pytest.raises(ValueError, match='lockfile'):
        plan(workspace, write_config(workspace, document))
    (nested / 'MODULE.bazel.lock').write_text('{}')
    rows = plan(workspace, write_config(workspace, document))
    assert rows[0]['working-directory'] == 'examples/integration'
    assert rows[0]['execution'] == 'native'



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
         if sys.argv[1] in ('build', 'run') else {'testSummary': {'overallStatus': 'PASSED'}})
Path(bep).write_text('' if mode == 'empty' else json.dumps(event) + '\\n')
logs = Path('bazel-testlogs/lib/test')
logs.mkdir(parents=True, exist_ok=True)
(logs / 'test.xml').write_text('<testsuite/>')
if os.environ.get('FAKE_COVERAGE') and 'COVERAGE_THRESHOLD' in os.environ:
    assert os.environ['COVERAGE_THRESHOLD'] == '0'
    output = Path('coverage-report')
    output.mkdir(exist_ok=True)
    (output / 'index.html').write_text('<html/>')
    (output / 'coverage.lcov').write_text('SF:lib/file.cc\\nDA:1,1\\nend_of_record\\n')
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


def test_qemu_profile_is_explicit_and_owns_emulation_timeout(workspace, document):
    variant = document['variants'][0]
    variant['architecture'][0]['name'] = 'aarch64'
    entry = variant['checks']['tests'][0]
    entry['workflow-config'] = {'execution': 'qemu-user'}
    rows = plan(workspace, write_config(workspace, document))
    assert rows[0]['execution'] == 'qemu-user'
    assert '--test_timeout=180,900,2700,10800' in rows[0]['commands'][1]
    assert not any('test_timeout' in arg for arg in rows[0]['commands'][0])


@pytest.mark.parametrize('architecture, execution', [('x86_64', 'qemu-user'), ('aarch64', 'native'), ('aarch64', 'custom-script')])
def test_execution_profile_must_match_test_architecture(workspace, document, architecture, execution):
    document['variants'][0]['architecture'][0]['name'] = architecture
    document['variants'][0]['checks']['tests'][0]['workflow-config'] = {'execution': execution}
    with pytest.raises(ValueError):
        plan(workspace, write_config(workspace, document))


def test_nested_execution_and_log_collection_use_the_selected_workspace(workspace, document, tmp_path, fake_bazel):
    """Root and nested workspaces have identical labels but separate test logs."""
    nested = workspace / 'nested'
    nested.mkdir()
    (nested / 'MODULE.bazel').touch()
    (nested / 'MODULE.bazel.lock').write_text('{}')
    document['variants'][0]['checks']['tests'][0]['workflow-config'] = {'working-directory': 'nested'}
    rows, reports, status, sha = run_fixture(workspace, document, tmp_path)
    assert status == 0
    assert (nested / 'bazel-testlogs/lib/test/test.xml').exists()
    assert not (workspace / 'bazel-testlogs').exists()
    report = verify(rows, reports, sha)['jobs'][rows[0]['id']]
    assert report['working-directory'] == 'nested'
    assert (reports / rows[0]['id'] / 'testlogs/lib/test/test.xml').exists()


def test_verification_rejects_evidence_from_the_wrong_workspace(workspace, document, tmp_path, fake_bazel):
    rows, reports, _, sha = run_fixture(workspace, document, tmp_path)
    path = reports / rows[0]['id'] / 'report.json'
    report = json.loads(path.read_text())
    report['working-directory'] = 'different-workspace'
    path.write_text(json.dumps(report))
    with pytest.raises(ValueError, match='workspace differs'):
        verify(rows, reports, sha)


def test_nested_working_directory_cannot_escape_through_a_symlink(workspace, document, tmp_path):
    outside = tmp_path / 'outside'
    outside.mkdir()
    (workspace / 'link').symlink_to(outside, target_is_directory=True)
    document['variants'][0]['checks']['tests'][0]['workflow-config'] = {'working-directory': 'link'}
    with pytest.raises(ValueError, match='escapes checkout'):
        plan(workspace, write_config(workspace, document))


@pytest.fixture
def coverage_document(workspace, document):
    """A report-only LLVM pipeline shares its variant with ordinary tests."""
    (workspace / 'justifications.yml').write_text('{}')
    document['variants'][0]['checks']['llvm-coverage'] = {
        'bazel-configs': 'llvm_cov', 'bazel-targets': '//lib/... -//lib:excluded',
        'justifications-yaml': 'justifications.yml', 'coverage-threshold': 0,
        'generate-artifacts-archive': True,
    }
    document['docs'] = {'bazel-target': '//:docs', 'tests': 'native-tests-unit',
                        'coverage': 'native-llvm-coverage'}
    return document


def test_llvm_and_module_orchestrator_have_distinct_contracts(workspace, coverage_document):
    rows = plan(workspace, write_config(workspace, coverage_document))
    llvm = next(row for row in rows if 'coverage' in row)
    assert [argv[1] for argv in llvm['commands']] == ['coverage', 'run']
    assert llvm['commands'][0][-2:] == ['//lib/...', '-//lib:excluded']
    assert llvm['commands'][1][-2:] == ['--archive-dir', 'coverage-report']
    gates = coverage_document['variants'][0]['checks']
    del gates['llvm-coverage']
    gates['coverage'] = {'bazel-target': '//quality:coverage', 'output-directory': 'reports',
                         'coverage-threshold': 66}
    coverage_document['docs']['coverage'] = 'native-coverage'
    row = next(row for row in plan(workspace, write_config(workspace, coverage_document)) if 'coverage' in row)
    assert row['commands'] == [['bazel', 'run', '--lockfile_mode=error', '//quality:coverage', '--']]
    assert row['coverage']['threshold'] == 66


@pytest.mark.parametrize('mutation, message', [
    ({'coverage-threshold': True}, 'coverage-threshold'),
    ({'coverage-threshold': 101}, 'coverage-threshold'),
    ({'coverage-threshold': float('nan')}, 'coverage-threshold'),
    ({'bazel-configs': None}, 'bazel-configs'),
    ({'generate-artifacts-archive': 'false'}, 'expected boolean'),
    ({'coverage-html-bazel-target': '//...'}, 'one positive'),
    ({'justifications-yaml': '../private.yml'}, 'escapes checkout'),
    ({'workflow-config': {'pre-coverage-script': 'anything'}}, 'unsupported fields'),
    ({'workflow-config': {'retention-days': 0}}, 'retention-days'),
    ({'workflow-config': {'artifact-name': 'bad/name'}}, 'artifact-name'),
])
def test_invalid_coverage_is_rejected_before_execution(workspace, coverage_document, mutation, message):
    coverage_document['variants'][0]['checks']['llvm-coverage'].update(mutation)
    with pytest.raises(ValueError, match=message):
        plan(workspace, write_config(workspace, coverage_document))


@pytest.mark.parametrize('key, value', [('tests', 'missing'), ('tests', 'native-coverage'),
                                        ('coverage', 'native-tests-unit'), ('coverage', []),
                                        ('github-context', 'true')])
def test_docs_references_are_validated_during_preparation(workspace, coverage_document, key, value):
    coverage_document['docs'][key] = value
    with pytest.raises(ValueError, match='docs'):
        plan(workspace, write_config(workspace, coverage_document))


def test_coverage_executor_collects_and_verifies_reports(workspace, coverage_document, tmp_path, fake_bazel, monkeypatch):
    monkeypatch.setenv('FAKE_COVERAGE', '1')
    config = write_config(workspace, coverage_document)
    rows = plan(workspace, config)
    reports = tmp_path / 'reports'
    for row in rows:
        assert execute(workspace, config, row['id'], reports) == 0
    sha = subprocess.check_output(['git', '-C', str(workspace), 'rev-parse', 'HEAD'], text=True).strip()
    verify(rows, reports, sha)
    coverage = next(row for row in rows if 'coverage' in row)
    artifact = reports / coverage['id'] / 'coverage'
    assert (artifact / 'coverage_report.dat').read_text().startswith('SF:')
    assert (artifact / 'index.html').is_file()
    manifest = artifact_names(rows, 'invocation')
    assert manifest[coverage['id']]['coverage'] == 'invocation-coverage-native-llvm-coverage'
    report_path = reports / coverage['id'] / 'report.json'
    report = json.loads(report_path.read_text())
    del report['coverage']
    report_path.write_text(json.dumps(report))
    with pytest.raises(ValueError, match='coverage collection'):
        verify(rows, reports, sha)


def test_green_coverage_commands_without_reports_fail(workspace, coverage_document, tmp_path, fake_bazel):
    config = write_config(workspace, coverage_document)
    row = next(row for row in plan(workspace, config) if 'coverage' in row)
    reports = tmp_path / 'reports'
    assert execute(workspace, config, row['id'], reports) == 1
    report = json.loads((reports / row['id'] / 'report.json').read_text())
    assert 'HTML report' in report['error']


@pytest.mark.parametrize('zipped', [False, True])
def test_coverage_normalizes_plain_and_zip_lcov_for_docs(tmp_path, zipped):
    from configured_coverage import collect_coverage
    import zipfile
    output = tmp_path / 'coverage-report'
    output.mkdir()
    (output / 'index.html').write_text('<html/>')
    raw = tmp_path / 'bazel-out/_coverage/_coverage_report.dat'
    raw.parent.mkdir(parents=True)
    data = b'SF:lib/file.cc\nDA:1,1\nend_of_record\n'
    if zipped:
        with zipfile.ZipFile(raw, 'w') as archive:
            archive.writestr('lcov_report/lcov.dat', data)
    else:
        raw.write_bytes(data)
    collect_coverage(tmp_path, tmp_path / 'staged', {'output-directory': 'coverage-report'})
    assert (tmp_path / 'staged/coverage_report.dat').read_bytes() == data
    assert (tmp_path / 'staged/coverage.lcov').read_bytes() == data


def test_scope_check_requires_complete_production_scope_and_current_exclusions():
    from configured_coverage import scope_difference
    production = {'//score:a', '//score:b'}
    scope_difference(production, {'//score:a'}, {'//score:b'})
    with pytest.raises(ValueError, match='missing=.*score:b'):
        scope_difference(production, {'//score:a'}, set())
    with pytest.raises(ValueError, match='stale exclusions=.*score:b'):
        scope_difference(production, production, {'//score:b'})


def test_docs_output_uses_resolved_artifacts_and_validated_targets(workspace, coverage_document, tmp_path, monkeypatch):
    """The CLI hands Docs concrete artifacts from the same invocation's plan."""
    import sys
    from configured_checks import main
    config = write_config(workspace, coverage_document)
    output = tmp_path / 'outputs'
    monkeypatch.setenv('GITHUB_OUTPUT', str(output))
    monkeypatch.setattr(sys, 'argv', ['configured_checks', 'plan', '--workspace', str(workspace),
                                    '--config', config, '--reports', str(tmp_path / 'reports')])
    assert main() == 0
    values = dict(line.split('=', 1) for line in output.read_text().splitlines())
    docs, artifacts = json.loads(values['docs']), json.loads(values['artifacts'])
    assert docs['tests-artifact'] == artifacts['native-tests-unit']['testlogs']
    assert docs['coverage-artifact'] == artifacts['native-llvm-coverage']['coverage']
    assert docs['bazel-target'] == '//:docs'
    assert 'tests' not in docs and 'coverage' not in docs


def test_scope_query_failure_stops_coverage_and_retains_failure_report(workspace, coverage_document, tmp_path, monkeypatch):
    """A failed completeness query cannot silently permit coverage to proceed."""
    import configured_coverage
    (workspace / 'exclusions.txt').write_text('//lib:excluded\n')
    coverage_document['variants'][0]['checks']['llvm-coverage']['scope-check'] = {
        'scope-target': '//tools:scope', 'universe': '//lib/...', 'exclusions': 'exclusions.txt'}
    calls = []
    def fail_query(argv, **kwargs):
        calls.append(argv)
        raise subprocess.CalledProcessError(1, argv)
    monkeypatch.setattr(configured_coverage.subprocess, 'check_output', fail_query)
    # Observe the commit before mocking the process boundary shared by both modules.
    import configured_checks
    monkeypatch.setattr(configured_checks, 'commit', lambda _: 'frozen-commit')
    config = write_config(workspace, coverage_document)
    row = next(row for row in plan(workspace, config) if 'coverage' in row)
    reports = tmp_path / 'reports'
    assert execute(workspace, config, row['id'], reports) == 1
    report = json.loads((reports / row['id'] / 'report.json').read_text())
    assert report['commands'] == []
    assert report['error']
    assert len(calls) == 1 and calls[0][1] == 'query'


def test_threshold_failure_keeps_reports_but_fails_verification(workspace, coverage_document, tmp_path, fake_bazel, monkeypatch):
    """Report generation may finish before its coverage threshold rejects the row."""
    import os
    from pathlib import Path
    executable = Path(os.environ['PATH'].split(os.pathsep)[0]) / 'bazel'
    executable.write_text(executable.read_text() + "\nif sys.argv[1] == 'run':\n    sys.exit(1)\n")
    monkeypatch.setenv('FAKE_COVERAGE', '1')
    config = write_config(workspace, coverage_document)
    row = next(row for row in plan(workspace, config) if 'coverage' in row)
    reports = tmp_path / 'reports'
    assert execute(workspace, config, row['id'], reports) == 1
    assert (reports / row['id'] / 'coverage/index.html').is_file()
    sha = subprocess.check_output(['git', '-C', str(workspace), 'rev-parse', 'HEAD'], text=True).strip()
    with pytest.raises(ValueError, match='Bazel evidence'):
        verify([row], reports, sha)


def test_release_artifact_template_tracks_selected_repository_including_forks(workspace, coverage_document):
    coverage_document['variants'][0]['checks']['llvm-coverage']['workflow-config'] = {
        'artifact-name': '{repository}_cpp_coverage_report'}
    rows = plan(workspace, write_config(workspace, coverage_document))
    for repo in ('eclipse-score/baselibs', 'contributor/renamed-fork'):
        manifest = artifact_names(rows, 'invocation', repo)
        assert manifest['native-llvm-coverage']['coverage'] == repo.split('/')[1] + '_cpp_coverage_report'
    with pytest.raises(ValueError, match='owner/name'):
        artifact_names(rows, 'invocation')


@pytest.mark.parametrize('report', ['missing', 'invalid', 'wrong-zip-member'])
def test_html_without_usable_lcov_cannot_be_published(tmp_path, report):
    from configured_coverage import collect_coverage
    import zipfile
    source = tmp_path / 'coverage-report'
    source.mkdir()
    (source / 'index.html').write_text('<html/>')
    if report == 'invalid':
        (source / 'coverage_report.dat').write_text('not LCOV')
    elif report == 'wrong-zip-member':
        with zipfile.ZipFile(source / 'coverage_report.dat', 'w') as archive:
            archive.writestr('../escape.dat', 'SF:invalid')
    with pytest.raises(ValueError, match='LCOV|lcov_report'):
        collect_coverage(tmp_path, tmp_path / 'staged', {'output-directory': 'coverage-report'})
    assert not (tmp_path / 'escape.dat').exists()


def test_native_test_defaults_preserve_explicit_plan(workspace, document):
    """Omitting convention fields must retain the same job IDs and argv."""
    entry = document['variants'][0]['checks']['tests'][0]
    entry['bazel-test-targets'] = '//...'
    explicit = plan(workspace, write_config(workspace, document))
    variant = document['variants'][0]
    del variant['os'], variant['architecture']
    del entry['test-type'], entry['bazel-test-targets']
    defaulted = plan(workspace, write_config(workspace, document))
    assert [{k: v for k, v in row.items() if k != 'config-sha256'} for row in explicit] == [
        {k: v for k, v in row.items() if k != 'config-sha256'} for row in defaulted]


def test_coverage_and_docs_defaults_match_explicit_configuration(workspace, coverage_document):
    from configured_coverage import LLVM_DEFAULTS, SCOPE_DEFAULTS, docs_plan
    directory = workspace / 'tools/coverage'
    directory.mkdir(parents=True)
    (directory / 'coverage_justifications.yaml').write_text('{}')
    (directory / 'scope_exclusions.txt').write_text('//lib:excluded\n')
    entry = {**LLVM_DEFAULTS, 'scope-check': dict(SCOPE_DEFAULTS)}
    coverage_document['variants'][0]['checks']['llvm-coverage'] = entry
    explicit = plan(workspace, write_config(workspace, coverage_document))
    docs = docs_plan(workspace, coverage_document['docs'], explicit)
    coverage_document['variants'][0]['checks']['llvm-coverage'] = {'scope-check': {}}
    coverage_document['docs'] = {}
    defaulted = plan(workspace, write_config(workspace, coverage_document))
    assert [{k: v for k, v in row.items() if k != 'config-sha256'} for row in explicit] == [
        {k: v for k, v in row.items() if k != 'config-sha256'} for row in defaulted]
    assert docs_plan(workspace, {}, defaulted) == docs
    assert defaulted[0]['id'] == explicit[0]['id']


@pytest.mark.parametrize('key', ['tests', 'coverage'])
def test_docs_default_selection_rejects_ambiguity_and_can_be_disabled(workspace, coverage_document, key):
    from configured_coverage import docs_plan
    rows = plan(workspace, write_config(workspace, coverage_document))
    selected = next(row for row in rows if ('coverage' in row) == (key == 'coverage'))
    rows.append({**selected, 'id': 'another-source'})
    with pytest.raises(ValueError, match=f'docs.{key}: ambiguous'):
        docs_plan(workspace, {}, rows)
    assert docs_plan(workspace, {key: ''}, rows)[key] == ''
    assert docs_plan(workspace, {key: selected['id']}, rows)[key] == selected['id']


def test_explicit_coverage_values_override_defaults(workspace, coverage_document):
    entry = coverage_document['variants'][0]['checks']['llvm-coverage']
    entry.update({'generate-artifacts-archive': False, 'coverage-threshold': 80,
                  'coverage-html-bazel-target': '//reports:html',
                  'workflow-config': {'retention-days': 2}})
    row = next(row for row in plan(workspace, write_config(workspace, coverage_document)) if 'coverage' in row)
    assert row['coverage']['threshold'] == 80
    assert row['coverage']['retention-days'] == 2
    assert '//reports:html' in row['commands'][1]
    assert '--archive-dir' not in row['commands'][1]
    assert row['commands'][1][-1] == 'coverage-report'


@pytest.mark.parametrize('family', ['tests', 'builds'])
def test_compiler_defaults_still_require_an_explicit_config(workspace, document, family):
    document['variants'][0]['checks'] = {family: [{}]}
    with pytest.raises(ValueError, match='bazel-configs'):
        plan(workspace, write_config(workspace, document))


def test_compact_profiles_preserve_platform_commands_ids_and_docs(workspace, coverage_document):
    """A simpler module document must preserve the executor and artifact contract."""
    from configured_coverage import docs_plan
    old = coverage_document
    variant = old['variants'][0]
    test = variant['checks']['tests'][0]
    compact = {'schema-version': 2,
               'tests': {'native': deepcopy(test)},
               'llvm-coverage': deepcopy(variant['checks']['llvm-coverage']),
               'docs': {}}
    old_rows = plan(workspace, write_config(workspace, old))
    new_rows = plan(workspace, write_config(workspace, compact))
    by_id = lambda rows: {row['id']: {key: value for key, value in row.items() if key != 'config-sha256'} for row in rows}
    assert by_id(old_rows) == by_id(new_rows)
    assert docs_plan(workspace, old['docs'], old_rows) == docs_plan(workspace, {}, new_rows)


def test_compact_nested_and_arm_profiles_keep_workspace_and_emulation(workspace):
    nested = workspace / 'nested'
    nested.mkdir()
    (nested / 'MODULE.bazel').touch()
    (nested / 'MODULE.bazel.lock').write_text('{}')
    compact = {'schema-version': 2, 'tests': {
        'arm': {'bazel-configs': 'cross', 'architecture': 'aarch64', 'execution': 'qemu-user'},
        'integration': {'bazel-configs': 'native', 'working-directory': 'nested', 'test-type': 'integration'},
    }}
    rows = plan(workspace, write_config(workspace, compact))
    arm = next(row for row in rows if row['id'] == 'arm-tests-unit')
    nested_row = next(row for row in rows if row['id'] == 'integration-tests-integration')
    assert arm['execution'] == 'qemu-user'
    assert '--test_timeout=180,900,2700,10800' in arm['commands'][0]
    assert nested_row['working-directory'] == 'nested'
    assert all(command[1] == 'test' for row in rows for command in row['commands'])


@pytest.mark.parametrize('compact', [
    {'schema-version': 2, 'tests': {}, 'variants': []},
    {'schema-version': 2, 'tests': []},
    {'schema-version': 1, 'tests': {'native': {'bazel-configs': 'linux'}}},
    {'schema-version': 2, 'tests': {'native': {'bazel-configs': 'linux', 'workflow-config': {}}}},
    {'schema-version': 2, 'tests': {'native': {'bazel-configs': 'linux', 'execution': 'shell'}}},
    {'schema-version': 2, 'tests': {'native': {'bazel-configs': 'linux', 'os': 'windows'}}},
    {'schema-version': 2, 'tests': {'native': {'bazel-configs': 'linux'}},
     'builds': {'native': {'bazel-configs': 'linux'}}},
    {'schema-version': 2, 'coverage': {'bazel-target': '//:coverage', 'workflow-config': {}}},
])
def test_compact_layout_rejects_mixed_ambiguous_and_invalid_profiles(workspace, compact):
    with pytest.raises(ValueError):
        plan(workspace, write_config(workspace, compact))


def test_compact_module_coverage_without_tests_still_requires_reports(workspace):
    compact = {'schema-version': 2, 'coverage': {'bazel-target': '//quality:coverage'}, 'docs': {}}
    row = plan(workspace, write_config(workspace, compact))[0]
    assert row['commands'] == [['bazel', 'run', '--lockfile_mode=error', '//quality:coverage', '--']]
    assert row['coverage']['output-directory'] == 'coverage_artifacts'
    assert not row['has-tests']


def test_llvm_report_can_select_build_configs_without_reusing_coverage_only_configs(workspace, coverage_document):
    gate = coverage_document['variants'][0]['checks']['llvm-coverage']
    before = next(row for row in plan(workspace, write_config(workspace, coverage_document)) if 'coverage' in row)
    assert '--config=llvm_cov' not in before['commands'][1]
    gate['coverage-html-bazel-configs'] = 'native,report-tools'
    after = next(row for row in plan(workspace, write_config(workspace, coverage_document)) if 'coverage' in row)
    assert after['commands'][0] == before['commands'][0]
    assert '--config=native' in after['commands'][1]
    assert '--config=report-tools' in after['commands'][1]
    gate['coverage-html-bazel-configs'] = '--remote_executor=elsewhere'
    with pytest.raises(ValueError, match='coverage-html-bazel-configs'):
        plan(workspace, write_config(workspace, coverage_document))
