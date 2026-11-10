# Copyright (c) 2026 Contributors to the Eclipse Foundation
# SPDX-License-Identifier: Apache-2.0
"""Keep artifact collection format-neutral and reject ambiguous report trees."""

import base64
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

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


def test_star_patterns_match_case_sensitively_and_overlaps_download_once():
    artifacts = [{'name': name, 'id': index} for index, name in enumerate([
        'bazel-linux', 'pytest-cached', 'pytest-runtime', 'Pytest-other', 'unrelated',
    ], 1)]
    selected = reports.select_artifacts(['bazel-linux', 'pytest-*', '*-cached', 'pytest-*'], artifacts)
    assert [artifact['name'] for artifact in selected] == ['bazel-linux', 'pytest-cached', 'pytest-runtime']


@pytest.mark.parametrize('patterns', [['absent-*'], ['bazel-linux', 'absent-*'], ['bazel-*', 'absent']])
def test_every_selector_requires_a_match_even_if_another_selector_matches(patterns):
    with pytest.raises(ValueError, match='No test artifacts match: absent'):
        reports.select_artifacts(patterns, [{'name': 'bazel-linux', 'id': 1}])


def test_reruns_select_only_the_latest_artifact_with_each_name():
    assert reports.select_artifacts(['unit-*'], [
        {'name': 'unit-linux', 'id': 3}, {'name': 'unit-linux', 'id': 1},
    ]) == [{'name': 'unit-linux', 'id': 3}]


@pytest.mark.parametrize('name', ['../outside', '.', '..', 'name/child'])
def test_wildcards_cannot_select_names_that_escape_download_directories(name):
    with pytest.raises(ValueError, match='Unsupported test artifact name'):
        reports.select_artifacts(['*'], [{'name': name, 'id': 1}])


def test_lists_run_scoped_artifacts_without_additional_github_token_permissions(monkeypatch):
    payload = base64.urlsafe_b64encode(json.dumps({'scp': 'Other.Scope Actions.Results:run-id:job-id'}).encode()).decode().rstrip('=')
    token = f'header.{payload}.signature'
    monkeypatch.setenv('ACTIONS_RUNTIME_TOKEN', token)
    monkeypatch.setenv('ACTIONS_RESULTS_URL', 'https://artifacts.example/')

    def open_request(request, timeout):
        assert request.full_url == 'https://artifacts.example/twirp/github.actions.results.api.v1.ArtifactService/ListArtifacts'
        assert request.get_header('Authorization') == f'Bearer {token}'
        assert json.loads(request.data) == {'workflow_run_backend_id': 'run-id', 'workflow_job_run_backend_id': 'job-id'}
        assert timeout == 60
        return io.StringIO(json.dumps({'artifacts': [{'name': 'unit-linux', 'database_id': '42'}]}))

    monkeypatch.setattr(reports, 'urlopen', open_request)
    assert reports.list_artifacts() == [{'name': 'unit-linux', 'id': 42}]


