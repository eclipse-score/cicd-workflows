# Copyright (c) 2026 Contributors to the Eclipse Foundation
# SPDX-License-Identifier: Apache-2.0
"""Check bounded Pages retries and documentation URL resolution locally."""

import os
from pathlib import Path
import subprocess

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = yaml.safe_load((ROOT / '.github/workflows/docs-publish.yml').read_text())
STEPS = WORKFLOW['jobs']['docs-deploy']['steps']
RESOLVE = next(step for step in STEPS if step.get('id') == 'documentation-url')
DEPLOYMENT_STEPS = STEPS[
    next(index for index, step in enumerate(STEPS) if step.get('id') == 'deploy-pages'):
    STEPS.index(RESOLVE)
]


def evaluate(expression, values, *, status='success', condition=False):
    """Evaluate the YAML expressions, including Actions' implicit success() gate."""
    expression = expression.removeprefix('${{').removesuffix('}}').strip()
    status_functions = {
        'success()': status == 'success',
        'failure()': status == 'failure',
        'cancelled()': status == 'cancelled',
        'always()': True,
    }
    if condition and not any(function in expression for function in status_functions):
        expression = f'success() && ({expression})'
    for key, value in {**values, **status_functions}.items():
        expression = expression.replace(key, repr(value))
    expression = expression.replace('&&', ' and ').replace('||', ' or ')
    return eval('(' + expression + ')', {'__builtins__': {}}, {'format': str.format})


def deployment_trace(outcomes, *, cancel_after=None, should_deploy='true',
                     deployment_type='workflow', status='success'):
    """Inject action outcomes without uploading artifacts or deploying Pages."""
    values = {
        'steps.metadata.outputs.should_deploy': should_deploy,
        'inputs.deployment_type': deployment_type,
        'github.repository_owner': 'eclipse-score',
        'github.event.repository.name': 'module',
    }
    for step in DEPLOYMENT_STEPS:
        if 'id' in step:
            for field in ('outcome', 'conclusion'):
                values[f"steps.{step['id']}.{field}"] = 'skipped'
            values[f"steps.{step['id']}.outputs.page_url"] = ''
    outcomes = iter(outcomes)
    trace = []
    for step in DEPLOYMENT_STEPS:
        if not evaluate(step['if'], values, status=status, condition=True):
            continue
        if 'uses' in step:
            identifier = step['id']
            trace.append(identifier)
            outcome = next(outcomes)
            conclusion = 'success' if outcome == 'failure' and step.get('continue-on-error') else outcome
            values[f'steps.{identifier}.outcome'] = outcome
            values[f'steps.{identifier}.conclusion'] = conclusion
            # A failed attempt's URL must not override a successful attempt's URL.
            values[f'steps.{identifier}.outputs.page_url'] = f'https://{identifier}.example/'
            if conclusion != 'success':
                status = conclusion
        else:
            trace.append(step['run'].strip())
        if trace[-1] == cancel_after:
            status = 'cancelled'
    return trace, status, values


@pytest.mark.parametrize('outcomes, expected, status', [
    (('success',), ['deploy-pages'], 'success'),
    (('failure', 'success'), ['deploy-pages', 'sleep 30', 'deploy-pages-retry-1'], 'success'),
    (('failure', 'failure', 'success'), [
        'deploy-pages', 'sleep 30', 'deploy-pages-retry-1', 'sleep 60', 'deploy-pages-retry-2',
    ], 'success'),
    (('failure', 'failure', 'failure'), [
        'deploy-pages', 'sleep 30', 'deploy-pages-retry-1', 'sleep 60', 'deploy-pages-retry-2',
    ], 'failure'),
    (('cancelled',), ['deploy-pages'], 'cancelled'),
    (('failure', 'cancelled'), ['deploy-pages', 'sleep 30', 'deploy-pages-retry-1'], 'cancelled'),
])
def test_deployment_retry_paths(outcomes, expected, status):
    trace, actual_status, values = deployment_trace(outcomes)
    assert trace == expected
    assert actual_status == status
    assert evaluate(RESOLVE['if'], values, status=status, condition=True) == (status == 'success')


@pytest.mark.parametrize('wait, expected', [
    ('sleep 30', ['deploy-pages', 'sleep 30']),
    ('sleep 60', ['deploy-pages', 'sleep 30', 'deploy-pages-retry-1', 'sleep 60']),
])
def test_cancellation_during_backoff_prevents_further_deployment(wait, expected):
    trace, status, values = deployment_trace(('failure', 'failure'), cancel_after=wait)
    assert trace == expected
    assert status == 'cancelled'
    assert not evaluate(RESOLVE['if'], values, status=status, condition=True)


@pytest.mark.parametrize('should_deploy, deployment_type, status', [
    ('false', 'workflow', 'success'),
    ('true', 'legacy', 'success'),
    ('true', 'workflow', 'cancelled'),
    ('true', 'workflow', 'failure'),
])
def test_all_attempts_and_waits_preserve_execution_guards(should_deploy, deployment_type, status):
    trace, actual_status, values = deployment_trace(
        (), should_deploy=should_deploy, deployment_type=deployment_type, status=status,
    )
    assert trace == []
    assert actual_status == status
    assert evaluate(RESOLVE['if'], values, status=status, condition=True) == (
        should_deploy == 'true' and status == 'success'
    )
    for key in values:
        if key.endswith('.outcome'):
            values[key] = 'failure'
    for step in DEPLOYMENT_STEPS:
        assert not evaluate(step['if'], values, status=status, condition=True), step['name']


@pytest.mark.parametrize('outcomes, fallback, expected_base', [
    (('success',), False, 'https://deploy-pages.example/'),
    (('failure', 'success'), False, 'https://deploy-pages-retry-1.example/'),
    (('failure', 'failure', 'success'), False, 'https://deploy-pages-retry-2.example/'),
    (('success',), True, 'https://eclipse-score.github.io/module/'),
    ((), True, 'https://eclipse-score.github.io/module/'),
])
def test_documentation_url_uses_successful_attempt_or_legacy_fallback(tmp_path, outcomes, fallback, expected_base):
    _, _, values = deployment_trace(outcomes, deployment_type='workflow' if outcomes else 'legacy')
    if fallback:
        for key in values:
            if key.endswith('.outputs.page_url'):
                values[key] = ''
    base = evaluate(RESOLVE['env']['PAGES_BASE_URL'], values)
    assert base == expected_base
    output = tmp_path / 'output'
    result = subprocess.run(['bash', '-e', '-c', RESOLVE['run']], capture_output=True, text=True, env={
        **os.environ, 'PAGES_BASE_URL': base, 'TARGET_FOLDER': 'pr-42', 'GITHUB_OUTPUT': str(output),
    })
    assert result.returncode == 0, result.stderr
    assert output.read_text() == f'url={expected_base}pr-42/\n'
