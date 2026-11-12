# Copyright (c) 2026 Contributors to the Eclipse Foundation
# SPDX-License-Identifier: Apache-2.0
"""Test Bazel execution directly, independently of workflow YAML and a Bazel installation."""

import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / '.github/actions/_bazel-execute'))
import execute


@pytest.fixture
def execution(monkeypatch, tmp_path):
    """Run main with a validated request, recording commands and their inherited environment."""
    defaults = dict(BUILD_TARGETS='', TEST_TARGETS='', TEST_COMMAND='test', BAZEL_ARGS='',
                    BAZEL_CONFIGS='', BUILD_ARGS='', TEST_ARGS='', COVERAGE_JUSTIFICATIONS='',
                    COVERAGE_THRESHOLD='100', COVERAGE_REPORT_DIRECTORY=str(tmp_path / 'coverage-report'),
                    SETUP_QNX_SDP='false', QNX_CREDENTIAL_HELPER='', QNX_BAZELRC='')
    for key, value in defaults.items():
        monkeypatch.setenv(key, value)
    monkeypatch.chdir(tmp_path)
    calls = []

    def subprocess_run(argv, *, check):
        calls.append((argv, Path.cwd(), dict(os.environ)))
        # Exit code 23 represents a Bazel failure without running a real build.
        command = next(arg for arg in argv[1:] if arg in ('build', 'test', 'coverage', 'run'))
        return SimpleNamespace(returncode=23 if command == os.environ.get('FAIL_COMMAND') else 0)

    monkeypatch.setattr(execute.subprocess, 'run', subprocess_run)
    resolutions: list[list[str]] = []
    monkeypatch.setattr(execute, 'download_dependencies', resolutions.append)

    def run(**values):
        for key, value in values.items():
            monkeypatch.setenv(key, value)
        execute.main()
        return [argv for argv, _, _ in calls]

    return SimpleNamespace(run=run, calls=calls, resolutions=resolutions)


@pytest.mark.parametrize('build, test, expected', [
    ('//examples/... //score/...', '//...', ['build', 'test']),
    ('//score/...', '', ['build']),
    ('', '//...', ['test']),
])
def test_enabled_commands_share_workspace_and_preserve_flags(tmp_path, monkeypatch, execution, build, test, expected):
    workspace = tmp_path / 'examples/integration'
    workspace.mkdir(parents=True)
    monkeypatch.chdir(workspace)
    commands = execution.run(BUILD_TARGETS=build, TEST_TARGETS=test, BAZEL_ARGS='--config=linux',
                             BUILD_ARGS='--keep_going', TEST_ARGS='--test_timeout=600')
    assert [argv[1] for argv in commands] == expected
    assert all(directory == workspace for _, directory, _ in execution.calls)
    for argv in commands:
        specific = '--keep_going' if argv[1] == 'build' else '--test_timeout=600'
        targets = build if argv[1] == 'build' else test
        assert argv == ['bazel', argv[1], '--lockfile_mode=error', '--config=linux', specific, '--', *targets.split()]


@pytest.mark.parametrize('failed, expected', [
    ('build', ['build']), ('test', ['build', 'test']), ('coverage', ['build', 'coverage']),
    ('run', ['build', 'coverage', 'run']),
])
@pytest.mark.parametrize('qnx', [False, True])
def test_failure_preserves_exit_status_and_stops_remaining_commands(execution, failed, expected, qnx):
    """Startup options for a QNX license server must not change failure handling."""
    with pytest.raises(SystemExit) as error:
        execution.run(BUILD_TARGETS='//score/...', TEST_TARGETS='//...', FAIL_COMMAND=failed,
                      TEST_COMMAND='coverage' if failed in ('coverage', 'run') else 'test',
                      SETUP_QNX_SDP='true' if qnx else 'false',
                      QNX_BAZELRC='/checkout with spaces/user.bazelrc' if qnx else '')
    assert error.value.code == 23
    command_index = 2 if qnx else 1
    assert [argv[command_index] for argv, _, _ in execution.calls] == expected


