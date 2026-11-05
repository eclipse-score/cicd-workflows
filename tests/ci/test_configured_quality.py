# Copyright (c) 2026 Contributors to the Eclipse Foundation
# SPDX-License-Identifier: Apache-2.0
"""Module checks retain their policy and cannot pass through empty execution."""

import hashlib
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

import configured_quality as quality
from configured_checks import plan as bazel_plan
from configured_coverage import docs_plan
from test_configured_checks import workspace as workspace, write_config


@pytest.fixture
def document(workspace):
    (workspace / 'Cargo.toml').write_text('[package]\nname="fixture"\nversion="0.1.0"\n')
    (workspace / 'Cargo.lock').touch()
    (workspace / 'rust-toolchain.toml').write_text('[toolchain]\nchannel="1.90.0"\n')
    return {'schema-version': 2, 'tests': {
        'native': {'bazel-configs': 'linux'},
        'asan': {'test-type': 'sanitizer', 'bazel-configs': 'linux,asan'},
        'tsan': {'test-type': 'sanitizer', 'bazel-configs': 'linux,tsan'},
    }, 'coverage': {'bazel-target': '//quality:coverage'},
        'clippy': {}, 'gitlint': True, 'codeql': {'bazel-configs': 'linux'},
        'docs': {'check': True, 'link-check': True}}


def selected(workspace, document, identity, **options):
    content = yaml.safe_dump(document)
    return next(row for row in quality.plan(workspace, document, hashlib.sha256(content.encode()).hexdigest(),
                                           **options) if row['id'] == identity)


def test_sanitizers_keep_test_only_scope_and_docs_uses_regular_tests(workspace, document):
    rows = bazel_plan(workspace, write_config(workspace, document))
    sanitizers = [row for row in rows if row.get('test-type') == 'sanitizer']
    assert len(sanitizers) == 2
    assert all([c[1] for c in row['commands']] == ['test'] for row in sanitizers)
    assert [row['id'] for row in rows if 'coverage' in row] == ['native-coverage']
    docs = docs_plan(workspace, document['docs'], rows)
    assert docs['tests'] == 'native-tests-unit'
    assert docs['retention-days'] == 5
    assert 'github-context' not in docs


@pytest.mark.parametrize('key, value, message', [
    ('clippy', {'command': 'true'}, 'unsupported fields'),
    ('gitlint', {}, 'boolean'),
    ('codeql', {'bazel-configs': 'linux', 'reports': []}, 'report names'),
    ('codeql', {'bazel-configs': 'linux', 'fail-on-findings': 'false'}, 'boolean'),
    ('codeql', {'bazel-configs': 'linux', 'target-universe': '//...; true'}, 'invalid value'),
    ('docs', {'check': 'true'}, 'boolean'),
])
def test_invalid_quality_policy_fails_main_planner(workspace, document, key, value, message):
    document[key] = value
    with pytest.raises(ValueError, match=message):
        bazel_plan(workspace, write_config(workspace, document))


@pytest.mark.parametrize('missing', ['Cargo.lock', 'rust-toolchain.toml'])
def test_clippy_requires_module_toolchain_and_lock(workspace, document, missing):
    (workspace / missing).unlink()
    with pytest.raises(ValueError, match=missing):
        bazel_plan(workspace, write_config(workspace, document))


def test_clippy_rejects_floating_toolchain(workspace, document):
    (workspace / 'rust-toolchain.toml').write_text('[toolchain]\nchannel="stable"\n')
    with pytest.raises(ValueError, match='pinned Rust'):
        bazel_plan(workspace, write_config(workspace, document))


def test_nightly_override_and_fetch_mode_are_explicit_in_plan(workspace, document):
    rows = quality.plan(workspace, document, 'digest', only_codeql=True, fetch_only=True, fail_override='false')
    assert len(rows) == 1
    row = rows[0]
    assert row['fail-on-findings'] is False and row['fetch-only'] is True
    assert quality.recipes(row, Path('/reports')) == [
        ['bazel', 'build', '--lockfile_mode=error', '--config=linux', '--nobuild', '//...']]


def sarif(results):
    return {'version': '2.1.0', 'runs': [{'tool': {'driver': {'name': 'CodeQL'}}, 'results': results}]}


@pytest.fixture
def runner(monkeypatch, workspace):
    """Model external tool evidence while retaining real planner/report paths."""
    state = {'bep': True, 'query': '//score:library\n', 'results': [], 'exit': 0, 'cargo': True}
    monkeypatch.setattr(quality, 'commit', lambda path: 'frozen-sha')

    def run(argv, **kwargs):
        stdout = ''
        for arg in argv:
            if arg.startswith('--build_event_json_file=') and state['bep']:
                Path(arg.split('=', 1)[1]).write_text(json.dumps({
                    'id': {'targetCompleted': {'label': '//:target'}}, 'completed': {'success': True}}) + '\n')
        if argv[:2] == ['bazel', 'query']:
            stdout = state['query']
        if '--output-file' in argv:
            Path(argv[argv.index('--output-file') + 1]).write_text(json.dumps(sarif(state['results'])))
        if argv[0] == 'cargo':
            entries = [{'reason': 'build-finished', 'success': True}]
            if state['cargo']:
                entries.append({'reason': 'compiler-artifact', 'manifest_path': str(workspace / 'Cargo.toml')})
            stdout = '\n'.join(map(json.dumps, entries)) + '\n'
        return SimpleNamespace(returncode=state['exit'], stdout=stdout)

    monkeypatch.setattr(quality.subprocess, 'run', run)
    return state


