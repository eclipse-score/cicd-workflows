# Copyright (c) 2026 Contributors to the Eclipse Foundation
# SPDX-License-Identifier: Apache-2.0
"""Check inline workflow preparation, report selection and the action boundary."""

import json
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest
import yaml


WORKFLOW = Path(__file__).resolve().parents[2] / '.github/workflows/bazel.yml'
JOBS = yaml.safe_load(WORKFLOW.read_text())['jobs']
STEPS = JOBS['bazel']['steps']
EXECUTE = WORKFLOW.parents[1] / 'actions/_bazel-execute/execute.py'
DEFAULTS = dict(BUILD_TARGETS='', TEST_TARGETS='', BAZEL_ARGS='', BUILD_ARGS='', TEST_ARGS='',
                EVENT_NAME='pull_request', EVENT_SHA='a' * 40, REPOSITORY='score/module',
                PR_HEAD_SHA='b' * 40, PR_HEAD_REPOSITORY='contributor/module',
                FORK_PR_LABEL='', APPROVAL_RESULT='skipped', APPROVAL_ENVIRONMENT='workflow-approval',
                WORKING_DIRECTORY='.', ARTIFACT_NAME='', BAZEL_CONFIGS='',
                TEST_COMMAND='test', COVERAGE_JUSTIFICATIONS='', COVERAGE_THRESHOLD='100',
                SETUP_QNX_SDP='false', QNX_CREDENTIALS_PRESENT='false', QNX_BAZELRC='')


def run_step(name, root, *, job='bazel', **values):
    """Run one inline workflow step with local paths replacing GitHub's runner environment."""
    # GitHub provides a separate output file for each step. Remove earlier step
    # outputs so failed steps cannot accidentally reuse a previous decision.
    (root / 'output').unlink(missing_ok=True)
    environment = {**os.environ, **DEFAULTS, 'GITHUB_WORKSPACE': str(root),
                   'GITHUB_OUTPUT': str(root / 'output'),
                   'COVERAGE_REPORT_DIRECTORY': str(root / 'coverage-report'), **values}
    steps = JOBS[job]['steps']
    step = next(step for step in steps if step.get('name') == name)
    command = [sys.executable, '-c', step['run']]
    return subprocess.run(command, cwd=root, env=environment,
                          text=True, capture_output=True)


@pytest.fixture
def bazel(tmp_path):
    """Record the composite wrapper's command and directory without a Bazel installation."""
    executable = tmp_path / 'bazel'
    executable.write_text('#!' + sys.executable + '\n' +
                          'import json, os, sys\n'
                          'with open(os.environ["COMMAND_LOG"], "a") as output:\n'
                          '    output.write(json.dumps([os.getcwd(), sys.argv[1:]]) + "\\n")\n')
    executable.chmod(0o755)
    return {'PATH': str(tmp_path) + os.pathsep + os.environ['PATH'],
            'COMMAND_LOG': str(tmp_path / 'commands')}


