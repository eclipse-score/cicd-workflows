# Copyright (c) 2026 Contributors to the Eclipse Foundation
# SPDX-License-Identifier: Apache-2.0
"""Exercise release comparisons against real, isolated Git repositories."""

import os
from pathlib import Path
import subprocess
import sys

import pytest


from monorepo_releases.releases import Commit, ComponentReport, check
from monorepo_releases.cli import render_summary, render_text


def git(root: Path, *args: str) -> bytes:
    return subprocess.check_output(['git', '-C', str(root), *args], stderr=subprocess.STDOUT)


def write(root: Path, path: str, text: str) -> Path:
    file = root / path
    file.parent.mkdir(parents=True, exist_ok=True)
    _ = file.write_text(text)
    return file


def commit(root: Path, message: str) -> None:
    _ = git(root, 'add', '--all')
    _ = git(root, 'commit', '-qm', message)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _ = git(tmp_path, 'init', '-q', '--initial-branch=main')
    _ = git(tmp_path, 'config', 'user.name', 'Release audit test')
    _ = git(tmp_path, 'config', 'user.email', 'release-audit@example.invalid')
    _ = git(tmp_path, 'config', 'commit.gpgsign', 'false')
    _ = write(tmp_path, '.github/labeler.yml', '''comp-bazel:
  - changed-files:
      - any-glob-to-any-file:
          - .github/workflows/bazel.yml
          - ".github/actions/_bazel-execute/**"
          - ".github/actions/_setup-*/**"
comp-docs:
  - changed-files:
      - any-glob-to-any-file:
          - .github/workflows/docs.yml
          - ".github/actions/_merge-test-reports/**"
comp-daily:
  - changed-files:
      - any-glob-to-any-file:
          - .github/workflows/daily.yml
          - .github/workflows/_docs-cleanup.yml
''')
    for name in ['bazel', 'docs', 'daily', '_docs-cleanup']:
        _ = write(tmp_path, f'.github/workflows/{name}.yml', 'original\n')
    _ = write(tmp_path, '.github/actions/_bazel-execute/execute.py', 'old helper\n')
    _ = write(tmp_path, '.gitignore', '*ignored*\n')
    commit(tmp_path, 'Initial components')
    for name in ['bazel', 'docs', 'daily']:
        _ = git(tmp_path, 'tag', f'{name}/v0.1.0')
    return tmp_path


def component(root: Path, name: str) -> ComponentReport:
    return next(component for component in check(root).component_reports if component.component_name == name)


def test_unrelated_commits_do_not_require_component_releases(repo: Path) -> None:
    _ = write(repo, 'README.md', 'Unrelated documentation\n')
    commit(repo, 'Update unrelated docs')
    result = check(repo)
    assert all(component.status == 'Unchanged' and not component.relevant_commits for component in result.component_reports)
    assert 'Update unrelated docs' not in render_text(result)


def test_local_edits_only_warn_while_committed_history_determines_status(repo: Path) -> None:
    _ = git(repo, 'switch', '-qc', 'feature')
    helper = write(repo, '.github/actions/_bazel-execute/execute.py', 'committed helper\n')
    commit(repo, 'Improve execution helper (#42)')
    _ = helper.write_text('staged helper\n')
    _ = git(repo, 'add', str(helper))
    # Local edits restore the released contents, but the committed improvement
    # still needs a release review. Other local-only edits must not affect status.
    _ = helper.write_text('old helper\n')
    (repo / '.github/workflows/daily.yml').unlink()
    index_before = (repo / '.git/index').read_bytes()
    status_before = git(repo, 'status', '--porcelain=v1', '-uall')

    result = check(repo)
    bazel, docs, daily = result.component_reports
    assert bazel.status == 'Changes need review'
    assert result.has_uncommitted_changes
    assert bazel.relevant_commits[0].subject == 'Improve execution helper (#42)'
    assert docs.status == 'Unchanged'
    assert daily.status == 'Unchanged'
    output = render_text(result)
    assert 'Warning: the repository has uncommitted changes.' in output
    assert '.github/' not in output
    assert 'diff --git' not in output
    assert (repo / '.git/index').read_bytes() == index_before
    assert git(repo, 'status', '--porcelain=v1', '-uall') == status_before
    assert git(repo, 'branch', '--show-current') == b'feature\n'