@pytest.mark.parametrize('identity', ['docs-check', 'docs-link-check', 'codeql', 'clippy'])
def test_execution_proves_all_planned_commands(workspace, document, runner, tmp_path, identity):
    row = selected(workspace, document, identity)
    reports = tmp_path / 'reports'
    assert quality.execute(workspace, row, reports) == 0
    verified = quality.verify([row], reports, 'frozen-sha')
    assert verified['jobs'][identity]['outcome'] == 'success'


@pytest.mark.parametrize('identity', ['docs-check', 'codeql'])
def test_green_bazel_without_target_evidence_fails(workspace, document, runner, tmp_path, identity):
    runner['bep'] = False
    row = selected(workspace, document, identity)
    assert quality.execute(workspace, row, tmp_path / 'reports') == 1
    with pytest.raises(ValueError, match='did not succeed'):
        quality.verify([row], tmp_path / 'reports', 'frozen-sha')


def test_codeql_query_must_select_targets(workspace, document, runner, tmp_path):
    runner['query'] = ''
    row = selected(workspace, document, 'codeql')
    assert quality.execute(workspace, row, tmp_path) == 1
    report = json.loads((tmp_path / 'codeql/report.json').read_text())
    assert len(report['commands']) == 1
    assert 'no valid production targets' in report['error']


def test_clippy_green_without_workspace_compilation_fails(workspace, document, runner, tmp_path):
    runner['cargo'] = False
    row = selected(workspace, document, 'clippy')
    assert quality.execute(workspace, row, tmp_path) == 1
    assert (tmp_path / 'clippy/cargo.jsonl').is_file()


@pytest.mark.parametrize('suppressed, gate, expected', [(False, True, 1), (True, True, 0), (False, False, 0)])
def test_codeql_preserves_suppression_and_failure_policy(workspace, document, runner, tmp_path, suppressed, gate, expected):
    runner['results'] = [{'ruleId': 'example', 'message': {'text': 'finding'},
                          **({'suppressions': [{'kind': 'inSource'}]} if suppressed else {})}]
    document['codeql']['fail-on-findings'] = gate
    row = selected(workspace, document, 'codeql')
    assert quality.execute(workspace, row, tmp_path) == expected
    # Finding failures retain the merged SARIF for artifacts and publication.
    assert (tmp_path / 'codeql/publish/codeql-nightly.sarif').is_file()


@pytest.mark.parametrize('mutation, message', [
    (lambda r: r.update(commit='different'), 'checkout or config'),
    (lambda r: r['plan'].update({'config-sha256': 'different'}), 'checkout or config'),
    (lambda r: r['commands'].pop(), 'commands were skipped'),
    (lambda r: r['commands'][1]['argv'].append('--some-other-rule'), 'commands were skipped'),
    (lambda r: r['commands'][1].pop('evidence-count'), 'Missing Bazel'),
    (lambda r: r.pop('unsuppressed-findings'), 'SARIF evidence'),
])
def test_quality_verification_rejects_incomplete_or_stale_reports(workspace, document, runner, tmp_path, mutation, message):
    row = selected(workspace, document, 'codeql')
    assert quality.execute(workspace, row, tmp_path) == 0
    path = tmp_path / 'codeql/report.json'
    report = json.loads(path.read_text())
    mutation(report)
    path.write_text(json.dumps(report))
    with pytest.raises(ValueError, match=message):
        quality.verify([row], tmp_path, 'frozen-sha')


def test_quality_verification_rejects_missing_and_duplicate_reports(workspace, document, runner, tmp_path):
    row = selected(workspace, document, 'docs-check')
    with pytest.raises(ValueError, match='Missing'):
        quality.verify([row], tmp_path, 'frozen-sha')
    assert quality.execute(workspace, row, tmp_path) == 0
    duplicate = tmp_path / 'duplicate'
    duplicate.mkdir()
    (duplicate / 'report.json').write_bytes((tmp_path / 'docs-check/report.json').read_bytes())
    with pytest.raises(ValueError, match='Duplicate'):
        quality.verify([row], tmp_path, 'frozen-sha')


