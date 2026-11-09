# Copyright (c) 2026 Contributors to the Eclipse Foundation
# SPDX-License-Identifier: Apache-2.0
"""Run the workflow's Gitlint scripts against real, shallow PR histories."""

import os
from pathlib import Path
import shlex
import subprocess
import sys

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
STEPS = yaml.safe_load((ROOT / '.github/workflows/on-pr.yml').read_text())['jobs']['pr-checks']['steps']


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], text=True, stderr=subprocess.DEVNULL).strip()


@pytest.mark.parametrize(('event', 'repository', 'ref', 'expected'), [
    ('pull_request', '', '', True),
    ('pull_request', 'example/module', '', True),
    ('pull_request', 'example/other', '', False),
    ('pull_request', '', 'a' * 40, False),
    ('push', '', '', False),
    ('merge_group', '', '', False),
    ('workflow_dispatch', '', '', False),
])
@pytest.mark.parametrize('config_kind', ['missing', 'file', 'directory'])
def test_detects_local_gitlint_config_for_the_callers_pr(tmp_path, event, repository, ref, expected, config_kind):
    """A local config is required; unrelated event histories remain inapplicable."""
    if config_kind == 'file':
        (tmp_path / '.gitlint').write_text('[general]\n')
    elif config_kind == 'directory':
        (tmp_path / '.gitlint').mkdir()
    expected = expected and config_kind == 'file'
    helper = next(step['run'] for step in STEPS if step.get('name') == '🧰 Prepare shared check helper')
    detect = next(step['run'] for step in STEPS if step.get('id') == 'detect')
    output = tmp_path / 'output'
    subprocess.run(['bash', '-e', '-c', helper + '\n' + detect], cwd=tmp_path, check=True, env={
        **os.environ, 'RUNNER_TEMP': str(tmp_path), 'GITHUB_OUTPUT': str(output),
        'EVENT_NAME': event, 'CHECK_REPOSITORY': repository, 'CHECK_REF': ref,
        'CALLER_REPOSITORY': 'example/module',
    }, capture_output=True, text=True)
    assert f'has_gitlint={str(expected).lower()}' in output.read_text().splitlines()


@pytest.mark.parametrize('length, limit, valid', [(26, 72, True), (90, 72, False), (90, 100, True)])
def test_lints_real_pr_commits_with_repository_policy_from_shallow_merge_checkout(tmp_path, length, limit, valid):
    """The PR head is checked even though HEAD is a different merge commit.

    Uvx uses the installed pinned Gitlint. Git fetching, range selection and
    linting run unchanged. The same long title passes or fails according to
    the repository's own configured title limit.
    """
    source = tmp_path / 'source'
    source.mkdir()
    git(source, 'init', '-b', 'main')
    git(source, 'config', 'user.name', 'Test')
    git(source, 'config', 'user.email', 'test@example.com')
    git(source, 'commit', '--allow-empty', '-m', 'Initial commit')
    base = git(source, 'rev-parse', 'HEAD')
    git(source, 'checkout', '-b', 'pr')
    message = 'x' * length
    git(source, 'commit', '--allow-empty', '-m', message)
    head = git(source, 'rev-parse', 'HEAD')
    git(source, 'checkout', 'main')
    git(source, 'commit', '--allow-empty', '-m', 'chore: advance base branch')
    git(source, 'merge', '--no-ff', 'pr', '-m', 'Merge PR')
    checkout = tmp_path / 'checkout'
    subprocess.run(['git', 'clone', '--depth=1', source.as_uri(), str(checkout)], check=True, capture_output=True)
    assert git(checkout, 'rev-parse', 'HEAD') != head
    assert git(checkout, 'rev-parse', '--is-shallow-repository') == 'true'
    (checkout / '.gitlint').write_text(
        f'[general]\nignore=B6\n[title-max-length]\nline-length={limit}\n'
    )
    executables = tmp_path / 'bin'
    executables.mkdir()
    (executables / 'uvx').write_text(
        '#!/bin/bash\nset -eu\n'
        '[[ "$1" == --from && "$2" == gitlint==0.19.1 && "$3" == gitlint ]]\n'
        'shift 3\nprintf "%s\\n" "$@" > "$TEST_ARGV"\n'
        f'exec {shlex.quote(str(Path(sys.executable).parent / "gitlint"))} "$@"\n'
    )
    for path in executables.iterdir():
        path.chmod(0o755)
    helper = next(step['run'] for step in STEPS if step.get('name') == '🧰 Prepare shared check helper')
    script = next(step['run'] for step in STEPS if step.get('id') == 'gitlint')
    result = subprocess.run(['bash', '-e', '-c', helper + '\n' + script], cwd=checkout, env={
        **os.environ, 'PATH': str(executables) + os.pathsep + os.environ['PATH'],
        'RUNNER_TEMP': str(tmp_path), 'PR_BASE_SHA': base, 'PR_HEAD_SHA': head,
        'TEST_ARGV': str(tmp_path / 'argv'),
    }, capture_output=True, text=True)
    assert (result.returncode == 0) == valid, result.stdout + result.stderr
    assert (tmp_path / 'argv').read_text().splitlines() == [
        '--config', '.gitlint', '--commits', f'{base}..{head}',
    ]
    assert git(checkout, 'rev-parse', '--is-shallow-repository') == 'false'
    if not valid:
        assert 'PR commit messages do not follow the repository Gitlint configuration' in result.stdout
