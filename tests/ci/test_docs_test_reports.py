# Copyright (c) 2026 Contributors to the Eclipse Foundation
# SPDX-License-Identifier: Apache-2.0
"""Keep artifact collection format-neutral and reject ambiguous report trees."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
ACTION_DIRECTORY = ROOT / '.github/actions/_merge-test-reports'
sys.path.insert(0, str(ACTION_DIRECTORY))

import merge_reports as reports


def artifact(root, name, files):
    directory = root / name
    directory.mkdir()
    for path, data in files.items():
        target = directory / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return name, directory


def test_preserves_bazel_pytest_and_log_files_with_distinct_relative_paths(tmp_path):
    """Collection must not depend on the filename convention of a Docs parser."""
    sources = [
        artifact(tmp_path, 'bazel', {'pkg/unit/test.xml': b'<testsuite/>', 'pkg/unit/test.log': b'log'}),
        artifact(tmp_path, 'pytest', {'docs_bzl_3.12_cached.xml': b'<testsuites/>', 'pytest/output.log': b'pytest'}),
        artifact(tmp_path, 'pytest-runtime', {'docs-bzl/3.14/runtime/test.xml': b'<testsuites/>'}),
    ]
    destination = tmp_path / 'merged'
    assert reports.merge(sources, destination) == 5
    for _name, directory in sources:
        for file in directory.rglob('*'):
            if file.is_file():
                assert (destination / file.relative_to(directory)).read_bytes() == file.read_bytes()


def test_accepts_identical_reports_and_rejects_different_results_at_same_path(tmp_path):
    first = artifact(tmp_path, 'first', {'pkg/test.xml': b'one'})
    second = artifact(tmp_path, 'second', {'pkg/test.xml': b'one'})
    assert reports.merge([first, second], tmp_path / 'merged') == 1
    (second[1] / 'pkg/test.xml').write_bytes(b'two')
    with pytest.raises(ValueError, match='Conflicting.*pkg/test.xml'):
        reports.merge([first, second], tmp_path / 'another')


def test_existing_consumer_reports_are_also_protected_from_overwrites(tmp_path):
    source = artifact(tmp_path, 'artifact', {'pkg/test.xml': b'new'})
    destination = tmp_path / 'merged'
    (destination / 'pkg').mkdir(parents=True)
    (destination / 'pkg/test.xml').write_bytes(b'committed')
    with pytest.raises(ValueError, match='Conflicting'):
        reports.merge([source], destination)
    assert (destination / 'pkg/test.xml').read_bytes() == b'committed'


@pytest.mark.parametrize('missing', [True, False])
def test_rejects_missing_or_empty_artifacts(tmp_path, missing):
    directory = tmp_path / 'selected'
    if not missing:
        directory.mkdir()
    with pytest.raises(ValueError, match='Missing|no files'):
        reports.merge([('selected', directory)], tmp_path / 'merged')


@pytest.mark.parametrize('location', ['root', 'source-file', 'source-directory', 'destination', 'destination-parent'])
def test_rejects_symlinks_without_touching_outside_files(tmp_path, location):
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'test.xml').write_bytes(b'outside')
    source = artifact(tmp_path, 'source', {'pkg/test.xml': b'inside'})
    destination = tmp_path / 'merged'
    destination.mkdir()
    if location == 'root':
        link = tmp_path / 'linked-source'
        link.symlink_to(outside, target_is_directory=True)
        source = ('source', link)
    elif location == 'source-file':
        (source[1] / 'linked.xml').symlink_to(outside / 'test.xml')
    elif location == 'source-directory':
        (source[1] / 'linked-directory').symlink_to(outside, target_is_directory=True)
    elif location == 'destination':
        (destination / 'pkg').mkdir()
        (destination / 'pkg/test.xml').symlink_to(outside / 'test.xml')
    else:
        (destination / 'pkg').symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match='symlink|Conflicting'):
        reports.merge([source], destination)
    assert (outside / 'test.xml').read_bytes() == b'outside'


def test_rejects_existing_symlinks_unrelated_to_incoming_reports(tmp_path):
    """The Docs parser must not see stale symlinks after a successful merge."""
    source = artifact(tmp_path, 'source', {'new.xml': b'new'})
    destination = tmp_path / 'merged'
    destination.mkdir()
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'test.xml').write_bytes(b'outside')
    (destination / 'stale.xml').symlink_to(outside / 'test.xml')
    with pytest.raises(ValueError, match='Existing.*symlink'):
        reports.merge([source], destination)
    assert not (destination / 'new.xml').exists()


def test_rejects_symlink_in_destination_ancestor_before_creating_directories(tmp_path):
    outside = tmp_path / 'outside'
    outside.mkdir()
    linked_parent = tmp_path / 'linked-parent'
    linked_parent.symlink_to(outside, target_is_directory=True)
    source = artifact(tmp_path, 'source', {'test.xml': b'report'})
    with pytest.raises(ValueError, match='destination path.*symlink'):
        reports.merge([source], linked_parent / 'new' / 'reports')
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize('reverse', [False, True])
def test_rejects_file_directory_conflicts(tmp_path, reverse):
    sources = [artifact(tmp_path, 'file', {'pkg': b'file'}),
               artifact(tmp_path, 'directory', {'pkg/test.xml': b'report'})]
    with pytest.raises(ValueError, match='Conflicting'):
        reports.merge(sources[::-1] if reverse else sources, tmp_path / 'merged')


@pytest.mark.parametrize('value', ['{}', 'null', '[]', '["same", "same"]', '[1]', '["../outside"]', '["*"]', '["."]', '[""]'])
def test_rejects_invalid_or_pattern_artifact_names(value):
    with pytest.raises(ValueError):
        reports.artifact_names(value)


def test_action_selects_only_requested_artifacts_and_combines_their_files(tmp_path):
    """Run the actual preparation and merge steps around a simulated download."""
    names = ['bazel-linux', 'pytest-runtime']
    output = tmp_path / 'output'
    action = yaml.safe_load((ACTION_DIRECTORY / 'action.yml').read_text())
    prepare = next(step for step in action['runs']['steps'] if step.get('id') == 'selection')
    subprocess.run(['bash', '-e', '-c', prepare['run']], check=True, env={
        **os.environ, 'RUNNER_TEMP': str(tmp_path), 'GITHUB_OUTPUT': str(output),
    })
    prepared = dict(line.split('=', 1) for line in output.read_text().splitlines())
    downloads = Path(prepared['downloads'])
    assert downloads.is_dir()
    artifact(downloads, names[0], {'pkg/test.xml': b'one'})
    artifact(downloads, names[1], {'pytest.xml': b'two'})
    artifact(downloads, 'unrelated', {'secret.txt': b'unselected'})
    destination = tmp_path / 'merged'
    merge = action['runs']['steps'][-1]
    subprocess.run(['bash', '-e', '-c', merge['run']], check=True, env={
        **os.environ, 'ACTION_DIRECTORY': str(ACTION_DIRECTORY), 'ARTIFACTS': json.dumps(names),
        'DOWNLOADS': str(downloads), 'DESTINATION': str(destination),
    })
    assert sorted(path.relative_to(destination).as_posix() for path in destination.rglob('*') if path.is_file()) == ['pkg/test.xml', 'pytest.xml']


def test_cli_accepts_single_artifact_extracted_directly_into_download_root(tmp_path):
    """Match download-artifact v8's ordinary one-artifact layout."""
    downloads = tmp_path / 'downloads'
    downloads.mkdir()
    (downloads / 'test.xml').write_bytes(b'root report')
    (downloads / 'test.log').write_bytes(b'log')
    destination = tmp_path / 'merged'
    subprocess.run([sys.executable, str(ACTION_DIRECTORY / 'merge_reports.py'),
                    '--artifacts', '["only-report"]', '--downloads', str(downloads),
                    '--destination', str(destination)], check=True)
    assert (destination / 'test.xml').read_bytes() == b'root report'
    assert (destination / 'test.log').read_bytes() == b'log'


