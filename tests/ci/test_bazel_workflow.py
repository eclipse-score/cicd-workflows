# Copyright (c) 2026 Contributors to the Eclipse Foundation
# SPDX-License-Identifier: Apache-2.0
"""Exercise the draft workflow's actual Python steps with a controlled Bazel CLI."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
import yaml


WORKFLOW = Path(__file__).resolve().parents[2] / '.github/workflows/bazel.yml'
STEPS = yaml.safe_load(WORKFLOW.read_text())['jobs']['bazel']['steps']
DEFAULTS = dict(BUILD_TARGETS='', TEST_TARGETS='', BAZEL_ARGS='', BUILD_ARGS='', TEST_ARGS='',
                EVENT_NAME='pull_request', WORKING_DIRECTORY='.', ARTIFACT_NAME='', BAZEL_CONFIGS='',
                TEST_COMMAND='test', COVERAGE_JUSTIFICATIONS='', COVERAGE_THRESHOLD='100',
                RESULT_PATHS='bazel-testlogs/**/*.xml\nbazel-testlogs/**/test.log')


def run_step(name, root, **values):
    step = next(step for step in STEPS if step.get('name') == name)
    environment = {**os.environ, **DEFAULTS, 'GITHUB_WORKSPACE': str(root),
                   'GITHUB_OUTPUT': str(root / 'output'),
                   'COVERAGE_REPORT_DIRECTORY': str(root / 'coverage-report'), **values}
    return subprocess.run([sys.executable, '-c', step['run']], cwd=root, env=environment,
                          text=True, capture_output=True)


@pytest.fixture
def bazel(tmp_path):
    executable = tmp_path / 'bazel'
    executable.write_text('#!' + sys.executable + '\n' +
                          'import json, os, sys\n'
                          'with open(os.environ["COMMAND_LOG"], "a") as output:\n'
                          '    output.write(json.dumps([os.getcwd(), sys.argv[1:]]) + "\\n")\n'
                          'sys.exit(23 if sys.argv[1] == os.environ.get("FAIL_COMMAND") else 0)\n')
    executable.chmod(0o755)
    return {'PATH': str(tmp_path) + os.pathsep + os.environ['PATH'],
            'COMMAND_LOG': str(tmp_path / 'commands')}


@pytest.mark.parametrize('build, test, expected', [
    ('//examples/... //score/...', '//...', ['build', 'test']),
    ('//score/...', '', ['build']),
    ('', '//...', ['test']),
])
def test_enabled_commands_share_workspace_and_preserve_flags(tmp_path, bazel, build, test, expected):
    workspace = tmp_path / 'examples/integration'
    workspace.mkdir(parents=True)
    result = run_step('Execute Bazel commands', workspace, **bazel, BUILD_TARGETS=build,
                      TEST_TARGETS=test, BAZEL_ARGS='--config=linux',
                      BUILD_ARGS='--keep_going', TEST_ARGS='--test_timeout=600')
    assert result.returncode == 0, result.stderr
    commands = [json.loads(line) for line in (tmp_path / 'commands').read_text().splitlines()]
    assert [argv[0] for _, argv in commands] == expected
    assert all(directory == str(workspace) for directory, _ in commands)
    for _, argv in commands:
        specific = '--keep_going' if argv[0] == 'build' else '--test_timeout=600'
        targets = build if argv[0] == 'build' else test
        assert argv[1:] == ['--lockfile_mode=error', '--config=linux', specific, '--', *targets.split()]


@pytest.mark.parametrize('failed, expected', [('build', ['build']), ('test', ['build', 'test'])])
def test_failed_commands_keep_exit_status_and_build_failure_stops_tests(tmp_path, bazel, failed, expected):
    result = run_step('Execute Bazel commands', tmp_path, **bazel, BUILD_TARGETS='//score/...',
                      TEST_TARGETS='//...', FAIL_COMMAND=failed)
    assert result.returncode == 23
    commands = [json.loads(line)[1][0] for line in (tmp_path / 'commands').read_text().splitlines()]
    assert commands == expected


def test_target_exclusions_and_shell_metacharacters_are_passed_literally(tmp_path, bazel):
    result = run_step('Execute Bazel commands', tmp_path, **bazel,
                      TEST_TARGETS='//... -//pkg:slow', TEST_ARGS='--test_env="VALUE=$(touch marker)"')
    assert result.returncode == 0, result.stderr
    argv = json.loads((tmp_path / 'commands').read_text())[1]
    assert argv == ['test', '--lockfile_mode=error', '--test_env=VALUE=$(touch marker)', '--', '//...', '-//pkg:slow']
    assert not (tmp_path / 'marker').exists()


@pytest.mark.parametrize('values', [
    {}, {'BUILD_TARGETS': '  ', 'TEST_TARGETS': '  '},
    {'TEST_TARGETS': '//...', 'EVENT_NAME': 'pull_request_target'},
    {'TEST_TARGETS': '//...', 'TEST_ARGS': '"unterminated'},
])
def test_invalid_requests_fail_before_setup(tmp_path, values):
    assert run_step('Validate request', tmp_path, **values).returncode != 0


def test_build_only_validation_disables_report_upload(tmp_path):
    assert run_step('Validate request', tmp_path, BUILD_TARGETS='//...').returncode == 0
    assert (tmp_path / 'output').read_text() == 'has-results=false\n'


def test_nested_workspace_prefixes_every_report_pattern_and_retains_explicit_name(tmp_path):
    (tmp_path / 'examples/integration').mkdir(parents=True)
    result = run_step('Prepare workspace and report paths', tmp_path,
                      WORKING_DIRECTORY='examples/integration', ARTIFACT_NAME='existing-docs-input',
                      RESULT_PATHS='bazel-testlogs/**/*.xml\n!bazel-testlogs/excluded/**')
    assert result.returncode == 0, result.stderr
    outputs = (tmp_path / 'output').read_text()
    assert 'artifact-name=existing-docs-input\n' in outputs
    assert f'{tmp_path}/examples/integration/bazel-testlogs/**/*.xml\n' in outputs
    assert f'!{tmp_path}/examples/integration/bazel-testlogs/excluded/**\n' in outputs


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


def test_coverage_runs_before_shared_reporter_with_separate_flags(tmp_path, bazel):
    result = run_step('Execute Bazel commands', tmp_path, **bazel,
                      TEST_TARGETS='//score/... -//score:excluded', TEST_COMMAND='coverage',
                      BAZEL_CONFIGS='llvm_cov', TEST_ARGS='--build_tests_only',
                      COVERAGE_JUSTIFICATIONS='quality/coverage/coverage_justifications.yaml')
    assert result.returncode == 0, result.stderr
    commands = [json.loads(line)[1] for line in (tmp_path / 'commands').read_text().splitlines()]
    assert commands == [
        ['coverage', '--lockfile_mode=error', '--config=llvm_cov', '--build_tests_only',
         '--', '//score/...', '-//score:excluded'],
        ['run', '--lockfile_mode=error', '@score_coverage//:generate_coverage_html', '--',
         '--archive-dir', str(tmp_path / 'coverage-report'),
         '--yaml', 'quality/coverage/coverage_justifications.yaml'],
    ]


def test_coverage_failure_stops_reporter_and_keeps_failure_status(tmp_path, bazel):
    result = run_step('Execute Bazel commands', tmp_path, **bazel, TEST_TARGETS='//score/...',
                      TEST_COMMAND='coverage', FAIL_COMMAND='coverage')
    assert result.returncode == 23
    commands = [json.loads(line)[1][0] for line in (tmp_path / 'commands').read_text().splitlines()]
    assert commands == ['coverage']


@pytest.mark.parametrize('threshold, failure', [('0', False), ('66', False), ('100', True)])
def test_threshold_reaches_tool_and_report_survives_reporter_failure(tmp_path, bazel, threshold, failure):
    executable = tmp_path / 'bazel'
    executable.write_text('#!' + sys.executable + '\n' +
                          'import os, sys\n'
                          'from pathlib import Path\n'
                          'if sys.argv[1] == "run":\n'
                          '    report = Path(sys.argv[sys.argv.index("--archive-dir") + 1])\n'
                          '    report.mkdir()\n'
                          '    (report / "threshold").write_text(os.environ["COVERAGE_THRESHOLD"])\n'
                          '    sys.exit(23 if os.environ["FAIL_REPORTER"] == "true" else 0)\n')
    result = run_step('Execute Bazel commands', tmp_path, **bazel, TEST_TARGETS='//score/...',
                      TEST_COMMAND='coverage', COVERAGE_THRESHOLD=threshold,
                      FAIL_REPORTER=str(failure).lower())
    assert result.returncode == (23 if failure else 0)
    assert (tmp_path / 'coverage-report/threshold').read_text() == threshold


def test_coverage_upload_uses_the_centrally_selected_report_directory(tmp_path):
    report = tmp_path / 'coverage-report'
    report.mkdir()
    (report / 'index.html').write_text('coverage report')
    result = run_step('Select coverage results', tmp_path,
                      TEST_RESULT_PATHS=str(tmp_path / 'bazel-testlogs/**/*.xml'))
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


def test_named_configs_preserve_order_for_build_and_test(tmp_path, bazel):
    result = run_step('Execute Bazel commands', tmp_path, **bazel, BUILD_TARGETS='//score/...',
                      TEST_TARGETS='//...', BAZEL_CONFIGS='x86_64-linux asan_ubsan_lsan')
    assert result.returncode == 0, result.stderr
    commands = [json.loads(line)[1] for line in (tmp_path / 'commands').read_text().splitlines()]
    assert commands == [
        ['build', '--lockfile_mode=error', '--config=x86_64-linux', '--config=asan_ubsan_lsan', '--', '//score/...'],
        ['test', '--lockfile_mode=error', '--config=x86_64-linux', '--config=asan_ubsan_lsan', '--', '//...'],
    ]


def test_instrumentation_config_does_not_leak_into_coverage_reporter(tmp_path, bazel):
    result = run_step('Execute Bazel commands', tmp_path, **bazel, TEST_TARGETS='//score/...',
                      TEST_COMMAND='coverage', BAZEL_CONFIGS='llvm_cov')
    assert result.returncode == 0, result.stderr
    commands = [json.loads(line)[1] for line in (tmp_path / 'commands').read_text().splitlines()]
    assert commands[0] == ['coverage', '--lockfile_mode=error', '--config=llvm_cov', '--', '//score/...']
    assert commands[1] == ['run', '--lockfile_mode=error', '@score_coverage//:generate_coverage_html', '--',
                            '--archive-dir', str(tmp_path / 'coverage-report')]


@pytest.mark.parametrize('configs', ['--config=linux', 'linux,tsan', 'linux $(touch marker)'])
def test_config_names_do_not_accept_flags_or_shell_expressions(tmp_path, configs):
    assert run_step('Validate request', tmp_path, TEST_TARGETS='//...', BAZEL_CONFIGS=configs).returncode != 0
    assert not (tmp_path / 'marker').exists()


def test_additional_flags_keep_lockfile_default_for_build_and_test(tmp_path, bazel):
    result = run_step('Execute Bazel commands', tmp_path, **bazel, BUILD_TARGETS='//score/...',
                      TEST_TARGETS='//...', BAZEL_ARGS='--verbose_failures')
    assert result.returncode == 0, result.stderr
    commands = [json.loads(line)[1] for line in (tmp_path / 'commands').read_text().splitlines()]
    assert [argv[:3] for argv in commands] == [
        ['build', '--lockfile_mode=error', '--verbose_failures'],
        ['test', '--lockfile_mode=error', '--verbose_failures'],
    ]


def test_explicit_test_lockfile_mode_overrides_only_the_test_default(tmp_path, bazel):
    result = run_step('Execute Bazel commands', tmp_path, **bazel, BUILD_TARGETS='//score/...',
                      TEST_TARGETS='//...', TEST_ARGS='--lockfile_mode=update')
    assert result.returncode == 0, result.stderr
    commands = [json.loads(line)[1] for line in (tmp_path / 'commands').read_text().splitlines()]
    assert commands == [
        ['build', '--lockfile_mode=error', '--', '//score/...'],
        ['test', '--lockfile_mode=error', '--lockfile_mode=update', '--', '//...'],
    ]


@pytest.mark.parametrize('failed, archive', [('coverage', False), ('run', False), ('run', True)])
def test_failed_coverage_uploads_test_results_unless_report_was_archived(tmp_path, bazel, failed, archive):
    """Retain diagnostics even when tests stop the reporter or the reporter cannot archive."""
    workspace = tmp_path / 'examples/integration'
    workspace.mkdir(parents=True)
    executable = tmp_path / 'bazel'
    executable.write_text('#!' + sys.executable + '\n' +
                          'import os, sys\n'
                          'from pathlib import Path\n'
                          'if sys.argv[1] == "coverage":\n'
                          '    logs = Path("bazel-testlogs/pkg/test")\n'
                          '    logs.mkdir(parents=True)\n'
                          '    (logs / "test.xml").write_text("<testsuite/>")\n'
                          '    (logs / "test.log").write_text("test output")\n'
                          'if sys.argv[1] == "run" and os.environ["CREATE_ARCHIVE"] == "true":\n'
                          '    report = Path(os.environ["COVERAGE_REPORT_DIRECTORY"])\n'
                          '    report.mkdir()\n'
                          '    (report / "index.html").write_text("coverage report")\n'
                          'sys.exit(23 if sys.argv[1] == os.environ["FAIL_COMMAND"] else 0)\n')
    prepared = run_step('Prepare workspace and report paths', tmp_path,
                        WORKING_DIRECTORY='examples/integration', TEST_COMMAND='coverage')
    assert prepared.returncode == 0, prepared.stderr
    prepared_paths = (tmp_path / 'output').read_text().splitlines()[2:-1]
    executed = run_step('Execute Bazel commands', workspace, **bazel,
                        TEST_TARGETS='//pkg:test', TEST_COMMAND='coverage',
                        FAIL_COMMAND=failed, CREATE_ARCHIVE=str(archive).lower())
    assert executed.returncode == 23
    (workspace / 'output').unlink(missing_ok=True)
    selected = run_step('Select coverage results', workspace,
                        TEST_RESULT_PATHS='\n'.join(prepared_paths))
    assert selected.returncode == 0, selected.stderr
    selected_paths = (workspace / 'output').read_text().splitlines()[1:-1]
    expected = [str(workspace / 'coverage-report')] if archive else prepared_paths
    assert selected_paths == expected
    if not archive:
        assert (workspace / 'bazel-testlogs/pkg/test/test.xml').is_file()
        assert (workspace / 'bazel-testlogs/pkg/test/test.log').is_file()
        assert selected_paths == [str(workspace / 'bazel-testlogs/**/*.xml'),
                                  str(workspace / 'bazel-testlogs/**/test.log')]


def test_empty_coverage_archive_falls_back_to_test_results(tmp_path):
    (tmp_path / 'coverage-report').mkdir()
    result = run_step('Select coverage results', tmp_path,
                      TEST_RESULT_PATHS=str(tmp_path / 'bazel-testlogs/**/*.xml'))
    assert result.returncode == 0, result.stderr
    assert (tmp_path / 'output').read_text().splitlines()[1:-1] == [
        str(tmp_path / 'bazel-testlogs/**/*.xml')]
