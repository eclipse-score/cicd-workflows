# Copyright (c) 2026 Contributors to the Eclipse Foundation
# SPDX-License-Identifier: Apache-2.0
"""Keep artifact collection format-neutral and reject ambiguous report trees."""

import os
from pathlib import Path
import subprocess
import sys
import stat
from zipfile import ZipFile, ZipInfo

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


@pytest.mark.parametrize('value', ['', '  \n  ', '[]', '["one", "two"]', '{}', '../outside', 'one?two', '[abc]', '.', '..', 'one|two'])
def test_rejects_json_and_unsupported_pattern_syntax(value):
    with pytest.raises(ValueError):
        reports.artifact_patterns(value)


def test_multiline_patterns_ignore_blank_lines_and_surrounding_whitespace():
    assert reports.artifact_patterns('  bazel-linux \n\n pytest-* \r\n') == ['bazel-linux', 'pytest-*']



def download(root, relative, files):
    """Model download-artifact's raw ZIP output without depending on ZIP names."""
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(target, 'w') as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return target


def run_merge(downloads, destination, output):
    action = yaml.safe_load((ACTION_DIRECTORY / 'action.yml').read_text())
    merge = next(step for step in action['runs']['steps'] if step.get('id') == 'merge')
    return subprocess.run(['bash', '-e', '-c', merge['run']], capture_output=True, text=True, env={
        'PATH': os.environ['PATH'], 'ACTION_DIRECTORY': str(ACTION_DIRECTORY),
        'DOWNLOADS': str(downloads), 'DESTINATION': str(destination), 'GITHUB_OUTPUT': str(output),
    })


@pytest.mark.parametrize('selectors, pattern', [
    ('bazel-linux\npytest-*', '@(bazel-linux|pytest-*)'),
    (' tests-* \n\n *-linux\ntests-*', '@(tests-*|*-linux)'),
    ('-report', '@(-report)'), ('-report*', '@(-report*)'),
])
def test_action_prepares_a_download_pattern_without_listing_artifacts(tmp_path, selectors, pattern):
    action = yaml.safe_load((ACTION_DIRECTORY / 'action.yml').read_text())
    prepare = next(step for step in action['runs']['steps'] if step.get('id') == 'selection')
    output = tmp_path / 'outputs'
    result = subprocess.run(['bash', '-e', '-c', prepare['run']], capture_output=True, text=True, env={
        'PATH': os.environ['PATH'], 'ACTION_DIRECTORY': str(ACTION_DIRECTORY),
        'ARTIFACTS': selectors, 'RUNNER_TEMP': str(tmp_path), 'GITHUB_OUTPUT': str(output),
    })
    assert result.returncode == 0, result.stderr
    prepared = dict(line.split('=', 1) for line in output.read_text().splitlines())
    assert prepared['pattern'] == pattern
    assert Path(prepared['downloads']).is_dir()
    downloader = next(step for step in action['runs']['steps'] if 'actions/download-artifact@' in step.get('uses', ''))
    assert downloader['with']['pattern'] == '${{ steps.selection.outputs.pattern }}'
    assert downloader['with']['skip-decompress'] is True
    assert downloader['with']['merge-multiple'] is False
    assert 'artifact-ids' not in downloader['with']


def test_merges_downloaded_archives_and_preserves_relative_paths_and_logs(tmp_path):
    downloads = tmp_path / 'downloads'
    download(downloads, 'bazel-linux/artifact.zip', {'pkg/unit/test.xml': b'unit', 'pkg/unit/test.log': b'log'})
    download(downloads, 'pytest-runtime/artifact.zip', {'docs_bzl_3.12_runtime.xml': b'pytest'})
    destination = tmp_path / 'merged'
    result = run_merge(downloads, destination, tmp_path / 'outputs')
    assert result.returncode == 0, result.stderr
    assert sorted(p.relative_to(destination).as_posix() for p in destination.rglob('*') if p.is_file()) == [
        'docs_bzl_3.12_runtime.xml', 'pkg/unit/test.log', 'pkg/unit/test.xml',
    ]
    assert (destination / 'pkg/unit/test.log').read_bytes() == b'log'