@pytest.mark.parametrize('names', [['missing'], ['present', 'missing']])
def test_cli_rejects_missing_selection_even_when_one_match_is_flattened(tmp_path, names):
    downloads = tmp_path / 'downloads'
    downloads.mkdir()
    if len(names) > 1:
        (downloads / 'test.xml').write_bytes(b'only present artifact was downloaded')
    result = subprocess.run([sys.executable, str(ACTION_DIRECTORY / 'merge_reports.py'),
                             '--artifacts', json.dumps(names), '--downloads', str(downloads),
                             '--destination', str(tmp_path / 'merged')], capture_output=True, text=True)
    assert result.returncode != 0
    assert 'no files' in result.stderr or 'Missing test artifact directory' in result.stderr


@pytest.mark.parametrize('single, multiple, success, selected', [
    ('', '[]', True, False), ('legacy', '[]', True, False),
    ('', '[ ]', True, False), ('', '["one", "two"]', True, True),
    ('legacy', '["one"]', False, False), ('', '{}', False, False),
    ('', 'invalid-json', False, False),
])
def test_workflow_preserves_legacy_input_and_rejects_ambiguous_selection(tmp_path, single, multiple, success, selected):
    workflow = yaml.safe_load((ROOT / '.github/workflows/docs.yml').read_text())
    step = next(step for step in workflow['jobs']['docs-build']['steps'] if step.get('id') == 'report-inputs')
    output = tmp_path / 'output'
    result = subprocess.run(['bash', '-e', '-c', step['run']], env={
        **os.environ, 'SINGLE_REPORT': single, 'REPORTS': multiple, 'GITHUB_OUTPUT': str(output),
    }, capture_output=True, text=True)
    assert (result.returncode == 0) == success
    if success:
        assert output.read_text() == f'multiple={str(selected).lower()}\n'
