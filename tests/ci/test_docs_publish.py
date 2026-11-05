# Copyright (c) 2026 Contributors to the Eclipse Foundation
# SPDX-License-Identifier: Apache-2.0
"""Exercise the publisher's real shell with controlled GitHub API responses."""

import json
import os
from pathlib import Path
import subprocess

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = yaml.safe_load((ROOT / '.github/workflows/docs-publish.yml').read_text())
JOB = WORKFLOW['jobs']['docs-deploy']
STEPS = {step.get('id'): step for step in JOB['steps'] if 'id' in step}
SHA = 'a' * 40


@pytest.fixture
def select_build(tmp_path):
    """Keep all network requests local while running the production jq filters."""
    executable = tmp_path / 'gh'
    executable.write_text('''#!/usr/bin/env python3
import json, os, sys
with open(os.environ['CALLS'], 'a') as stream:
    stream.write(json.dumps(sys.argv[1:]) + '\\n')
print(os.environ['RUNS' if sys.argv[1:3] == ['run', 'list'] else 'DETAILS'])
''')
    executable.chmod(0o755)

    def run(*, requested='42', details=None, runs=None, **overrides):
        output, calls = tmp_path / 'outputs', tmp_path / 'calls'
        output.write_text('')
        calls.write_text('')
        environment = {
            **os.environ, 'PATH': str(tmp_path) + os.pathsep + os.environ['PATH'],
            'GITHUB_OUTPUT': str(output), 'CALLS': str(calls),
            'REPOSITORY': 'example/module', 'SOURCE_WORKFLOW': 'on-pr.yml',
            'REQUESTED_RUN_ID': requested, 'EXPECTED_SHA': SHA, 'EXPECTED_REF': 'main',
            'CALLER_EVENT': 'workflow_dispatch',
            'DETAILS': json.dumps({
                'status': 'completed', 'conclusion': 'success', 'head_sha': SHA,
                'head_branch': 'main', 'path': '.github/workflows/on-pr.yml',
                'head_repository': {'full_name': 'example/module'}, 'event': 'push',
                **(details or {}),
            }),
            'RUNS': json.dumps(runs if runs is not None else [
                {'databaseId': 42, 'event': 'push', 'headBranch': 'main'},
            ]), **overrides,
        }
        result = subprocess.run(['bash', '-euo', 'pipefail', '-c', STEPS['source']['run']],
                                env=environment, text=True, capture_output=True)
        return result, output.read_text(), [json.loads(line) for line in calls.read_text().splitlines()]
    return run


@pytest.mark.parametrize('requested', ['', '42'])
@pytest.mark.parametrize('event', ['push', 'workflow_dispatch'])
def test_manual_publication_downloads_successful_build_of_selected_revision(select_build, requested, event):
    result, output, calls = select_build(requested=requested, details={'event': event})
    assert result.returncode == 0, result.stderr
    assert output == 'run-id=42\n'
    assert calls[-1] == ['api', 'repos/example/module/actions/runs/42']
    if not requested:
        assert '--commit' in calls[0] and SHA in calls[0]
        assert '--workflow' in calls[0] and 'on-pr.yml' in calls[0]


@pytest.mark.parametrize('requested', ['', '42'])
@pytest.mark.parametrize('changed', [
    {'conclusion': 'failure'}, {'status': 'in_progress'}, {'head_sha': 'b' * 40},
    {'head_branch': 'other'}, {'path': '.github/workflows/other.yml'},
    {'head_repository': {'full_name': 'elsewhere/fork'}},
    {'event': 'pull_request'}, {'event': 'pull_request_target'}, {'event': 'merge_group'},
])
def test_explicit_and_discovered_runs_receive_identical_validation(select_build, requested, changed):
    result, output, _ = select_build(requested=requested, details=changed)
    assert result.returncode != 0
    assert output == ''


