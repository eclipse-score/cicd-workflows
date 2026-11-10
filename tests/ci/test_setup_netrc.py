# Copyright (c) 2026 Contributors to the Eclipse Foundation
# SPDX-License-Identifier: Apache-2.0
"""Exercise credential file permissions and cleanup through the actual Node entry points."""

import os
from pathlib import Path
import stat
import subprocess

import pytest


ACTION = Path(__file__).resolve().parents[2] / '.github/actions/_setup-netrc'
CONTENT = ('machine packages.example.com login ci password first-token\n'
           'machine downloads.example.com login other password second-token\n')


@pytest.fixture
def runner(tmp_path):
    """Model runner command files and preserve credentials that the action does not own."""
    home = tmp_path / 'home'
    home.mkdir()
    (home / '.netrc').write_text('existing home credentials')
    temporary = tmp_path / 'runner temp'
    temporary.mkdir()
    environment = {**os.environ, 'HOME': str(home), 'RUNNER_TEMP': str(temporary),
                   'GITHUB_ENV': str(tmp_path / 'environment'),
                   'GITHUB_STATE': str(tmp_path / 'state'), 'INPUT_CONTENT': CONTENT,
                   'NETRC': str(home / '.netrc'), 'STATE_netrcDirectory': ''}

    def run(script, **values):
        return subprocess.run(['node', str(ACTION / script)], env={**environment, **values},
                              capture_output=True, text=True)

    return environment, run


def test_credentials_are_private_and_available_until_post_cleanup(runner):
    environment, run = runner
    result = run('setup.cjs')
    assert result.returncode == 0, result.stderr
    assert result.stdout == result.stderr == ''
    filename = Path(Path(environment['GITHUB_ENV']).read_text().removeprefix('NETRC=').strip())
    assert filename.read_text() == CONTENT
    assert filename.parent.parent == Path(environment['RUNNER_TEMP'])
    assert stat.S_IMODE(filename.stat().st_mode) == 0o600
    assert stat.S_IMODE(filename.parent.stat().st_mode) == 0o700
    state = Path(environment['GITHUB_STATE']).read_text()
    assert state == f'netrcDirectory={filename.parent}\n'

    # A failed job still invokes this same post entry point with the saved state.
    for _ in range(2):
        result = run('cleanup.cjs', STATE_netrcDirectory=str(filename.parent))
        assert result.returncode == 0, result.stderr
        assert not filename.parent.exists()
    assert Path(environment['NETRC']).read_text() == 'existing home credentials'


def test_omitted_secret_leaves_existing_authentication_unchanged(runner):
    environment, run = runner
    for script in ('setup.cjs', 'cleanup.cjs'):
        result = run(script, INPUT_CONTENT='')
        assert result.returncode == 0, result.stderr
    assert not Path(environment['GITHUB_ENV']).exists()
    assert not Path(environment['GITHUB_STATE']).exists()
    assert list(Path(environment['RUNNER_TEMP']).iterdir()) == []
    assert Path(environment['NETRC']).read_text() == 'existing home credentials'


@pytest.mark.parametrize('command_file', ['GITHUB_ENV', 'GITHUB_STATE'])
def test_setup_failure_removes_credentials_immediately(runner, command_file):
    """A runner command-file failure must not strand a credential file until job cleanup."""
    environment, run = runner
    result = run('setup.cjs', **{command_file: environment['RUNNER_TEMP']})
    assert result.returncode != 0
    assert 'first-token' not in result.stdout + result.stderr
    assert list(Path(environment['RUNNER_TEMP']).iterdir()) == []
    assert Path(environment['NETRC']).read_text() == 'existing home credentials'


def test_each_invocation_owns_a_separate_file(runner):
    """Cleaning one invocation cannot remove another invocation's credentials."""
    environment, run = runner
    files = []
    for _ in range(2):
        assert run('setup.cjs').returncode == 0
        files.append(Path(Path(environment['GITHUB_ENV']).read_text().splitlines()[-1][6:]))
    assert files[0] != files[1]
    assert run('cleanup.cjs', STATE_netrcDirectory=str(files[0].parent)).returncode == 0
    assert not files[0].exists()
    assert files[1].read_text() == CONTENT
    assert run('cleanup.cjs', STATE_netrcDirectory=str(files[1].parent)).returncode == 0


def test_secret_content_is_written_literally_without_shell_expansion(runner):
    environment, run = runner
    content = 'machine packages.example.com login ci password "$(echo injected)`date`%"\n'
    result = run('setup.cjs', INPUT_CONTENT=content)
    assert result.returncode == 0, result.stderr
    filename = Path(Path(environment['GITHUB_ENV']).read_text().removeprefix('NETRC=').strip())
    assert filename.read_text() == content
    assert result.stdout == result.stderr == ''