def test_single_match_uses_archive_contents_without_guessing_artifact_directory_names(tmp_path):
    """The flattened ZIP can contain directories that resemble artifact selectors."""
    downloads = tmp_path / 'downloads'
    download(downloads, 'arbitrary-archive-name.zip', {
        'tests-unit/pkg/test.xml': b'unit', 'tests-integration/pkg/test.xml': b'integration',
    })
    destination = tmp_path / 'merged'
    assert reports.merge_downloads(downloads, destination) == 2
    assert (destination / 'tests-unit/pkg/test.xml').read_bytes() == b'unit'
    assert (destination / 'tests-integration/pkg/test.xml').read_bytes() == b'integration'


def test_partially_matching_selection_keeps_the_reports_that_were_downloaded(tmp_path):
    """An unmatched input line is allowed when another selector downloads reports."""
    downloads = tmp_path / 'downloads'
    download(downloads, 'only-match.zip', {'unit/test.xml': b'present'})
    destination = tmp_path / 'merged'
    result = run_merge(downloads, destination, tmp_path / 'outputs')
    assert result.returncode == 0, result.stderr
    assert (destination / 'unit/test.xml').read_bytes() == b'present'


@pytest.mark.parametrize('identical', [False, True])
def test_archive_duplicates_preserve_identical_files_and_reject_conflicts(tmp_path, identical):
    downloads = tmp_path / 'downloads'
    download(downloads, 'first/artifact.zip', {'unit/test.xml': b'one'})
    download(downloads, 'second/artifact.zip', {'unit/test.xml': b'one' if identical else b'two'})
    destination = tmp_path / 'merged'
    if identical:
        assert reports.merge_downloads(downloads, destination) == 1
    else:
        with pytest.raises(reports.CollectionError, match='Conflicting test report file'):
            reports.merge_downloads(downloads, destination)
    assert (destination / 'unit/test.xml').read_bytes() == b'one'


@pytest.mark.parametrize('filename', ['../outside/test.xml', '/absolute/test.xml', 'dir/../../outside/test.xml', 'dir\\test.xml'])
def test_rejects_unsafe_archive_paths_before_extracting(tmp_path, filename):
    downloads = tmp_path / 'downloads'
    download(downloads, 'artifact.zip', {filename: b'outside'})
    with pytest.raises(ValueError, match='Unsafe'):
        reports.merge_downloads(downloads, tmp_path / 'merged')
    assert not (tmp_path / 'outside').exists()
    assert not (tmp_path / 'merged').exists()


def test_rejects_symlinks_stored_in_archives(tmp_path):
    downloads = tmp_path / 'downloads'
    downloads.mkdir()
    with ZipFile(downloads / 'artifact.zip', 'w') as archive:
        entry = ZipInfo('linked.xml')
        entry.create_system = 3
        entry.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(entry, '/outside/test.xml')
    with pytest.raises(ValueError, match='symlink'):
        reports.merge_downloads(downloads, tmp_path / 'merged')


def test_rejects_symlinks_in_the_download_tree(tmp_path):
    downloads = tmp_path / 'downloads'
    downloads.mkdir()
    outside = download(tmp_path, 'outside.zip', {'test.xml': b'outside'})
    (downloads / 'linked.zip').symlink_to(outside)
    with pytest.raises(ValueError, match='symlink'):
        reports.merge_downloads(downloads, tmp_path / 'merged')


@pytest.mark.parametrize('same', [True, False])
def test_duplicate_zip_entries_cannot_silently_replace_a_report(tmp_path, same):
    downloads = tmp_path / 'downloads'
    downloads.mkdir()
    with ZipFile(downloads / 'artifact.zip', 'w') as archive:
        archive.writestr('unit/test.xml', b'one')
        with pytest.warns(UserWarning, match='Duplicate name'):
            archive.writestr('unit/test.xml', b'one' if same else b'two')
    destination = tmp_path / 'merged'
    if same:
        assert reports.merge_downloads(downloads, destination) == 1
    else:
        with pytest.raises(reports.CollectionError, match='Conflicting'):
            reports.merge_downloads(downloads, destination)
        assert not destination.exists()