def test_internal_action_launches_its_script_in_the_selected_workspace(tmp_path, bazel):
    """The helper comes from the action revision while Bazel runs in the module workspace."""
    step = next(step for step in STEPS if step.get('name') == 'Execute Bazel commands')
    assert step['uses'] == '$/.github/actions/_bazel-execute'
    assert step['with']['working-directory'] == '${{ inputs.working-directory }}'
    action = yaml.safe_load(EXECUTE.with_name('action.yml').read_text())
    launch = action['runs']['steps'][0]
    assert launch['working-directory'] == '${{ inputs.working-directory }}'
    assert launch['env']['ACTION_DIRECTORY'] == '${{ github.action_path }}'
    workspace = tmp_path / 'examples/integration'
    workspace.mkdir(parents=True)
    action_directory = tmp_path / 'downloaded action'
    action_directory.mkdir()
    (action_directory / 'execute.py').symlink_to(EXECUTE)
    environment = {**os.environ, **DEFAULTS, **bazel, 'TEST_TARGETS': '//...',
                   'ACTION_DIRECTORY': str(action_directory)}
    result = subprocess.run([launch['shell'], '-c', launch['run']], cwd=workspace,
                            env=environment, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert [json.loads(line) for line in (tmp_path / 'commands').read_text().splitlines()] == [
        [str(workspace), ['test', '--lockfile_mode=error', '--', '//...']],
    ]


@pytest.mark.parametrize('action', ['setup-bazel-cache', 'unblock-user-namespace-for-linux-sandbox'])
def test_bazel_setup_uses_the_selected_workspace(action):
    """Cache and sandbox setup must use the module workspace's Bazel version."""
    step = next(step for step in STEPS
                if step.get('uses', '').startswith(f'eclipse-score/cicd-actions/{action}@'))
    assert step['with']['working-directory'] == '${{ inputs.working-directory }}'


@pytest.mark.parametrize('values', [
    {}, {'BUILD_TARGETS': '  ', 'TEST_TARGETS': '  '},
    {'TEST_TARGETS': '//...', 'EVENT_NAME': 'pull_request_target'},
    {'TEST_TARGETS': '//...', 'TEST_ARGS': '"unterminated'},
])
def test_invalid_requests_fail_before_setup(tmp_path, values):
    assert run_step('Validate request', tmp_path, **values).returncode != 0


def test_netrc_is_available_to_cache_setup_and_cleaned_after_its_post_step():
    """Post actions run in reverse order; credentials must outlive cache finalization."""
    setup = next(step for step in STEPS if step.get('name') == 'Configure Netrc for dependency downloads')
    cache = next(step for step in STEPS if step.get('name') == 'Setup Bazel with shared caching')
    assert setup['uses'] == '$/.github/actions/_setup-netrc'
    assert setup['with']['content'] == '${{ secrets.netrc }}'
    assert STEPS.index(setup) < STEPS.index(cache)
    action = yaml.safe_load((WORKFLOW.parents[1] / 'actions/_setup-netrc/action.yml').read_text())
    assert action['runs']['post'] == 'cleanup.cjs'
    assert action['runs']['post-if'] == 'always()'


def test_build_only_validation_disables_report_upload(tmp_path):
    assert run_step('Validate request', tmp_path, BUILD_TARGETS='//...').returncode == 0
    assert (tmp_path / 'output').read_text().splitlines()[0] == 'has-results=false'


def test_qnx_setup_requires_all_credentials_before_checkout(tmp_path):
    result = run_step('Validate request', tmp_path, BUILD_TARGETS='//...', SETUP_QNX_SDP='true',
                      PR_HEAD_REPOSITORY=DEFAULTS['REPOSITORY'])
    assert result.returncode != 0
    assert 'setup-qnx-sdp requires qnx-license, qnx-user and qnx-password secrets.' in result.stderr
    assert not (tmp_path / 'output').exists()


@pytest.mark.parametrize('enabled, credentials', [('false', 'false'), ('false', 'true'), ('true', 'true')])
def test_optional_qnx_setup_accepts_normal_jobs_and_complete_credentials(tmp_path, enabled, credentials):
    result = run_step('Validate request', tmp_path, BUILD_TARGETS='//...',
                      SETUP_QNX_SDP=enabled, QNX_CREDENTIALS_PRESENT=credentials,
                      PR_HEAD_REPOSITORY=DEFAULTS['REPOSITORY'])
    assert result.returncode == 0, result.stderr


def test_qnx_and_qemu_setup_are_independent_and_preserve_cache_defaults():
    """QEMU is explicit even for QNX tests; only SDK setup changes cache policy."""
    qnx = next(step for step in STEPS if step.get('name') == 'Setup QNX SDP')
    qemu = next(step for step in STEPS if step.get('name') == 'Setup QEMU')
    cache = next(step for step in STEPS if step.get('name') == 'Setup Bazel with shared caching')
    execute = next(step for step in STEPS if step.get('name') == 'Execute Bazel commands')
    assert qnx['if'] == '${{ inputs.setup-qnx-sdp }}'
    assert qemu['if'] == '${{ inputs.setup-qemu }}'
    assert STEPS.index(qnx) < STEPS.index(cache) < STEPS.index(execute)
    assert STEPS.index(qemu) < STEPS.index(execute)
    assert 'GITHUB_WORKSPACE' not in qnx.get('env', {})
    for key in ('repository-cache-restore', 'repository-cache-save'):
        assert cache['with'][key] == "${{ inputs.setup-qnx-sdp && 'false' || 'auto' }}"
    for key in ('external-cache-restore', 'external-cache-save'):
        assert cache['with'][key] == '${{ !inputs.setup-qnx-sdp }}'
    assert not any(key.startswith('disk-cache-') and key != 'disk-cache-key' for key in cache['with'])


@pytest.mark.parametrize('failed', ['', 'tee', 'control', 'trigger'])
def test_qemu_host_preparation_installs_kvm_rule_and_propagates_failures(tmp_path, failed):
    """Record privileged commands without running sudo or modifying device permissions."""
    action = yaml.safe_load((WORKFLOW.parents[1] / 'actions/_setup-qemu/action.yml').read_text())
    step = next(step for step in action['runs']['steps'] if step.get('name') == 'Enable KVM access')
    sudo = tmp_path / 'sudo'
    sudo.write_text('#!' + sys.executable + '\n'
                    'import json, os, sys\n'
                    'payload = sys.stdin.read() if sys.argv[1] == "tee" else ""\n'
                    'with open(os.environ["COMMAND_LOG"], "a") as output:\n'
                    '    output.write(json.dumps([sys.argv[1:], payload]) + "\\n")\n'
                    'sys.exit(23 if os.environ["FAIL_COMMAND"] in sys.argv[1:] else 0)\n')
    sudo.chmod(0o755)
    log = tmp_path / 'commands'
    environment = {**os.environ, 'PATH': str(tmp_path) + os.pathsep + os.environ['PATH'],
                   'COMMAND_LOG': str(log), 'FAIL_COMMAND': failed}
    result = subprocess.run([step['shell'], '-c', step['run']], env=environment, capture_output=True, text=True)
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert calls[0] == [['tee', '/etc/udev/rules.d/99-kvm4all.rules'],
                        'KERNEL=="kvm", GROUP="kvm", MODE="0666", OPTIONS+="static_node=kvm"\n']
    if failed:
        assert result.returncode == 23
        assert failed in calls[-1][0]
    else:
        assert result.returncode == 0, result.stderr
        assert [args for args, _ in calls[1:]] == [
            ['udevadm', 'control', '--reload-rules'], ['udevadm', 'trigger', '--name-match=kvm'],
        ]


def test_nested_workspace_selects_standard_test_reports_and_retains_explicit_name(tmp_path):
    (tmp_path / 'examples/integration').mkdir(parents=True)
    result = run_step('Prepare workspace and report paths', tmp_path,
                      WORKING_DIRECTORY='examples/integration', ARTIFACT_NAME='existing-docs-input')
    assert result.returncode == 0, result.stderr
    outputs = (tmp_path / 'output').read_text()
    assert 'artifact-name=existing-docs-input\n' in outputs
    assert outputs.splitlines()[2:-1] == [
        f'{tmp_path}/examples/integration/bazel-testlogs/**/*.xml',
        f'{tmp_path}/examples/integration/bazel-testlogs/**/*.log',
    ]


def test_default_report_names_do_not_collide_between_invocations(tmp_path):
    names = []
    for _ in range(2):
        (tmp_path / 'output').unlink(missing_ok=True)
        assert run_step('Prepare workspace and report paths', tmp_path).returncode == 0
        names.append((tmp_path / 'output').read_text().splitlines()[0])
    assert names[0] != names[1]


@pytest.mark.parametrize('directory', ['..', '/tmp', 'missing'])
def test_workspace_must_exist_inside_checkout(tmp_path, directory):
    assert run_step('Prepare workspace and report paths', tmp_path,
                    WORKING_DIRECTORY=directory).returncode != 0


def test_coverage_upload_uses_the_centrally_selected_report_directory(tmp_path):
    report = tmp_path / 'coverage-report'
    report.mkdir()
    (report / 'index.html').write_text('coverage report')
    result = run_step('Select coverage results', tmp_path)
    assert result.returncode == 0, result.stderr
    output = (tmp_path / 'output').read_text()
    assert f'{tmp_path}/coverage-report\n' in output
    assert 'bazel-testlogs' not in output


@pytest.mark.parametrize('values', [
    {'TEST_TARGETS': '//...', 'TEST_COMMAND': 'clean'},
    {'BUILD_TARGETS': '//...', 'TEST_COMMAND': 'coverage'},
])
def test_invalid_coverage_requests_fail_before_setup(tmp_path, values):
    assert run_step('Validate request', tmp_path, **values).returncode != 0


@pytest.mark.parametrize('configs', ['--config=linux', 'linux,tsan', 'linux $(touch marker)'])
def test_config_names_do_not_accept_flags_or_shell_expressions(tmp_path, configs):
    assert run_step('Validate request', tmp_path, TEST_TARGETS='//...', BAZEL_CONFIGS=configs).returncode != 0
    assert not (tmp_path / 'marker').exists()


def test_empty_coverage_archive_falls_back_to_test_results(tmp_path):
    (tmp_path / 'coverage-report').mkdir()
    result = run_step('Select coverage results', tmp_path)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / 'output').read_text() == ''


