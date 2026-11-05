# Copyright (c) 2026 Contributors to the Eclipse Foundation
# SPDX-License-Identifier: Apache-2.0
"""Configured PR checks preserve shared platform, batch and report contracts."""

from copy import deepcopy
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest
import yaml

import configured_checks as checks
import configured_quality as quality
from configured_coverage import docs_plan
from configured_profiles import test_arguments as parse_test_arguments
from gcov_coverage import generate
from test_reports import merge
from test_configured_checks import workspace as workspace, write_config


def test_batches_inherit_policy_but_select_their_own_targets(workspace):
    document = {'schema-version': 2, 'tests': {'target': {
        'os': 'qnx', 'bazel-configs': 'target', 'test-type': 'component',
        'bazel-build-targets': '//:all_target_images',
        'bazel-test-custom-cli-args': '--test_timeout=3600 --nocache_test_results --test_arg=-x',
        'batches': [{'batch-name': 'one', 'bazel-test-targets': '//a:tests'},
                    {'batch-name': 'two', 'bazel-test-targets': '//b:tests', 'bazel-build-targets': '//b:image'}],
    }}}
    before = deepcopy(document)
    rows = checks.plan(workspace, write_config(workspace, document))
    assert document == before
    assert [r['id'] for r in rows] == ['target-tests-component-one', 'target-tests-component-two']
    assert [c[1] for c in rows[0]['commands']] == ['test']
    assert [c[1] for c in rows[1]['commands']] == ['build', 'test']
    assert all(r['execution'] == 'qnx-vm' for r in rows)
    assert all('--test_timeout=3600' in r['commands'][-1] for r in rows)
    assert all('--nocache_test_results' in r['commands'][-1] for r in rows)
    assert all('//:all_target_images' not in c for r in rows for c in r['commands'])


@pytest.mark.parametrize('batches', [[], [{'batch-name': 'a', 'bazel-test-targets': '//a:tests'}] * 2,
                                    [{'batch-name': 'a'}], [{'batch-name': '../a', 'bazel-test-targets': '//...'}]])
def test_invalid_batches_fail_before_execution(workspace, batches):
    document = {'schema-version': 2, 'tests': {'native': {'bazel-configs': 'native', 'batches': batches}}}
    with pytest.raises(ValueError, match='batch|missing fields'):
        checks.plan(workspace, write_config(workspace, document))


def test_qnx_builds_need_sdk_without_requesting_vm_tests(workspace):
    document = {'schema-version': 2, 'builds': {'cross': {
        'os': 'qnx', 'architecture': 'aarch64', 'bazel-configs': 'cross'}}}
    row = checks.plan(workspace, write_config(workspace, document))[0]
    assert row['execution'] == 'native'
    assert row['platform'] == {'os': 'qnx', 'architecture': 'aarch64'}
    assert row['commands'][0][1] == 'build'


@pytest.mark.parametrize('value', ['--runs_per_test=3', '--cache_test_results=no --build_tests_only',
                                   '--test_output=all --test_timeout=3600 --test_arg=-x --flaky_test_attempts=2',
                                   "--test_arg='a; echo literal'"])
def test_test_controls_remain_literal_argv(value):
    assert parse_test_arguments(value, 'tests')


@pytest.mark.parametrize('value', ['--remote_executor=elsewhere', '--build_event_json_file=/tmp/fake',
                                   '--test_timeout=0', '--runs_per_test=0', '--test_arg=x\necho surprise'])
def test_controls_cannot_replace_evidence_or_execution(value):
    with pytest.raises(ValueError):
        parse_test_arguments(value, 'tests')


def test_precheck_is_a_bazel_target_and_failure_prevents_tests(workspace, monkeypatch, tmp_path):
    document = {'schema-version': 2, 'tests': {'native': {
        'bazel-configs': 'native', 'bazel-pre-test-targets': '//checks:inventory'}}}
    config = write_config(workspace, document)
    row = checks.plan(workspace, config)[0]
    assert [c[1] for c in row['commands']] == ['run', 'test']
    assert row['commands'][0][-2:] == ['//checks:inventory', '--']
    monkeypatch.setattr(checks, 'commit', lambda path: 'source')
    monkeypatch.setattr(checks.subprocess, 'run', lambda *a, **kw: SimpleNamespace(returncode=1))
    assert checks.execute(workspace, config, row['id'], tmp_path / 'reports') == 1
    report = json.loads((tmp_path / 'reports' / row['id'] / 'report.json').read_text())
    assert len(report['commands']) == 1
    with pytest.raises(ValueError, match='commands were skipped'):
        checks.verify([row], tmp_path / 'reports', 'source')