@pytest.mark.parametrize('alias', ['unit//test.xml', 'unit/./test.xml'])
def test_equivalent_zip_paths_cannot_bypass_conflict_detection(tmp_path, alias):
    """ZIP extraction normalizes separators and dot components to one file."""
    downloads = tmp_path / 'downloads'
    download(downloads, 'artifact.zip', {'unit/test.xml': b'one', alias: b'two'})
    with pytest.raises(reports.CollectionError, match='Conflicting'):
        reports.merge_downloads(downloads, tmp_path / 'merged')
    assert not (tmp_path / 'merged').exists()


def test_empty_archives_do_not_produce_a_successful_documentation_report(tmp_path):
    downloads = tmp_path / 'downloads'
    download(downloads, 'empty.zip', {})
    with pytest.raises(ValueError, match='no files'):
        reports.merge_downloads(downloads, tmp_path / 'merged')


def verify_failure_scenario(scenario, error_code):
    workflow = yaml.safe_load((ROOT / '.github/workflows/_test_merge_test_reports.yml').read_text())
    verify = workflow['jobs']['reports']['steps'][-1]['run']
    return subprocess.run(['bash', '-e', '-c', verify], capture_output=True, text=True, env={
        'PATH': os.environ['PATH'], 'SCENARIO': scenario, 'OUTCOME': 'failure', 'ERROR_CODE': error_code,
    })


@pytest.mark.parametrize('scenario', ['missing-name', 'missing-pattern'])
def test_no_downloads_emit_the_specific_error_checked_by_the_selftest(tmp_path, scenario):
    downloads = tmp_path / 'downloads'
    downloads.mkdir()
    output = tmp_path / 'outputs'
    result = run_merge(downloads, tmp_path / 'merged', output)
    assert result.returncode != 0
    assert 'No test report artifacts were downloaded' in result.stderr
    assert output.read_text() == 'error-code=no-reports\n'
    assert verify_failure_scenario(scenario, 'no-reports').returncode == 0


def test_conflicting_files_emit_the_specific_error_checked_by_the_selftest(tmp_path):
    downloads = tmp_path / 'downloads'
    download(downloads, 'first/artifact.zip', {'unit/test.xml': b'one'})
    download(downloads, 'second/artifact.zip', {'unit/test.xml': b'two'})
    output = tmp_path / 'outputs'
    result = run_merge(downloads, tmp_path / 'merged', output)
    assert result.returncode != 0
    assert output.read_text() == 'error-code=file-conflict\n'
    assert verify_failure_scenario('conflict', 'file-conflict').returncode == 0


def test_corrupt_download_does_not_pass_as_an_expected_missing_report(tmp_path):
    downloads = tmp_path / 'downloads'
    downloads.mkdir()
    (downloads / 'broken.zip').write_bytes(b'not an archive')
    output = tmp_path / 'outputs'
    result = run_merge(downloads, tmp_path / 'merged', output)
    assert result.returncode != 0
    assert 'BadZipFile' in result.stderr
    assert not output.exists()
    assert verify_failure_scenario('missing-pattern', '').returncode != 0


@pytest.mark.parametrize('scenario', ['conflict', 'missing-name', 'missing-pattern'])
@pytest.mark.parametrize('error_code', ['', 'unexpected-error'])
def test_negative_selftests_reject_unexpected_failure_reasons(scenario, error_code):
    assert verify_failure_scenario(scenario, error_code).returncode != 0


@pytest.mark.parametrize('single, multiple, success, selected', [
    ('', '', True, False), ('legacy', '', True, False),
    ('', ' \n ', True, False), ('', 'one\ntwo-*', True, True),
    ('legacy', 'one', False, False), ('legacy', 'one-*', False, False),
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