@pytest.mark.parametrize('archive', [False, True])
def test_coverage_upload_selects_archive_or_nested_workspace_test_reports(tmp_path, archive):
    """A failed command can leave test logs, with or without an archived coverage report."""
    workspace = tmp_path / 'examples/integration'
    logs = workspace / 'bazel-testlogs/pkg/test'
    logs.mkdir(parents=True)
    (logs / 'test.xml').write_text('<testsuite/>')
    (logs / 'test.log').write_text('test output')
    (logs / 'tool.log').write_text('additional test tool output')
    (logs / 'temporary.txt').write_text('not a report')
    if archive:
        report = tmp_path / 'coverage-report'
        report.mkdir()
        (report / 'index.html').write_text('coverage report')
    prepared = run_step('Prepare workspace and report paths', tmp_path,
                        WORKING_DIRECTORY='examples/integration')
    assert prepared.returncode == 0, prepared.stderr
    test_paths = (tmp_path / 'output').read_text().splitlines()[2:-1]
    (tmp_path / 'output').unlink()
    selected = run_step('Select coverage results', tmp_path)
    assert selected.returncode == 0, selected.stderr
    coverage_path = (tmp_path / 'output').read_text().removeprefix('result-paths=').strip()
    # The upload expression falls back to the prepared XML/log paths without an archive.
    paths = [coverage_path] if coverage_path else test_paths
    assert paths == ([str(tmp_path / 'coverage-report')] if archive else [
        str(workspace / 'bazel-testlogs/**/*.xml'), str(workspace / 'bazel-testlogs/**/*.log'),
    ])
    # Extra tool logs must survive the fallback, while unrelated files stay out.
    selected_reports = {path.name for pattern in test_paths
                        for path in workspace.glob(str(Path(pattern).relative_to(workspace)))}
    assert selected_reports == {'test.xml', 'test.log', 'tool.log'}