def test_target_exclusions_and_quoted_arguments_remain_literal(execution):
    assert execution.run(TEST_TARGETS='//... -//pkg:slow', TEST_ARGS='--test_env="VALUE=$(touch marker)"') == [
        ['bazel', 'test', '--lockfile_mode=error', '--test_env=VALUE=$(touch marker)', '--', '//...', '-//pkg:slow'],
    ]


@pytest.mark.parametrize(('setup_qnx_sdp', 'platform'), [('false', 'linux'), ('true', 'qnx')])
def test_coverage_runs_before_reporter_without_leaking_instrumentation_configs(
    tmp_path, execution, setup_qnx_sdp, platform,
):
    assert execution.run(TEST_TARGETS='//score/... -//score:excluded', TEST_COMMAND='coverage',
                         BAZEL_CONFIGS='llvm_cov', TEST_ARGS='--build_tests_only', SETUP_QNX_SDP=setup_qnx_sdp,
                         COVERAGE_JUSTIFICATIONS='quality/coverage/coverage_justifications.yaml') == [
        ['bazel', 'coverage', '--lockfile_mode=error', '--config=llvm_cov', '--build_tests_only',
         '--', '//score/...', '-//score:excluded'],
        ['bazel', 'run', '--lockfile_mode=error', '@score_coverage//:generate_coverage_html', '--',
         '--archive-dir', str(tmp_path / 'coverage-report'), '--platform', platform,
         '--yaml', 'quality/coverage/coverage_justifications.yaml'],
    ]


@pytest.mark.parametrize('threshold', ['0', '66', '100'])
def test_coverage_reporter_inherits_threshold_and_omits_absent_justifications(execution, threshold):
    execution.run(TEST_TARGETS='//...', TEST_COMMAND='coverage', COVERAGE_THRESHOLD=threshold)
    reporter, _, environment = execution.calls[-1]
    assert reporter[1] == 'run'
    assert '--yaml' not in reporter
    assert environment['COVERAGE_THRESHOLD'] == threshold


def test_named_configs_preserve_order_for_build_and_test(execution):
    assert execution.run(BUILD_TARGETS='//score/...', TEST_TARGETS='//...',
                         BAZEL_CONFIGS='x86_64-linux asan_ubsan_lsan') == [
        ['bazel', 'build', '--lockfile_mode=error', '--config=x86_64-linux', '--config=asan_ubsan_lsan', '--', '//score/...'],
        ['bazel', 'test', '--lockfile_mode=error', '--config=x86_64-linux', '--config=asan_ubsan_lsan', '--', '//...'],
    ]


def test_additional_flags_keep_lockfile_default_for_build_and_test(execution):
    commands = execution.run(BUILD_TARGETS='//score/...', TEST_TARGETS='//...', BAZEL_ARGS='--verbose_failures')
    assert [argv[:4] for argv in commands] == [
        ['bazel', 'build', '--lockfile_mode=error', '--verbose_failures'],
        ['bazel', 'test', '--lockfile_mode=error', '--verbose_failures'],
    ]


def test_explicit_test_lockfile_mode_overrides_only_the_test_default(execution):
    assert execution.run(BUILD_TARGETS='//score/...', TEST_TARGETS='//...', TEST_ARGS='--lockfile_mode=update') == [
        ['bazel', 'build', '--lockfile_mode=error', '--', '//score/...'],
        ['bazel', 'test', '--lockfile_mode=error', '--lockfile_mode=update', '--', '//...'],
    ]


def test_qnx_download_helper_is_literal_and_shared_by_all_commands(execution):
    """The absolute helper works from nested workspaces alongside a separate Netrc file."""
    helper = '/checkout with spaces/.github/tools/qnx_credential_helper.py'
    commands = execution.run(BUILD_TARGETS='//score/...', TEST_TARGETS='//...', TEST_COMMAND='coverage',
                             SETUP_QNX_SDP='true', QNX_CREDENTIAL_HELPER=helper,
                             NETRC='/runner/temp/other-download-credentials')
    assert [argv[1] for argv in commands] == ['build', 'coverage', 'run']
    for argv, _, environment in execution.calls:
        assert argv[3] == f'--credential_helper=*.qnx.com={helper}'
        assert environment['NETRC'] == '/runner/temp/other-download-credentials'