@contextmanager
def artifact_service(names, artifacts=None):
    """Serve the real action helper's metadata request without GitHub credentials."""
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            assert self.path.endswith('.ArtifactService/ListArtifacts')
            request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            if request != {'workflow_run_backend_id': 'run', 'workflow_job_run_backend_id': 'job'}:
                self.send_error(400, 'Expected protobuf field names')
                return
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps({'artifacts': artifacts if artifacts is not None else [
                {'name': name, 'database_id': str(index)} for index, name in enumerate(names, 1)
            ]}).encode())

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    payload = base64.urlsafe_b64encode(json.dumps({'scp': 'Actions.Results:run:job'}).encode()).decode().rstrip('=')
    try:
        yield {'ACTIONS_RUNTIME_TOKEN': f'header.{payload}.signature',
               'ACTIONS_RESULTS_URL': f'http://127.0.0.1:{server.server_port}'}
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


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
    selection = next(step for step in action['runs']['steps'] if step.get('id') == 'reports')
    # Exercise the JavaScript wrapper, environment handoff, service request and
    # selector together. An unrelated artifact must never reach the download IDs.
    selection_output = tmp_path / 'selection-output'
    with artifact_service([*names, 'unrelated']) as runtime_env:
        subprocess.run(['node', '-e', selection['with']['script']], check=True, env={
            'PATH': os.environ['PATH'], **runtime_env, 'ACTION_DIRECTORY': str(ACTION_DIRECTORY),
            'ARTIFACTS': 'bazel-*\npytest-*\nbazel-linux', 'GITHUB_OUTPUT': str(selection_output),
        })
    selected = dict(line.split('=', 1) for line in selection_output.read_text().splitlines())
    assert selected['ids'] == '1,2'
    assert json.loads(selected['names']) == names
    artifact(downloads, names[0], {'pkg/test.xml': b'one'})
    artifact(downloads, names[1], {'pytest.xml': b'two'})
    artifact(downloads, 'unrelated', {'secret.txt': b'unselected'})
    destination = tmp_path / 'merged'
    merge = action['runs']['steps'][-1]
    subprocess.run(['bash', '-e', '-c', merge['run']], check=True, env={
        **os.environ, 'ACTION_DIRECTORY': str(ACTION_DIRECTORY), 'NAMES': json.dumps(names),
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
    subprocess.run([sys.executable, str(ACTION_DIRECTORY / 'merge_reports.py'), 'merge',
                    '--names', '["only-report"]', '--downloads', str(downloads),
                    '--destination', str(destination)], check=True)
    assert (destination / 'test.xml').read_bytes() == b'root report'
    assert (destination / 'test.log').read_bytes() == b'log'


@pytest.mark.parametrize('names', [['missing'], ['present', 'missing']])
def test_cli_rejects_missing_selection_even_when_one_match_is_flattened(tmp_path, names):
    downloads = tmp_path / 'downloads'
    downloads.mkdir()
    if len(names) > 1:
        (downloads / 'test.xml').write_bytes(b'only present artifact was downloaded')
    result = subprocess.run([sys.executable, str(ACTION_DIRECTORY / 'merge_reports.py'), 'merge',
                             '--names', json.dumps(names), '--downloads', str(downloads),
                             '--destination', str(tmp_path / 'merged')], capture_output=True, text=True)
    assert result.returncode != 0
    assert 'no files' in result.stderr or 'Missing test artifact directory' in result.stderr


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


def verify_failure_scenario(scenario, error_code):
    workflow = yaml.safe_load((ROOT / '.github/workflows/_test_merge_test_reports.yml').read_text())
    verify = workflow['jobs']['reports']['steps'][-1]['run']
    return subprocess.run(['bash', '-e', '-c', verify], capture_output=True, text=True, env={
        'PATH': os.environ['PATH'], 'SCENARIO': scenario, 'OUTCOME': 'failure', 'ERROR_CODE': error_code,
    })


@pytest.mark.parametrize('scenario, patterns', [
    ('missing', 'present\nmissing'),
    ('missing-pattern', 'absent-*'),
    ('partial-missing-pattern', 'present\nabsent-*'),
])
def test_missing_selection_emits_the_reason_checked_by_the_real_selftest(tmp_path, scenario, patterns):
    action = yaml.safe_load((ACTION_DIRECTORY / 'action.yml').read_text())
    selection = next(step for step in action['runs']['steps'] if step.get('id') == 'reports')
    output = tmp_path / 'output'
    with artifact_service(['present']) as runtime_env:
        result = subprocess.run(['node', '-e', selection['with']['script']], capture_output=True, text=True, env={
            'PATH': os.environ['PATH'], **runtime_env, 'ACTION_DIRECTORY': str(ACTION_DIRECTORY),
            'ARTIFACTS': patterns, 'GITHUB_OUTPUT': str(output),
        })
    assert result.returncode != 0
    assert 'No test artifacts match:' in result.stderr
    assert output.read_text() == 'error-code=artifact-not-found\n'
    assert verify_failure_scenario(scenario, 'artifact-not-found').returncode == 0


def test_conflicting_files_emit_the_reason_checked_by_the_real_selftest(tmp_path):
    downloads = tmp_path / 'downloads'
    downloads.mkdir()
    artifact(downloads, 'first', {'unit/test.xml': b'one'})
    artifact(downloads, 'second', {'unit/test.xml': b'two'})
    output = tmp_path / 'output'
    action = yaml.safe_load((ACTION_DIRECTORY / 'action.yml').read_text())
    merge = next(step for step in action['runs']['steps'] if step.get('id') == 'merge')
    result = subprocess.run(['bash', '-e', '-c', merge['run']], capture_output=True, text=True, env={
        'PATH': os.environ['PATH'], 'ACTION_DIRECTORY': str(ACTION_DIRECTORY),
        'NAMES': '["first", "second"]', 'DOWNLOADS': str(downloads),
        'DESTINATION': str(tmp_path / 'merged'), 'GITHUB_OUTPUT': str(output),
    })
    assert result.returncode != 0
    assert 'Conflicting test report file: unit/test.xml' in result.stderr
    assert output.read_text() == 'error-code=file-conflict\n'
    assert verify_failure_scenario('conflict', 'file-conflict').returncode == 0


def test_unexpected_service_schema_failure_is_not_a_successful_negative_test(tmp_path):
    """A runtime KeyError must not be reported as an expected missing artifact."""
    action = yaml.safe_load((ACTION_DIRECTORY / 'action.yml').read_text())
    selection = next(step for step in action['runs']['steps'] if step.get('id') == 'reports')
    output = tmp_path / 'output'
    with artifact_service([], artifacts=[{'name': 'present', 'databaseId': '1'}]) as runtime_env:
        result = subprocess.run(['node', '-e', selection['with']['script']], capture_output=True, text=True, env={
            'PATH': os.environ['PATH'], **runtime_env, 'ACTION_DIRECTORY': str(ACTION_DIRECTORY),
            'ARTIFACTS': 'absent-*', 'GITHUB_OUTPUT': str(output),
        })
    assert result.returncode != 0
    assert "KeyError: 'database_id'" in result.stderr
    assert not output.exists()
    assert verify_failure_scenario('missing-pattern', '').returncode != 0


@pytest.mark.parametrize('scenario', ['conflict', 'missing', 'missing-pattern', 'partial-missing-pattern'])
@pytest.mark.parametrize('error_code', ['', 'unexpected-error'])
def test_negative_selftests_reject_unspecified_or_unexpected_failure_reasons(scenario, error_code):
    assert verify_failure_scenario(scenario, error_code).returncode != 0


@pytest.mark.parametrize('selector', ['-report', '-report*'])
def test_action_passes_hyphen_prefixed_selectors_as_values(tmp_path, selector):
    """Valid names and patterns beginning with '-' must reach artifact selection."""
    action = yaml.safe_load((ACTION_DIRECTORY / 'action.yml').read_text())
    selection = next(step for step in action['runs']['steps'] if step.get('id') == 'reports')
    output = tmp_path / 'output'
    with artifact_service(['-report', 'unrelated']) as runtime_env:
        result = subprocess.run(['node', '-e', selection['with']['script']], capture_output=True, text=True, env={
            'PATH': os.environ['PATH'], **runtime_env, 'ACTION_DIRECTORY': str(ACTION_DIRECTORY),
            'ARTIFACTS': selector, 'GITHUB_OUTPUT': str(output),
        })
    assert result.returncode == 0, result.stderr
    selected = dict(line.split('=', 1) for line in output.read_text().splitlines())
    assert selected['ids'] == '1'
    assert json.loads(selected['names']) == ['-report']