def test_gcov_coverage_uses_qnx_setup_and_real_test_evidence(workspace):
    document = {'schema-version': 2, 'variants': [{'name': 'target', 'os': [{'name': 'qnx'}],
        'checks': {'gcov-coverage': {'bazel-configs': 'qcc', 'bazel-targets': '//lib/...', 'coverage-threshold': 80}}}]}
    row = checks.plan(workspace, write_config(workspace, document))[0]
    assert row['execution'] == 'qnx-vm' and row['has-tests']
    assert row['prerequisites'] == 'gcov-coverage'
    assert row['commands'][0][1] == 'coverage'
    assert '--combined_report=lcov' in row['commands'][0]


@pytest.mark.parametrize('threshold, succeeds', [(50, True), (75, False)])
def test_gcov_threshold_is_enforced_and_reports_retained(workspace, monkeypatch, threshold, succeeds):
    output = workspace / 'bazel-out/_coverage'
    output.mkdir(parents=True)
    (output / '_coverage_report.dat').write_text('SF:lib/a.cc\nLF:2\nLH:1\nDA:1,1\nDA:2,0\nend_of_record\n')
    def genhtml(argv, **kwargs):
        assert argv[0] == 'genhtml' and '--branch-coverage' in argv
        target = Path(argv[argv.index('--output-directory') + 1])
        (target / 'index.html').write_text('coverage')
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(subprocess, 'run', genhtml)
    if succeeds:
        generate(workspace, workspace / 'coverage', threshold)
    else:
        with pytest.raises(ValueError, match='below'):
            generate(workspace, workspace / 'coverage', threshold)
    assert (workspace / 'coverage/index.html').is_file()
    assert (workspace / 'coverage/coverage_report.dat').is_file()


@pytest.fixture
def analysis_document():
    return {'schema-version': 2, 'sca': {'native': {
        'bazel-target': '//analysis:run', 'bazel-configs': 'native', 'bazel-targets': '//lib/...',
        'reports': ['cert'], 'safety-level': 'QM'}}, 'sbom': {'native': {
        'bazel-target': '//:sbom', 'bazel-configs': 'native', 'cyclonedx': 'bazel-bin/sbom.cdx.json',
        'spdx': 'bazel-bin/sbom.spdx.json'}}}


def test_analysis_and_sbom_can_run_without_synthetic_test_rows(workspace, analysis_document):
    assert checks.plan(workspace, write_config(workspace, analysis_document)) == []
    rows = quality.load_plan(workspace, 'ci.yml')
    assert {r['id'] for r in rows} == {'sca-native', 'sbom-native'}
    command = quality.recipes(rows[0], Path('/reports'))[0]
    assert command == ['bazel', 'run', '--lockfile_mode=error', '//analysis:run', '--',
                       '--config=native', '--platform=linux', '--safety-level=QM', '--report=cert', '//lib/...']


@pytest.fixture
def analysis_runner(workspace, monkeypatch):
    state = {'fail': False, 'results': [], 'valid-sbom': True, 'sarif': True}
    monkeypatch.setattr(quality, 'commit', lambda path: 'source')
    def run(argv, **kwargs):
        flag = next(a for a in argv if a.startswith('--build_event_json_file='))
        Path(flag.split('=', 1)[1]).write_text(json.dumps({
            'id': {'targetCompleted': {'label': '//:tool'}}, 'completed': {'success': True}}))
        if argv[1] == 'build':
            output = workspace / 'bazel-bin'
            output.mkdir(exist_ok=True)
            (output / 'sbom.cdx.json').write_text(json.dumps({'bomFormat': 'CycloneDX', 'specVersion': '1.6'}
                                                          if state['valid-sbom'] else {}))
            (output / 'sbom.spdx.json').write_text(json.dumps({'spdxVersion': 'SPDX-2.3', 'SPDXID': 'SPDXRef-DOCUMENT'}))
        else:
            output = workspace / '_sca'
            output.mkdir()
            (output / 'checks.csv').write_text('analysis diagnostics')
            if state['sarif']:
                (output / 'findings.sarif').write_text(json.dumps({'version': '2.1.0', 'runs': [{
                    'tool': {'driver': {'name': 'Analysis'}}, 'results': state['results']}]}))
        return SimpleNamespace(returncode=1 if state['fail'] else 0, stdout=None)
    monkeypatch.setattr(quality.subprocess, 'run', run)
    return state