def test_numeric_tag_order_resolves_annotated_tags_and_ignores_prereleases(repo: Path) -> None:
    _ = write(repo, '.github/workflows/bazel.yml', 'version two\n')
    commit(repo, 'Version two')
    _ = git(repo, 'tag', 'bazel/v0.2.0')
    _ = write(repo, '.github/workflows/bazel.yml', 'version ten\n')
    commit(repo, 'Version ten')
    _ = git(repo, '-c', 'tag.gpgsign=false', 'tag', '-a', 'bazel/v0.10.0', '-m', 'Release')
    for tag in ['bazel/v99.0.0-rc.1', 'other/v999.0.0', 'v9999.0.0']:
        _ = git(repo, 'tag', tag)
    bazel = component(repo, 'bazel')
    assert bazel.latest_release_tag == 'bazel/v0.10.0'
    assert bazel.status == 'Unchanged'
    assert not bazel.relevant_commits


def test_missing_tag_reports_unreleased_without_listing_files_or_all_history(repo: Path) -> None:
    _ = git(repo, 'tag', '-d', 'docs/v0.1.0')
    _ = git(repo, 'tag', 'v9.0.0')
    _ = write(repo, '.github/actions/_merge-test-reports/new.py', 'report helper\n')
    docs = component(repo, 'docs')
    assert docs.latest_release_tag is None
    assert docs.status == 'Unreleased'
    assert not docs.relevant_commits
    assert not check(repo).has_uncommitted_changes
    assert check(repo).has_untracked_files
    output = render_text(check(repo))
    assert '.github/' not in output
    assert 'Initial components' not in output


def test_ignored_new_helpers_do_not_affect_status(repo: Path) -> None:
    _ = write(repo, '.github/actions/_setup-new/ignored.txt', 'ignored helper\n')
    bazel = component(repo, 'bazel')
    assert bazel.status == 'Unchanged'
    assert not check(repo).has_uncommitted_changes
    assert not check(repo).has_untracked_files


def test_secondary_workflow_and_shared_helpers_have_component_specific_history(repo: Path) -> None:
    _ = write(repo, '.github/workflows/_docs-cleanup.yml', 'new cleanup\n')
    commit(repo, 'Improve cleanup (#101)')
    _ = write(repo, '.github/actions/_merge-test-reports/merge.py', 'new merge helper\n')
    commit(repo, 'Improve merge (#102)')
    bazel, docs, daily = check(repo).component_reports
    assert bazel.status == 'Unchanged' and not bazel.relevant_commits
    assert [commit.subject for commit in docs.relevant_commits] == ['Improve merge (#102)']
    assert [commit.subject for commit in daily.relevant_commits] == ['Improve cleanup (#101)']


def test_empty_path_selection_fails_instead_of_comparing_the_entire_repo(repo: Path) -> None:
    _ = write(repo, '.github/labeler.yml', 'comp-bazel:\n  - changed-files:\n      - any-glob-to-any-file: []\n')
    with pytest.raises(ValueError, match='paths must not be empty'):
        _ = check(repo)


@pytest.mark.parametrize(('subject', 'linked'), [
    ('Improve helper (#123)', True),
    ('Improve helper (#123) continued', False),
    ('Fix #123 in helper', False),
    ('Improve helper (#abc)', False),
    ('Improve helper (other/repo#123)', False),
    ('<script>alert("commit")</script> (#123)', True),
])
def test_summary_links_only_trailing_pr_numbers_and_escapes_subjects(repo: Path, subject: str, linked: bool) -> None:
    _ = write(repo, '.github/workflows/bazel.yml', 'new workflow\n')
    commit(repo, subject)
    result = check(repo)
    summary = render_summary(result, 'example/consumer')
    assert ('href="https://github.com/example/consumer/pull/123"' in summary) is linked
    assert '/releases/tag/bazel%2Fv0.1.0' in summary
    assert f'/commit/{result.head_sha}' in summary
    assert '<details><summary>bazel: commits since bazel/v0.1.0</summary>' in summary
    assert '<script>' not in summary
    assert '.github/' not in summary and 'diff --git' not in summary
    assert 'Changed files' not in summary
    assert subject in render_text(result)
    if subject.startswith('<script>'):
        assert '&lt;script&gt;' in summary