def evaluate_gate(expression, *, event='pull_request_target', fork=True, qnx=True,
                  label='', labels=(), environment='workflow-approval', approval='skipped',
                  cancelled=False, head_repository=None):
    """Evaluate the workflow's boolean gates using explicit event and input values."""
    if 'cancelled()' not in expression and cancelled:
        return False
    if head_repository is None:
        head_repository = 'contributor/module' if fork else DEFAULTS['REPOSITORY']
    values = {
        'contains(github.event.pull_request.labels.*.name, inputs.fork-pr-label)':
            label.casefold() in {item.casefold() for item in labels},
        'github.event_name': event,
        'github.event.pull_request.head.repo.full_name': head_repository,
        'github.repository': DEFAULTS['REPOSITORY'],
        'inputs.setup-qnx-sdp': qnx,
        'inputs.fork-pr-label': label,
        'inputs.environment-name': environment,
        'needs.approval.result': approval,
        'cancelled()': cancelled,
    }
    expression = expression.removeprefix('${{').removesuffix('}}').strip()
    for key, value in values.items():
        expression = expression.replace(key, repr(value))
    expression = expression.replace('&&', ' and ').replace('||', ' or ')
    expression = re.sub(r'!(?!=)', 'not ', expression)
    return eval('(' + expression + ')', {'__builtins__': {}}, {})