def test_discovery_skips_pr_runs_and_other_refs_and_preserves_quoted_branch_names(select_build):
    branch = 'feature/"docs'
    result, output, calls = select_build(requested='', EXPECTED_REF=branch,
        details={'head_branch': branch}, runs=[
            {'databaseId': 1, 'event': 'pull_request', 'headBranch': branch},
            {'databaseId': 2, 'event': 'push', 'headBranch': 'other'},
            {'databaseId': 42, 'event': 'push', 'headBranch': branch},
        ])
    assert result.returncode == 0, result.stderr
    assert output == 'run-id=42\n'
    assert calls[-1][-1].endswith('/42')


@pytest.mark.parametrize('requested', ['', 'not-a-number', '../42', '42; touch injected'])
def test_missing_or_invalid_run_ids_fail_before_artifact_selection(select_build, requested):
    result, output, calls = select_build(requested=requested, runs=[])
    assert result.returncode != 0
    assert output == ''
    assert not any(call[0] == 'api' for call in calls)


@pytest.mark.parametrize('overrides', [
    {'SOURCE_WORKFLOW': ''}, {'SOURCE_WORKFLOW': '../on-pr.yml'},
    {'CALLER_EVENT': 'pull_request'}, {'CALLER_EVENT': 'workflow_run'},
])
def test_opt_in_selection_requires_manual_event_and_workflow_filename(select_build, overrides):
    result, output, calls = select_build(**overrides)
    assert result.returncode != 0
    assert output == '' and calls == []


@pytest.mark.parametrize('event, pr, ref, deploy', [
    ('push', '', 'main', True), ('workflow_dispatch', '', 'main', True),
    ('pull_request', '123', 'feature', True), ('release', '', 'v1.2.3', True),
    ('merge_group', '', 'queue', False),
])
def test_existing_source_contexts_keep_their_publishing_metadata(tmp_path, event, pr, ref, deploy):
    output = tmp_path / 'outputs'
    result = subprocess.run(['bash', '-euo', 'pipefail', '-c', STEPS['metadata']['run']],
        env={**os.environ, 'GITHUB_OUTPUT': str(output), 'EVENT_NAME': event,
             'PR_NUMBER': pr, 'REF_NAME': ref, 'HEAD_SHA': SHA,
             'HEAD_REPO': 'example/module', 'REPO': 'example/module'},
        capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    values = dict(line.split('=', 1) for line in output.read_text().splitlines())
    assert values['should_deploy'] == str(deploy).lower()
    assert values['ref_name'] == ref and values['pr_number'] == pr


@pytest.mark.parametrize('event, owner, workflow, expected', [
    ('workflow_run', 'eclipse-score', '', True),
    ('workflow_dispatch', 'eclipse-score', 'on-pr.yml', True),
    ('workflow_dispatch', 'eclipse-score', '', False),
    ('pull_request', 'eclipse-score', '', False),
    ('push', 'example', '', True),
])
def test_job_keeps_existing_permission_boundary_and_allows_verified_manual_builds(event, owner, workflow, expected):
    expression = JOB['if']
    for key, value in {
        'github.event.workflow_run.conclusion': 'success', 'github.event_name': event,
        'github.repository_owner': owner, 'inputs.source-workflow': workflow,
        'inputs.source-run-id': '',
    }.items():
        expression = expression.replace(key, repr(value))
    assert eval(expression.replace('&&', 'and').replace('||', 'or'), {'__builtins__': {}}) is expected


def test_download_uses_selected_build_directly_with_existing_run_fallbacks():
    download = next(step for step in JOB['steps'] if step.get('name') == 'Download documentation artifact')
    assert download['with']['run-id'] == '${{ steps.source.outputs.run-id || github.event.workflow_run.id || github.run_id }}'
    assert download['with']['name'] == 'github-pages'
    # The publisher still uploads the assembled versioned site for deployment,
    # but never uploads another copy of the incoming build artifact.
    assert not any('actions/upload-artifact@' in step.get('uses', '') for step in JOB['steps'])
