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
                    COVERAGE_THRESHOLD='100', COVERAGE_REPORT_DIRECTORY=str(tmp_path / 'coverage-report'))
    for key, value in defaults.items():
        monkeypatch.setenv(key, value)
    monkeypatch.chdir(tmp_path)
    calls = []

    def subprocess_run(argv, *, check):
        calls.append((argv, Path.cwd(), dict(os.environ)))
        return SimpleNamespace(returncode=23 if argv[1] == os.environ.get('FAIL_COMMAND') else 0)

    monkeypatch.setattr(execute.subprocess, 'run', subprocess_run)

    def run(**values):
        for key, value in values.items():
            monkeypatch.setenv(key, value)
        execute.main()
        return [argv for argv, _, _ in calls]

    return SimpleNamespace(run=run, calls=calls)


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
def test_failure_preserves_exit_status_and_stops_remaining_commands(execution, failed, expected):
    with pytest.raises(SystemExit) as error:
        execution.run(BUILD_TARGETS='//score/...', TEST_TARGETS='//...', FAIL_COMMAND=failed,
                    TEST_COMMAND='coverage' if failed in ('coverage', 'run') else 'test')
    assert error.value.code == 23
    assert [argv[1] for argv, _, _ in execution.calls] == expected


def test_target_exclusions_and_quoted_arguments_remain_literal(execution):
    assert execution.run(TEST_TARGETS='//... -//pkg:slow', TEST_ARGS='--test_env="VALUE=$(touch marker)"') == [
        ['bazel', 'test', '--lockfile_mode=error', '--test_env=VALUE=$(touch marker)', '--', '//...', '-//pkg:slow'],
    ]


def test_coverage_runs_before_reporter_without_leaking_instrumentation_configs(tmp_path, execution):
    assert execution.run(TEST_TARGETS='//score/... -//score:excluded', TEST_COMMAND='coverage',
                       BAZEL_CONFIGS='llvm_cov', TEST_ARGS='--build_tests_only',
                       COVERAGE_JUSTIFICATIONS='quality/coverage/coverage_justifications.yaml') == [
        ['bazel', 'coverage', '--lockfile_mode=error', '--config=llvm_cov', '--build_tests_only',
         '--', '//score/...', '-//score:excluded'],
        ['bazel', 'run', '--lockfile_mode=error', '@score_coverage//:generate_coverage_html', '--',
         '--archive-dir', str(tmp_path / 'coverage-report'),
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
