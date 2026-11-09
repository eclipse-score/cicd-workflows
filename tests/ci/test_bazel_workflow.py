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
                EVENT_NAME='pull_request', WORKING_DIRECTORY='.', ARTIFACT_NAME='',
                RUN_TARGET='', RUN_ARGS='', COMMAND_ENV='{}', TEST_COMMAND='test',
                RESULT_PATHS='bazel-testlogs/**/*.xml\nbazel-testlogs/**/test.log')


def run_step(name, root, **values):
    step = next(step for step in STEPS if step.get('name') == name)
    environment = {**os.environ, **DEFAULTS, 'GITHUB_WORKSPACE': str(root),
                   'GITHUB_OUTPUT': str(root / 'output'), **values}
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
                      TEST_TARGETS=test, BAZEL_ARGS='--lockfile_mode=error --config=linux',
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
    assert argv == ['test', '--test_env=VALUE=$(touch marker)', '--', '//...', '-//pkg:slow']
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


def test_run_passes_executable_arguments_and_environment_to_module_reporter(tmp_path, bazel):
    reporter = tmp_path / 'bazel'
    reporter.write_text('#!' + sys.executable + '\n' +
                        'import json, os, sys\n'
                        'from pathlib import Path\n'
                        'Path("report.json").write_text(json.dumps({"argv": sys.argv[1:], '
                        '"threshold": os.environ["COVERAGE_THRESHOLD"]}))\n')
    result = run_step('Execute Bazel commands', tmp_path, **bazel,
                      RUN_TARGET='@score_coverage//:generate_coverage_html',
                      RUN_ARGS='--output "coverage reports"',
                      BAZEL_ARGS='--config=llvm_cov', COMMAND_ENV='{"COVERAGE_THRESHOLD": "66"}')
    assert result.returncode == 0, result.stderr
    report = json.loads((tmp_path / 'report.json').read_text())
    assert report == {'argv': ['run', '--config=llvm_cov', '@score_coverage//:generate_coverage_html',
                               '--', '--output', 'coverage reports'], 'threshold': '66'}


def test_run_failure_keeps_exit_status_and_is_eligible_for_result_upload(tmp_path, bazel):
    values = {'RUN_TARGET': '@score_coverage//:generate_coverage_html'}
    assert run_step('Validate request', tmp_path, **values).returncode == 0
    assert (tmp_path / 'output').read_text() == 'has-results=true\n'
    result = run_step('Execute Bazel commands', tmp_path, **bazel, **values, FAIL_COMMAND='run')
    assert result.returncode == 23


def test_failed_build_stops_run_as_well_as_tests(tmp_path, bazel):
    result = run_step('Execute Bazel commands', tmp_path, **bazel, BUILD_TARGETS='//...',
                      RUN_TARGET='//:report', FAIL_COMMAND='build')
    assert result.returncode == 23
    commands = [json.loads(line)[1][0] for line in (tmp_path / 'commands').read_text().splitlines()]
    assert commands == ['build']


@pytest.mark.parametrize('values', [
    {'RUN_TARGET': '//:one //:two'}, {'RUN_TARGET': '--help'},
    {'BUILD_TARGETS': '//...', 'RUN_ARGS': '--output report'},
    {'RUN_TARGET': '//:report', 'COMMAND_ENV': '[]'},
    {'RUN_TARGET': '//:report', 'COMMAND_ENV': '{"COVERAGE_THRESHOLD": 66}'},
    {'RUN_TARGET': '//:report', 'COMMAND_ENV': 'not JSON'},
])
def test_invalid_run_requests_fail_before_setup(tmp_path, values):
    assert run_step('Validate request', tmp_path, **values).returncode != 0


def test_coverage_runs_before_shared_reporter_with_separate_flags(tmp_path, bazel):
    result = run_step('Execute Bazel commands', tmp_path, **bazel,
                      TEST_TARGETS='//score/... -//score:excluded', TEST_COMMAND='coverage',
                      TEST_ARGS='--config=llvm_cov --lockfile_mode=error --build_tests_only',
                      RUN_TARGET='@score_coverage//:generate_coverage_html',
                      RUN_ARGS='--yaml quality/coverage/coverage_justifications.yaml --archive-dir coverage_artifacts')
    assert result.returncode == 0, result.stderr
    commands = [json.loads(line)[1] for line in (tmp_path / 'commands').read_text().splitlines()]
    assert commands == [
        ['coverage', '--config=llvm_cov', '--lockfile_mode=error', '--build_tests_only',
         '--', '//score/...', '-//score:excluded'],
        ['run', '@score_coverage//:generate_coverage_html', '--',
         '--yaml', 'quality/coverage/coverage_justifications.yaml', '--archive-dir', 'coverage_artifacts'],
    ]


def test_coverage_failure_stops_reporter_and_keeps_failure_status(tmp_path, bazel):
    result = run_step('Execute Bazel commands', tmp_path, **bazel, TEST_TARGETS='//score/...',
                      TEST_COMMAND='coverage', RUN_TARGET='@score_coverage//:generate_coverage_html',
                      FAIL_COMMAND='coverage')
    assert result.returncode == 23
    commands = [json.loads(line)[1][0] for line in (tmp_path / 'commands').read_text().splitlines()]
    assert commands == ['coverage']


def test_test_phase_cannot_select_an_arbitrary_bazel_subcommand(tmp_path):
    result = run_step('Validate request', tmp_path, TEST_TARGETS='//...', TEST_COMMAND='clean')
    assert result.returncode != 0