def test_large_commit_list_keeps_summary_within_limit_and_all_commits_in_log(repo: Path) -> None:
    result = check(repo)
    result.component_reports[0].relevant_commits = [Commit(result.head_sha, 'ä' * 600_000)]
    summary = render_summary(result, 'example/consumer')
    assert len(summary.encode('utf-8')) < 1024 * 1024
    assert '| `bazel` |' in summary and '| `docs` |' in summary
    assert 'Further commits omitted' in summary
    assert 'ä' * 600_000 in render_text(result)


def run_cli(root: Path, *arguments: str,
            extra_environment: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    environment = {key: value for key, value in os.environ.items()
                   if key not in ('GITHUB_STEP_SUMMARY', 'GITHUB_REPOSITORY')}
    environment.update(extra_environment or {})
    return subprocess.run([str(Path(sys.executable).parent / 'monorepo-releases'),
                           *arguments], cwd=root, env=environment, text=True, capture_output=True)


def test_installed_cli_audits_consumer_from_subdirectory_and_appends_summary(repo: Path, tmp_path_factory: pytest.TempPathFactory) -> None:
    summary = tmp_path_factory.mktemp('summary') / 'summary.md'
    _ = summary.write_text('Existing summary\n')
    result = run_cli(repo / '.github', extra_environment={
        'GITHUB_STEP_SUMMARY': str(summary), 'GITHUB_REPOSITORY': 'example/consumer',
    })
    assert result.returncode == 0, result.stderr
    assert result.stdout.count('Unchanged') == 3
    assert 'Warning: the repository has uncommitted changes.' not in result.stdout
    assert summary.read_text().startswith('Existing summary\n## Monorepo releases')
    assert 'example/consumer' in summary.read_text()


def test_installed_cli_reports_invalid_config_and_rejects_arguments(repo: Path) -> None:
    assert run_cli(repo, 'bazel').returncode == 2
    _ = write(repo, '.github/labeler.yml', '{}\n')
    result = run_cli(repo)
    assert result.returncode == 1
    assert 'nonempty labels mapping' in result.stderr


def test_cli_uses_shared_labeler_patterns_and_ignores_other_labels(repo: Path) -> None:
    """Scalar/list rules can share a helper; general PR labels do not become releases."""
    _ = write(repo, '.github/labeler.yml', '''comp-bazel:
  - changed-files:
      - any-glob-to-any-file: ".github/actions/_bazel-execute/**"
comp-docs:
  - changed-files:
      - any-glob-to-any-file:
          - ".github/actions/_bazel-execute/**"
ci:
  - base-branch: main
''')
    commit(repo, 'chore: configure component labels')
    _ = write(repo, '.github/actions/_bazel-execute/execute.py', 'improved helper\n')
    commit(repo, 'fix: improve shared helper (#123)')
    result = run_cli(repo)
    assert result.returncode == 0, result.stderr
    assert result.stdout.count('Changes need review') == 2
    assert result.stdout.count('fix: improve shared helper (#123)') == 2
    assert 'bazel/v0.1.0' in result.stdout and 'docs/v0.1.0' in result.stdout
    assert 'Unreleased' not in result.stdout


@pytest.mark.parametrize(('configuration', 'error'), [
    ('- bazel\n', 'nonempty labels mapping'),
    ('123: []\n', 'Label names must be strings'),
    ('comp-: []\n', 'Component names must be nonempty strings'),
    ('comp-bazel: workflow\n', 'expected a single changed-files rule'),
    ('comp-bazel:\n  - changed-files:\n      - any-glob-to-any-file: [123]\n',
     'paths must be a list of nonempty strings'),
    ('comp-bazel:\n  - changed-files:\n      - any-glob-to-any-file: [" "]\n',
     'paths must be a list of nonempty strings'),
    ('documentation: []\n', 'at least one comp-* label'),
    ('comp-bazel:\n  - changed-files:\n      - all-globs-to-any-file: [workflow]\n',
     'expected a single any-glob-to-any-file rule'),
    ('comp-bazel:\n  - changed-files:\n      - any-glob-to-any-file: [workflow]\n    base-branch: main\n',
     'expected a single changed-files rule'),
])
def test_cli_rejects_invalid_component_mapping_without_a_traceback(repo: Path, configuration: str, error: str) -> None:
    """Malformed YAML selections must fail rather than produce a misleading release status."""
    _ = write(repo, '.github/labeler.yml', configuration)
    result = run_cli(repo)
    assert result.returncode == 1
    assert error in result.stderr
    assert 'Traceback' not in result.stderr
    assert not result.stdout


@pytest.mark.parametrize(('subject', 'hidden'), [
    ('chore: cleanup (#12)', True),
    ('chore(deps): bump dependency (#12)', True),
    ('test(parser): cover edge case (#12)', True),
    ('ci: update runner (#12)', True),
    ('CI(build): update runner (#12)', True),
    ('chore!: remove old API (#12)', False),
    ('ci(build)!: require new runner (#12)', False),
    ('feat: new feature (#12)', False),
    ('fix: fix bug (#12)', False),
    ('Revert "chore: cleanup (#12)" (#13)', False),
    ('choreography: improve dance (#12)', False),
    ('Add test: reproduce issue (#12)', False),
])
def test_commit_filter_only_hides_nonbreaking_maintenance_subjects(repo: Path, subject: str, hidden: bool) -> None:
    _ = write(repo, '.github/workflows/bazel.yml', 'changed workflow\n')
    commit(repo, subject)
    result = check(repo)
    expected_status = 'Unchanged' if hidden else 'Changes need review'
    assert result.component_reports[0].status == expected_status
    expected_maintenance = [subject] if hidden else []
    assert [commit.subject for commit in result.component_reports[0].maintenance_commits] == expected_maintenance
    assert (subject in render_text(result)) is not hidden
    assert (f'/commit/{result.head_sha}"><code>' in render_summary(result, 'example/consumer')) is not hidden
    assert subject in render_text(result, no_filter=True)
    assert f'/commit/{result.head_sha}"><code>' in render_summary(result, 'example/consumer', no_filter=True)


def test_cli_no_filter_restores_commits_and_pr_links_without_changing_status(repo: Path, tmp_path_factory: pytest.TempPathFactory) -> None:
    _ = write(repo, '.github/workflows/bazel.yml', 'new workflow\n')
    commit(repo, 'chore(deps): bump tool (#123)')
    summary = tmp_path_factory.mktemp('filter-summary') / 'summary.md'
    filtered = run_cli(repo)
    unfiltered = run_cli(repo, '--no-filter', extra_environment={
        'GITHUB_STEP_SUMMARY': str(summary), 'GITHUB_REPOSITORY': 'example/consumer',
    })
    assert filtered.returncode == unfiltered.returncode == 0
    assert 'chore(deps): bump tool (#123)' not in filtered.stdout
    assert 'chore(deps): bump tool (#123)' in unfiltered.stdout
    assert filtered.stdout.count('Unchanged') == unfiltered.stdout.count('Unchanged') == 3
    assert '/pull/123' in summary.read_text()


@pytest.mark.parametrize('path', ['notes.txt', '.github/actions/_setup-new/new.py'])
def test_untracked_files_only_produce_a_general_warning(repo: Path, path: str) -> None:
    """Untracked files anywhere in the repo are warned about, never assigned to components."""
    _ = write(repo, path, 'untracked content\n')
    report = check(repo)
    assert report.has_untracked_files
    assert not report.has_uncommitted_changes
    assert all(component.status == 'Unchanged' for component in report.component_reports)
    for output in [render_text(report), render_summary(report, 'example/consumer')]:
        assert 'Warning: untracked files are excluded from the comparison.' in output
        assert path not in output
        assert 'untracked content' not in output


def test_uncommitted_warning_covers_unconfigured_files_and_staged_changes(repo: Path) -> None:
    file = write(repo, 'README.md', 'original\n')
    commit(repo, 'Add unrelated readme')
    _ = file.write_text('staged change\n')
    _ = git(repo, 'add', 'README.md')
    # Restoring the working file still leaves an uncommitted change in the index.
    _ = file.write_text('original\n')
    report = check(repo)
    assert report.has_uncommitted_changes
    assert all(component.status == 'Unchanged' for component in report.component_reports)
    assert 'Warning: the repository has uncommitted changes.' in render_text(report)
    assert 'Warning: the repository has uncommitted changes.' in render_summary(report, 'example/consumer')


def test_cli_assesses_each_component_and_no_filter_only_expands_output(repo: Path, tmp_path_factory: pytest.TempPathFactory) -> None:
    """Release policy is observable through the command, independently of its Python data model."""
    _ = git(repo, 'tag', '-d', 'docs/v0.1.0')
    _ = git(repo, 'tag', 'v99.0.0')
    _ = write(repo, '.github/actions/_bazel-execute/execute.py', 'updated helper\n')
    commit(repo, 'chore(deps): update helper (#123)')
    _ = write(repo, '.github/workflows/_docs-cleanup.yml', 'improved cleanup\n')
    commit(repo, 'fix: clean obsolete documentation (#456)')
    _ = write(repo, 'README.md', 'unrelated changes\n')
    commit(repo, 'feat: unrelated documentation (#789)')

    output_dir = tmp_path_factory.mktemp('cli-policy')
    default_summary = output_dir / 'default.md'
    all_summary = output_dir / 'all.md'
    default = run_cli(repo, extra_environment={
        'GITHUB_STEP_SUMMARY': str(default_summary), 'GITHUB_REPOSITORY': 'example/consumer',
    })
    all_commits = run_cli(repo, '--no-filter', extra_environment={
        'GITHUB_STEP_SUMMARY': str(all_summary), 'GITHUB_REPOSITORY': 'example/consumer',
    })
    assert default.returncode == all_commits.returncode == 0
    for output in [default.stdout, all_commits.stdout]:
        component_rows = [
            line for line in output.splitlines()
            if line.startswith(('bazel ', 'docs ', 'daily '))
            and line.endswith(('Unchanged', 'Unreleased', 'Changes need review'))
        ]
        assert len(component_rows) == 3
        assert component_rows[0].endswith('Unchanged')
        assert component_rows[1].endswith('Unreleased')
        assert component_rows[2].endswith('Changes need review')
        assert 'fix: clean obsolete documentation (#456)' in output
        assert 'feat: unrelated documentation (#789)' not in output
    assert 'chore(deps): update helper (#123)' not in default.stdout
    assert 'chore(deps): update helper (#123)' in all_commits.stdout
    assert '/pull/123' not in default_summary.read_text()
    assert '/pull/123' in all_summary.read_text()
    for summary in [default_summary.read_text(), all_summary.read_text()]:
        assert '| `bazel` |' in summary and '| Unchanged |' in summary
        assert '| `docs` | — | Unreleased |' in summary
        assert '/pull/456' in summary and '/pull/789' not in summary


def test_cli_local_changes_warn_without_creating_a_release_candidate(repo: Path) -> None:
    _ = write(repo, '.github/workflows/bazel.yml', 'local workflow edit\n')
    _ = git(repo, 'add', '.github/workflows/bazel.yml')
    _ = write(repo, '.github/workflows/docs.yml', 'unstaged docs edit\n')
    _ = write(repo, 'new-file.txt', 'untracked\n')
    index_before = (repo / '.git/index').read_bytes()
    result = run_cli(repo)
    assert result.returncode == 0, result.stderr
    assert result.stdout.count('Unchanged') == 3
    assert 'Changes need review' not in result.stdout
    assert 'Warning: the repository has uncommitted changes.' in result.stdout
    assert 'Warning: untracked files are excluded' in result.stdout
    assert (repo / '.git/index').read_bytes() == index_before


def test_cli_relevant_history_remains_reviewable_after_a_revert(repo: Path) -> None:
    workflow = write(repo, '.github/workflows/bazel.yml', 'feature\n')
    commit(repo, 'feat: add feature (#123)')
    _ = git(repo, 'revert', '--no-edit', 'HEAD')
    assert workflow.read_text() == 'original\n'
    result = run_cli(repo)
    assert result.returncode == 0, result.stderr
    assert 'Changes need review' in result.stdout
    assert 'feat: add feature (#123)' in result.stdout