def test_nested_qnx_workspace_also_reads_generated_license_server_config(execution):
    """The extra config is a Bazel startup option, including for the host report tool."""
    commands = execution.run(TEST_TARGETS='//...', TEST_COMMAND='coverage', SETUP_QNX_SDP='true',
                             QNX_BAZELRC='/checkout with spaces/user.bazelrc')
    assert [argv[:3] for argv in commands] == [
        ['bazel', '--bazelrc=/checkout with spaces/user.bazelrc', 'coverage'],
        ['bazel', '--bazelrc=/checkout with spaces/user.bazelrc', 'run'],
    ]


@pytest.mark.parametrize('enabled, helper', [('false', '/existing/helper'), ('true', '')])
def test_qnx_helper_is_not_forced_when_setup_or_helper_is_disabled(execution, enabled, helper):
    commands = execution.run(TEST_TARGETS='//...', SETUP_QNX_SDP=enabled, QNX_CREDENTIAL_HELPER=helper)
    assert commands == [['bazel', 'test', '--lockfile_mode=error', '--', '//...']]


@pytest.mark.parametrize('command', ['test', 'coverage'])
def test_resolution_uses_phase_flags_and_forces_analysis_only(execution, command: str):
    """The preparation must select the same dependencies even with per-command configs."""
    execution.run(BUILD_TARGETS='//lib/...', TEST_TARGETS='//... -//slow/...',
                  TEST_COMMAND=command, BAZEL_CONFIGS='qnx', BAZEL_ARGS='--build',
                  BUILD_ARGS='--keep_going', TEST_ARGS='--test_timeout=600',
                  SETUP_QNX_SDP='true', QNX_BAZELRC='/checkout/user.bazelrc',
                  QNX_CREDENTIAL_HELPER='/checkout/helper')
    for (argv, _, _), resolution in zip(execution.calls[:2], execution.resolutions[:2], strict=True):
        separator = argv.index('--')
        assert resolution == [*argv[:separator], '--nobuild', *argv[separator:]]
    if command == 'coverage':
        assert execution.resolutions[-1] == [
            'bazel', '--bazelrc=/checkout/user.bazelrc', 'run', '--lockfile_mode=error',
            '--credential_helper=*.qnx.com=/checkout/helper', '--build',
            '@score_coverage//:generate_coverage_html', '--nobuild', '--norun',
        ]


@pytest.mark.parametrize('diagnostic', [
    'java.net.SocketException: Connection reset',
    'java.net.SocketTimeoutException: Read timed out',
    'java.net.UnknownHostException: downloads.example.com',
    'ContentLengthMismatchException: expected 100 bytes',
    'Checksum was abc123 but wanted def456',
    'GET returned 503 Service Unavailable',
    'GET returned 429 Too Many Requests',
    'Network is unreachable',
])
def test_known_resolution_causes_allow_retries(diagnostic: str):
    assert execute.retryable_download_failure(
        f'Error in download_and_extract: java.io.IOException: Error downloading '
        f'[https://downloads.example.com/sdk.tar] to /tmp/sdk: {diagnostic}\n')
    assert execute.retryable_download_failure(diagnostic)


@pytest.mark.parametrize('output', [
    'ERROR: Analysis of target failed: invalid configuration',
    'Error downloading [https://example.com/sdk]: GET returned 401 Unauthorized',
    'Error downloading [https://example.com/sdk]: GET returned 403 Forbidden',
    'Error downloading [https://example.com/sdk]: GET returned 404 Not Found',
    'Error downloading [https://example.com/sdk]: certificate verify failed',
])
def test_other_failures_are_not_retryable(output: str):
    assert not execute.retryable_download_failure(output)