@pytest.mark.parametrize('identity', ['sca-native', 'sbom-native'])
def test_ported_analysis_and_sbom_require_valid_output_and_evidence(workspace, analysis_document, analysis_runner, tmp_path, identity):
    rows = quality.load_plan(workspace, write_config(workspace, analysis_document))
    row = next(r for r in rows if r['id'] == identity)
    assert quality.execute(workspace, row, tmp_path) == 0
    assert quality.verify([row], tmp_path, 'source')


@pytest.mark.parametrize('case', ['findings', 'no-sarif', 'tool-failure', 'bad-sbom'])
def test_bad_analysis_cannot_pass_the_gate(workspace, analysis_document, analysis_runner, tmp_path, case):
    identity = 'sca-native'
    if case == 'findings':
        analysis_runner['results'] = [{'ruleId': 'example', 'message': {'text': 'finding'}}]
    elif case == 'no-sarif':
        analysis_runner['sarif'] = False
    elif case == 'tool-failure':
        analysis_runner['fail'] = True
    else:
        identity = 'sbom-native'
        analysis_runner['valid-sbom'] = False
    row = next(r for r in quality.load_plan(workspace, write_config(workspace, analysis_document)) if r['id'] == identity)
    assert quality.execute(workspace, row, tmp_path) == 1
    with pytest.raises(ValueError, match='did not succeed'):
        quality.verify([row], tmp_path, 'source')
    if identity == 'sca-native':
        assert (tmp_path / identity / 'analysis/checks.csv').is_file()


@pytest.mark.parametrize('path', ['../private.json', '/tmp/sbom.json', 'bazel-bin/../private.json'])
def test_sbom_cannot_read_arbitrary_checkout_files(workspace, analysis_document, path):
    analysis_document['sbom']['native']['cyclonedx'] = path
    with pytest.raises(ValueError, match='SBOM output'):
        checks.plan(workspace, write_config(workspace, analysis_document))


def test_docs_can_select_multiple_batched_reports(workspace):
    document = {'schema-version': 2, 'tests': {'target': {'os': 'qnx', 'bazel-configs': 'target',
        'batches': [{'batch-name': 'one', 'bazel-test-targets': '//a:tests'},
                    {'batch-name': 'two', 'bazel-test-targets': '//b:tests'}]}}}
    rows = checks.plan(workspace, write_config(workspace, document))
    ids = [r['id'] for r in rows]
    assert docs_plan(workspace, {'tests': ids}, rows)['tests'] == ids


def test_docs_merges_packages_and_rejects_conflicting_variants(tmp_path):
    roots = [tmp_path / 'a', tmp_path / 'b']
    for root in roots:
        (root / 'lib/test').mkdir(parents=True)
        (root / 'lib/test/test.xml').write_text('<testsuite/>')
    assert merge([('a', roots[0]), ('b', roots[1])], tmp_path / 'merged') == 1
    (roots[1] / 'lib/test/test.xml').write_text('<testsuite failures="1"/>')
    with pytest.raises(ValueError, match='Conflicting'):
        merge([('a', roots[0]), ('b', roots[1])], tmp_path / 'merged')


def test_empty_and_escaping_doc_reports_fail(tmp_path):
    root = tmp_path / 'artifact'
    root.mkdir()
    with pytest.raises(ValueError, match='no test.xml'):
        merge([('empty', root)], tmp_path / 'merged')
    outside = tmp_path / 'test.xml'
    outside.write_text('<testsuite/>')
    (root / 'test.xml').symlink_to(outside)
    with pytest.raises(ValueError, match='escaping'):
        merge([('escape', root)], tmp_path / 'merged')