@pytest.mark.parametrize('event, fork, qnx, label, labels, approval, environment, cancelled, gate, outcome', [
    ('pull_request_target', True, True, 'test-qnx', [], 'skipped', 'workflow-approval', False, False, 'skipped'),
    ('pull_request_target', True, True, 'test-qnx', ['test-qnx-extra'], 'skipped', 'workflow-approval', False, False, 'skipped'),
    ('pull_request_target', True, True, 'test-qnx', ['test-qnx'], 'pending', 'workflow-approval', False, True, 'skipped'),
    ('pull_request_target', True, True, 'test-qnx', ['test-qnx'], 'success', 'workflow-approval', False, True, 'success'),
    ('pull_request_target', True, True, 'test-qnx', ['TEST-QNX'], 'success', 'workflow-approval', False, True, 'success'),
    ('pull_request_target', True, True, 'TEST-QNX', ['test-qnx'], 'success', 'workflow-approval', False, True, 'success'),
    ('pull_request_target', True, True, 'test-qnx', ['test-qnx'], 'failure', 'workflow-approval', False, True, 'skipped'),
    ('pull_request_target', True, True, '', [], 'success', 'workflow-approval', False, True, 'success'),
    ('pull_request_target', True, True, '', [], 'cancelled', 'workflow-approval', False, True, 'skipped'),
    ('pull_request_target', True, True, '', [], 'skipped', 'workflow-approval', False, True, 'skipped'),
    ('pull_request_target', True, True, '', [], 'success', 'workflow-approval', True, False, 'skipped'),
    ('pull_request_target', True, True, 'test-qnx', [], 'skipped', '', False, False, 'skipped'),
    ('pull_request_target', True, True, 'test-qnx', ['test-qnx'], 'skipped', '', False, False, 'failure'),
    ('pull_request_target', True, False, 'test-qnx', [], 'skipped', 'workflow-approval', False, False, 'failure'),
    ('pull_request_target', True, False, 'test-qnx', ['test-qnx'], 'skipped', 'workflow-approval', False, False, 'failure'),
    ('pull_request_target', False, True, 'test-qnx', [], 'skipped', 'workflow-approval', False, False, 'success'),
    ('pull_request_target', False, False, 'test-qnx', [], 'skipped', 'workflow-approval', False, False, 'failure'),
    ('pull_request', True, True, '', [], 'skipped', 'workflow-approval', False, False, 'failure'),
    ('pull_request', True, True, 'test-qnx', [], 'skipped', 'workflow-approval', False, False, 'failure'),
    ('pull_request', True, True, 'test-qnx', ['test-qnx'], 'skipped', 'workflow-approval', False, False, 'failure'),
    ('pull_request', True, False, 'test-qnx', [], 'skipped', 'workflow-approval', False, False, 'skipped'),
    ('pull_request', True, False, 'test-qnx', ['test-qnx'], 'skipped', 'workflow-approval', False, False, 'success'),
    ('pull_request', True, False, '', [], 'skipped', '', False, False, 'success'),
    ('pull_request', False, False, 'test-qnx', [], 'skipped', '', False, False, 'success'),
    ('pull_request', False, True, 'test-qnx', [], 'skipped', 'workflow-approval', False, False, 'success'),
    ('merge_group', True, True, 'test-qnx', [], 'skipped', 'workflow-approval', False, False, 'success'),
    ('merge_group', False, False, 'test-qnx', [], 'skipped', '', False, False, 'success'),
    ('push', True, True, 'test-qnx', [], 'skipped', 'workflow-approval', False, False, 'success'),
    ('push', False, False, 'test-qnx', [], 'skipped', '', False, False, 'success'),
    ('workflow_dispatch', True, True, 'test-qnx', [], 'skipped', 'workflow-approval', False, False, 'success'),
    ('workflow_dispatch', False, False, 'test-qnx', [], 'skipped', '', False, False, 'success'),
])
def test_event_label_approval_and_validation_paths(tmp_path, event, fork, qnx, label, labels,
                                                   approval, environment, cancelled, gate, outcome):
    """Optional builds remain skipped; invalid calls fail before checkout; approved calls pin the event SHA."""
    context = dict(event=event, fork=fork, qnx=qnx, label=label, labels=labels,
                   environment=environment, approval=approval, cancelled=cancelled)
    assert evaluate_gate(JOBS['approval']['if'], **context) == gate
    can_start = evaluate_gate(JOBS['bazel']['if'], **context)
    if outcome == 'skipped':
        assert not can_start
        return
    assert can_start
    head = 'contributor/module' if fork else DEFAULTS['REPOSITORY']
    result = run_step('Validate request', tmp_path, BUILD_TARGETS='//...', EVENT_NAME=event,
                      PR_HEAD_REPOSITORY=head, APPROVAL_RESULT=approval,
                      APPROVAL_ENVIRONMENT=environment, SETUP_QNX_SDP=str(qnx).lower(),
                      QNX_CREDENTIALS_PRESENT='true')
    if outcome == 'failure':
        assert result.returncode != 0
        assert not (tmp_path / 'output').exists()
        if event == 'pull_request':
            assert 'QNX fork PRs require pull_request_target with environment approval' in result.stderr
        elif not qnx:
            assert 'pull_request_target requires setup-qnx-sdp.' in result.stderr
        else:
            assert 'Fork execution requires successful environment approval.' in result.stderr
        return
    assert result.returncode == 0, result.stderr
    outputs = dict(line.split('=', 1) for line in (tmp_path / 'output').read_text().splitlines())
    target = event == 'pull_request_target'
    assert outputs['checkout-sha'] == DEFAULTS['PR_HEAD_SHA' if target else 'EVENT_SHA']
    assert outputs['checkout-repository'] == (head if target else DEFAULTS['REPOSITORY'])


