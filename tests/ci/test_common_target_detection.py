# Copyright (c) 2026 Contributors to the Eclipse Foundation
# SPDX-License-Identifier: Apache-2.0
"""Execute the workflow's actual discovery script against a controlled Bazel CLI."""

import os
from pathlib import Path
import subprocess

import pytest
import yaml


@pytest.mark.parametrize('available, explicit, expected', [
    ('//:format.check //tools:format.check //:copyright.check', '',
     {'format-target': '//:format.check', 'copyright-target': '//:copyright.check'}),
    ('//tools:format.check //tools:copyright-check', '',
     {'format-target': '//tools:format.check', 'copyright-target': '//tools:copyright-check'}),
    ('//tools:format.check //tools:copyright.check //tools:copyright-check', '',
     {'format-target': '//tools:format.check', 'copyright-target': '//tools:copyright.check'}),
    ('//tools:format.check', '//custom:format', {'format-target': '', 'copyright-target': ''}),
    ('//:format.check //custom:format', '//custom:format',
     {'format-target': '//custom:format', 'copyright-target': ''}),
    ('', '', {'format-target': '', 'copyright-target': ''}),
])
def test_standard_target_discovery_prefers_root_and_respects_explicit_overrides(tmp_path, available, explicit, expected):
    root = Path(__file__).resolve().parents[2]
    workflow = yaml.safe_load((root / '.github/workflows/on-pr.yml').read_text())
    step = next(step for step in workflow['jobs']['pr-checks']['steps'] if step.get('id') == 'bazel')
    executable = tmp_path / 'bazel'
    executable.write_text('#!/bin/bash\nfor target in $AVAILABLE; do [[ "$2" == "$target" ]] && exit 0; done\nexit 1\n')
    executable.chmod(0o755)
    (tmp_path / 'pr-check-lib.sh').touch()
    output = tmp_path / 'output'
    env = {**os.environ, 'PATH': str(tmp_path) + os.pathsep + os.environ['PATH'],
           'RUNNER_TEMP': str(tmp_path), 'GITHUB_OUTPUT': str(output), 'AVAILABLE': available,
           'FORMAT_TARGET': explicit, 'COPYRIGHT_TARGET': ''}
    subprocess.run(['bash', '-e', '-c', step['run']], env=env, check=True)
    assert dict(line.split('=', 1) for line in output.read_text().splitlines()) == expected