def workflow(name):
    return yaml.load(Path(f'.github/workflows/{name}.yml').read_text(), Loader=yaml.BaseLoader)


def test_on_pr_owns_execution_and_forwards_credentials_and_evidence():
    entry = workflow('on-pr')
    jobs = entry['jobs']
    assert {'prepare-configured', 'configured', 'verify-configured',
            'configured-quality', 'configured-docs'}.issubset(jobs)
    assert 'quality-gates' not in jobs
    assert not Path('.github/workflows/quality-gates.yml').exists()
    setup = next(step for step in jobs['configured']['steps']
                 if 'build-environment' in step.get('uses', ''))
    for name in ('token', 'github-app-private-key', 'artifact-server-user', 'artifact-server-token', 'qnx-license'):
        assert name in setup['with']
        assert name in jobs['configured-quality']['secrets']
    outputs = entry['on']['workflow_call']['outputs']
    assert outputs['configured-results']['value'] == '${{ jobs.verify-configured.outputs.results }}'
    assert outputs['quality-results']['value'] == '${{ jobs.configured-quality.outputs.results }}'
    assert outputs['configured-artifacts']['value'] == '${{ jobs.prepare-configured.outputs.artifacts }}'
    assert 'workflow-config-yaml' in jobs['prepare-configured']['if']
    assert 'workflow-config-yaml' in jobs['verify-configured']['if']
    assert 'pull_request_target' in jobs['prepare-configured']['if']
    public = '\n'.join(Path('.github/workflows/' + name + '.yml').read_text()
                       for name in ('on-pr', 'configured-quality', 'docs'))
    assert 'etas-eng/' not in public and 'vsps_' not in public
    assert 'quality_pack' not in public


def test_build_setup_is_selected_by_profile_and_public_credentials_are_optional():
    action = yaml.load(Path('.github/actions/build-environment/action.yml').read_text(), Loader=yaml.BaseLoader)
    assert all(value.get('required', 'false') != 'true' for value in action['inputs'].values())
    setup = next(step for step in action['runs']['steps'] if step.get('id') == 'prerequisites')['run']
    assert "execution == 'qnx-vm'" in setup and "execution == 'qemu-user'" in setup
    assert "profile == 'gcov-coverage'" in setup
    assert "parsed.scheme != 'https'" in setup


def test_docs_merges_only_selected_downloaded_artifacts(tmp_path, monkeypatch):
    """Use runtime artifact downloads without requiring Actions API read access."""
    import test_reports
    downloads = tmp_path / 'downloads'
    for name in ('first', 'second', 'unselected'):
        path = downloads / name / name
        path.mkdir(parents=True)
        (path / 'test.xml').write_text(f'<testsuite name="{name}"/>')
    destination = tmp_path / 'merged'
    monkeypatch.setenv('TEST_REPORT_DOWNLOADS', str(downloads))
    monkeypatch.setenv('TEST_REPORT_ARTIFACTS', '["first", "second"]')
    monkeypatch.setattr('sys.argv', ['test_reports', 'merge', '--workspace', str(tmp_path), '--reports', str(destination)])
    test_reports.main()
    assert sorted(p.parent.name for p in destination.rglob('test.xml')) == ['first', 'second']


@pytest.mark.parametrize('names', ['[["nested"]]', '["../escape"]', '["*"]', '["first", "first"]'])
def test_docs_rejects_invalid_download_names(tmp_path, monkeypatch, names):
    import test_reports
    monkeypatch.setenv('TEST_REPORT_ARTIFACTS', names)
    monkeypatch.setattr('sys.argv', ['test_reports', 'merge', '--workspace', str(tmp_path), '--reports', str(tmp_path / 'out')])
    with pytest.raises(ValueError, match='artifact names'):
        test_reports.main()


def test_docs_rejects_empty_row_in_explicit_list(workspace):
    with pytest.raises(ValueError, match='nonempty row IDs'):
        docs_plan(workspace, {'tests': ['']}, [])