@pytest.mark.parametrize('continuation', [
    '    Connection reset',
    'Caused by: java.net.SocketTimeoutException: Read timed out',
    'java.net.UnknownHostException: downloads.example.com',
    '    Checksum was abc123\n    but wanted def456',
    '    GET returned 503 Service Unavailable',
])
def test_download_cause_can_follow_on_continuation_lines(continuation: str):
    assert execute.retryable_download_failure(
        'ERROR: Error downloading [https://example.com/sdk]:\n' + continuation)


def test_http_status_must_be_a_complete_code():
    assert not execute.retryable_download_failure(
        'Error downloading [https://example.com/sdk]: HTTP 5030')


@pytest.fixture
def resolution_process(monkeypatch: pytest.MonkeyPatch):
    """Provide streamed process results and record real retry orchestration without sleeping."""
    from io import StringIO

    calls: list[tuple[list[str], dict[str, object]]] = []
    delays: list[int] = []
    results: list[tuple[int, str]] = []

    class Process:
        def __init__(self, argv: list[str], **kwargs: object):
            calls.append((argv, kwargs))
            self.returncode, output = results.pop(0)
            self.stdout = StringIO(output)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.stdout.close()

        def wait(self):
            return self.returncode

    monkeypatch.setattr(execute.subprocess, 'Popen', Process)
    monkeypatch.setattr(execute.time, 'sleep', delays.append)
    return SimpleNamespace(calls=calls, delays=delays, results=results)


DOWNLOAD_ERROR = ('Error downloading [https://example.com/sdk]: '
                  'Checksum was abc123 but wanted def456\n')


def test_resolution_succeeds_after_five_cooldowns(resolution_process, capsys: pytest.CaptureFixture[str]):
    """Six attempts includes the first invocation; only failed attempts have a cooldown."""
    state = resolution_process
    state.results.extend([(1, DOWNLOAD_ERROR)] * 5 + [(0, 'Dependencies ready\n')])
    argv = ['bazel', 'test', '--nobuild', '--', '//...']
    execute.download_dependencies(argv)
    assert len(state.calls) == 6
    assert all(command == argv for command, _ in state.calls)
    assert state.delays == [30] * 5
    output = capsys.readouterr().out
    assert 'retrying in 30 seconds' in output
    assert 'Resolve dependencies (6/6)' in output
    assert 'Dependencies ready' in output
    assert 'exhausted' not in output


@pytest.mark.parametrize('status, output, attempts, delays', [
    (0, 'Ready\n', 1, []),
    (7, 'ERROR: invalid configuration\n', 1, []),
    (23, DOWNLOAD_ERROR, 6, [30] * 5),
])
def test_resolution_stops_on_success_unknown_error_or_exhaustion(
    resolution_process, capsys: pytest.CaptureFixture[str], status: int, output: str,
    attempts: int, delays: list[int],
):
    state = resolution_process
    state.results.extend([(status, output)] * attempts)
    if status:
        with pytest.raises(SystemExit) as error:
            execute.download_dependencies(['bazel', 'build', '--nobuild', '//...'])
        assert error.value.code == status
    else:
        execute.download_dependencies(['bazel', 'build', '--nobuild', '//...'])
    assert len(state.calls) == attempts
    assert state.delays == delays
    log = capsys.readouterr().out
    if attempts == 6:
        assert 'all 6 resolution attempts exhausted' in log
    elif status:
        assert 'not retrying' in log


def test_resolution_failure_prevents_execution_and_later_phases(execution, monkeypatch: pytest.MonkeyPatch):
    def fail_resolution(argv: list[str]):
        raise SystemExit(7)

    monkeypatch.setattr(execute, 'download_dependencies', fail_resolution)
    with pytest.raises(SystemExit) as error:
        execution.run(BUILD_TARGETS='//lib/...', TEST_TARGETS='//...')
    assert error.value.code == 7
    assert execution.calls == []