def test_gitlint_is_applicable_only_to_pull_requests(workspace, document, runner, monkeypatch, tmp_path):
    row = selected(workspace, document, 'gitlint')
    monkeypatch.setenv('GITHUB_EVENT_NAME', 'merge_group')
    assert quality.execute(workspace, row, tmp_path) == 0
    assert quality.verify([row], tmp_path, 'frozen-sha')
    monkeypatch.setenv('GITHUB_EVENT_NAME', 'pull_request')
    with pytest.raises(ValueError, match='did not succeed'):
        quality.verify([row], tmp_path, 'frozen-sha')


def test_fetch_only_never_requires_or_claims_sarif(workspace, document, runner, tmp_path):
    row = selected(workspace, document, 'codeql', fetch_only=True)
    assert quality.execute(workspace, row, tmp_path) == 0
    assert quality.verify([row], tmp_path, 'frozen-sha')
    assert not (tmp_path / 'codeql/publish').exists()


def test_empty_or_malformed_sarif_cannot_claim_analysis(tmp_path):
    path = tmp_path / 'report.sarif'
    for content in ({}, {'version': '2.1.0', 'runs': []}, {'version': '2.1.0', 'runs': [{}]}):
        path.write_text(json.dumps(content))
        with pytest.raises(ValueError, match='SARIF'):
            quality.findings(path)


def test_docs_context_uses_selected_repository_environment():
    workflow = Path('.github/workflows/docs.yml').read_text()
    assert 'GITHUB_REPOSITORY=os.environ[\'DOCS_REPOSITORY\']' in workflow
    assert '--github_user' not in workflow and '--github_repo' not in workflow


def test_quality_is_required_before_documentation_and_lifecycle_gate():
    workflow = yaml.load(Path('.github/workflows/on-pr.yml').read_text(), Loader=yaml.BaseLoader)
    assert 'configured-quality' in workflow['jobs']['configured-docs']['needs']
    quality_workflow = yaml.load(Path('.github/workflows/configured-quality.yml').read_text(), Loader=yaml.BaseLoader)
    assert quality_workflow['jobs']['checks']['permissions'] == {'contents': 'read'}
    assert quality_workflow['jobs']['verify']['needs'] == ['prepare', 'checks']
    assert 'permissions' not in quality_workflow['jobs']['publish-sarif']


@pytest.mark.parametrize('valid', [True, False])
def test_real_gitlint_checks_frozen_pr_head_without_synthetic_merge(workspace, document, monkeypatch, tmp_path, valid):
    """A merge checkout must lint PR commits only, against the actual base SHA."""
    base = quality.commit(workspace)
    message = 'Check the shared policy' if valid else 'x' * 73
    subprocess.run(['git', '-C', str(workspace), '-c', 'user.name=Test',
                    '-c', 'user.email=test@example.org', 'commit', '--allow-empty', '-qm', message], check=True)
    head = quality.commit(workspace)
    policy = tmp_path / '.gitlint'
    policy.write_text('[general]\nignore=T3,T5,B1,B5,B6,B7\n[title-max-length]\nline-length=72\n')
    monkeypatch.setenv('GITHUB_EVENT_NAME', 'pull_request')
    monkeypatch.setenv('PR_BASE_SHA', base)
    monkeypatch.setenv('PR_HEAD_SHA', head)
    monkeypatch.setenv('GITLINT_POLICY', str(policy))
    row = selected(workspace, document, 'gitlint')
    assert quality.execute(workspace, row, tmp_path / 'reports') == (0 if valid else 1)
    if valid:
        verified = quality.verify([row], tmp_path / 'reports', head)
        assert verified['jobs']['gitlint']['commits'] == [head]


@pytest.mark.parametrize('identity', ['clippy', 'docs-check', 'codeql'])
def test_tool_failures_fail_the_quality_gate(workspace, document, runner, tmp_path, identity):
    runner['exit'] = 2
    row = selected(workspace, document, identity)
    assert quality.execute(workspace, row, tmp_path) == 1
    with pytest.raises(ValueError, match='did not succeed'):
        quality.verify([row], tmp_path, 'frozen-sha')


def test_quality_cli_plan_accepts_relative_workspace_path(workspace, document, monkeypatch):
    config = write_config(workspace, document)
    monkeypatch.setenv('ONLY_CODEQL', 'false')
    monkeypatch.setenv('FETCH_ONLY', 'false')
    monkeypatch.setenv('FAIL_ON_FINDINGS', '')
    rows = quality.load_plan(Path(os.path.relpath(workspace)), config)
    assert {r['id'] for r in rows} == {'clippy', 'docs-check', 'docs-link-check', 'gitlint', 'codeql'}


def test_gitlint_rejects_pr_event_from_different_repository(workspace, document, monkeypatch):
    monkeypatch.setenv('GITHUB_EVENT_NAME', 'pull_request')
    monkeypatch.setenv('GITHUB_REPOSITORY', 'owner/caller')
    monkeypatch.setenv('SOURCE_REPOSITORY', 'owner/selected')
    with pytest.raises(ValueError, match='another repository'):
        quality.plan(workspace, document, 'digest')