@pytest.mark.parametrize('revision', ['', 'main', 'refs/pull/42/head', 'abc123'])
def test_privileged_checkout_rejects_mutable_or_missing_revision(tmp_path, revision):
    result = run_step('Validate request', tmp_path, BUILD_TARGETS='//...',
                      EVENT_NAME='pull_request_target', PR_HEAD_SHA=revision,
                      APPROVAL_RESULT='success', SETUP_QNX_SDP='true', QNX_CREDENTIALS_PRESENT='true')
    assert result.returncode != 0
    assert 'immutable event commit SHA' in result.stderr
    assert not (tmp_path / 'output').exists()


def test_checkout_consumes_only_validated_revision_and_gate_has_no_checkout():
    """Branch movement during approval cannot change the checkout's immutable input."""
    assert set(JOBS) == {'approval', 'bazel'}
    assert JOBS['bazel']['needs'] == 'approval'
    assert 'needs' not in JOBS['approval']
    assert JOBS['approval']['steps'] == [{'name': 'Approve fork revision', 'run': 'true'}]
    assert JOBS['approval']['environment']['name'] == '${{ inputs.environment-name }}'
    assert 'github.event.pull_request.head.sha' in JOBS['approval']['environment']['url']
    checkout = next(step for step in STEPS if step.get('name') == 'Checkout repository')
    assert checkout['with']['ref'] == '${{ steps.validate.outputs.checkout-sha }}'
    assert checkout['with']['repository'] == '${{ steps.validate.outputs.checkout-repository }}'
    assert checkout['with']['persist-credentials'] is False


@pytest.mark.parametrize('event', ['pull_request', 'pull_request_target', 'merge_group',
                                  'push', 'workflow_dispatch'])
@pytest.mark.parametrize('approval', ['skipped', 'failure', 'cancelled'])
def test_trusted_events_do_not_depend_on_approval_results(event, approval):
    """A required queue or internal build must execute independently of optional fork approval."""
    context = dict(event=event, fork=False, label='test-qnx', approval=approval)
    assert not evaluate_gate(JOBS['approval']['if'], **context)
    assert evaluate_gate(JOBS['bazel']['if'], **context)
    assert not evaluate_gate(JOBS['bazel']['if'], cancelled=True, **context)


def test_approval_honors_the_callers_runner_selection():
    """Approval must remain usable when callers have only their configured runners."""
    assert JOBS['approval']['runs-on'] == JOBS['bazel']['runs-on']


@pytest.mark.parametrize('event', ['pull_request', 'pull_request_target'])
def test_missing_pr_repository_fails_before_approval_or_checkout(tmp_path, event):
    """A deleted head repository must produce a validation error instead of an invalid approval."""
    context = dict(event=event, head_repository='', label='test-qnx', labels=['test-qnx'])
    assert not evaluate_gate(JOBS['approval']['if'], **context)
    assert evaluate_gate(JOBS['bazel']['if'], **context)
    result = run_step('Validate request', tmp_path, EVENT_NAME=event, BUILD_TARGETS='//...',
                      PR_HEAD_REPOSITORY='', SETUP_QNX_SDP='true', QNX_CREDENTIALS_PRESENT='true')
    assert result.returncode != 0
    assert ('Cannot run PR checks: the event does not identify '
            'the repository containing the PR branch.') in result.stderr
    assert not (tmp_path / 'output').exists()


@pytest.mark.parametrize('credentials', ['false', 'true'])
def test_qnx_fork_pull_request_is_rejected_even_with_credentials(tmp_path, credentials):
    """Secret availability must never allow an ordinary fork PR to bypass approval."""
    result = run_step('Validate request', tmp_path, BUILD_TARGETS='//...',
                      SETUP_QNX_SDP='true', QNX_CREDENTIALS_PRESENT=credentials)
    assert result.returncode != 0
    assert 'QNX fork PRs require pull_request_target with environment approval' in result.stderr
    assert 'setup-qnx-sdp requires qnx-license' not in result.stderr
    assert not (tmp_path / 'output').exists()