@pytest.mark.parametrize('color', [False, True])
@pytest.mark.parametrize('command, flags, status, output, expected', [
    ('run', ['--nobuild', '--norun'], 2, 'ERROR: No targets found to run\n', 0),
    ('run', ['--nobuild', '--norun'], 2, 'ERROR: invalid configuration\n', 2),
    ('run', ['--nobuild', '--norun'], 1, DOWNLOAD_ERROR, 1),
    ('run', ['--nobuild', '--norun'], 2,
     'ERROR: No targets found to run\nERROR: Build failed. Not running target\n', 2),
    ('run', [], 2, 'ERROR: No targets found to run\n', 2),
    ('build', ['--nobuild'], 2, 'ERROR: No targets found to run\n', 2),
])
def test_only_expected_analysis_only_run_completion_is_success(
    resolution_process, command: str, flags: list[str], status: int, output: str, expected: int,
    color: bool, capsys: pytest.CaptureFixture[str],
):
    """Accept run's post-analysis result without hiding analysis or actual run failures."""
    if color:
        output = '\x1b[31m\x1b[1m' + output.replace('ERROR: ', 'ERROR: \x1b[0m') + '\x1b[0m'
    resolution_process.results.append((status, output))
    result, captured = execute.run_resolution_attempt(['bazel', command, *flags, '//:tool'], 1)
    assert result == expected
    assert captured == output
    assert output in capsys.readouterr().out


@pytest.mark.skipif(not os.environ.get('BAZEL_TEST_BINARY'), reason='Set BAZEL_TEST_BINARY for real Bazel tests')
@pytest.mark.parametrize('already_built', [False, True])
def test_real_bazel_reporter_preparation_preserves_run_rc_without_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, already_built: bool,
):
    """With Bazel 8.8.2, build has an invalid platform while run selects a valid one.

    Preparation must work in a fresh workspace and with cached outputs, without
    building or running the program. BAZEL_TEST_BINARY selects the executable
    explicitly so the regular Python suite does not require a Bazel install.
    """
    import subprocess

    monkeypatch.chdir(tmp_path)
    (tmp_path / '.bazelversion').write_text('8.8.2\n')
    (tmp_path / 'WORKSPACE').write_text('workspace(name="reporter_test")\n')
    (tmp_path / '.bazelrc').write_text(
        'common --color=yes\n'
        'common --enable_bzlmod=false\n'
        'common --enable_workspace=true\n'
        'common --incompatible_autoload_externally=\n'
        'build --platforms=//:missing_build_platform\n'
        'run --platforms=//:host\n')
    (tmp_path / 'BUILD.bazel').write_text(r'''
platform(name = "host")
genrule(
    name = "reporter",
    outs = ["reporter.sh"],
    cmd = "echo '#!/bin/sh' > $@; echo 'echo executed >> \"$$BUILD_WORKSPACE_DIRECTORY/PROGRAM_EXECUTED\"' >> $@; chmod +x $@",
    executable = True,
)
''')
    bazel = [os.environ['BAZEL_TEST_BINARY'], '--batch', '--nosystem_rc', '--nohome_rc']
    wrong_configuration = subprocess.run([*bazel, 'build', '--nobuild', '//:reporter'],
                                         capture_output=True, text=True)
    assert wrong_configuration.returncode != 0
    assert 'missing_build_platform' in wrong_configuration.stderr
    if already_built:
        subprocess.run([*bazel, 'build', '--platforms=//:host', '//:reporter'], check=True)
    executable = tmp_path / 'bazel-bin/reporter.sh'
    marker = tmp_path / 'PROGRAM_EXECUTED'
    assert executable.exists() == already_built

    execute.download_dependencies([*bazel, 'run', '//:reporter', '--nobuild', '--norun'])

    assert executable.exists() == already_built
    assert not marker.exists()
    # The actual run still works with its rc override and executes exactly once.
    subprocess.run([*bazel, 'run', '//:reporter'], check=True)
    assert marker.read_text() == 'executed\n'
