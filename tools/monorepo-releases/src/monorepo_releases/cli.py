# Copyright (c) 2026 Contributors to the Eclipse Foundation
# SPDX-License-Identifier: Apache-2.0
"""Present the release report in the terminal and GitHub Actions job summary."""

import argparse
from html import escape
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import cast
from urllib.parse import quote

import yaml

from .releases import Commit, ComponentReport, ReleaseReport, check, git


def report_notes(report: ReleaseReport) -> list[str]:
    """Build the repository warnings shared by both output formats."""
    notes: list[str] = []
    if report.has_uncommitted_changes:
        notes.append('Warning: the repository has uncommitted changes.')
    if report.has_untracked_files:
        notes.append('Warning: untracked files are excluded from the comparison.')
    return notes


def render_text(report: ReleaseReport, *, no_filter: bool = False) -> str:
    """Render the terminal table followed by relevant commits per component."""
    rows = [('Component', 'Latest release', 'Status')]
    for component in report.component_reports:
        rows.append((component.component_name, component.latest_release_tag or '—', component.status))

    # Include the header when sizing columns, so unreleased components
    # and long component names still produce an aligned table.
    name_width = max(len(name) for name, _, _ in rows)
    release_width = max(len(release) for _, release, _ in rows)
    lines = ['Monorepo releases', '']
    for name, release, status in rows:
        lines.append(f'{name.ljust(name_width)}  {release.ljust(release_width)}  {status}')

    lines.extend(['', f'Commits through {report.head_sha[:7]}.'])
    lines.extend(report_notes(report))
    for component in report.component_reports:
        if not component.relevant_commits and not (no_filter and component.maintenance_commits):
            continue
        lines.extend(['', f'{component.component_name} — commits since {component.latest_release_tag}:'])
        for commit in component.relevant_commits:
            lines.append(f'  {commit.sha[:7]} {commit.subject}')
        if no_filter and component.maintenance_commits:
            lines.append('  Maintenance commits:')
            for commit in component.maintenance_commits:
                lines.append(f'    {commit.sha[:7]} {commit.subject}')
    return '\n'.join(lines) + '\n'


def commit_html(commit: Commit, repository_url: str) -> str:
    """Escape a commit subject and link its SHA and optional trailing PR number."""
    subject = escape(commit.subject)
    # Only a trailing (#123) identifies the introducing PR; other numbers
    # in the subject may refer to something else.
    pr_match = re.search(r'\(#(?P<number>[1-9][0-9]*)\)$', commit.subject)
    if pr_match:
        number = pr_match.group('number')
        message = escape(commit.subject[:pr_match.start()])
        subject = f'{message}(<a href="{repository_url}/pull/{number}">#{number}</a>)'

    commit_url = f'{repository_url}/commit/{commit.sha}'
    commit_link = f'<a href="{commit_url}"><code>{commit.sha[:7]}</code></a>'
    return f'<li>{commit_link} {subject}</li>'


def render_commit_list(component: ComponentReport, repository_url: str, *, no_filter: bool = False) -> str:
    """Render one component's relevant commits as a collapsible HTML list."""
    # Subjects and component names are repository-controlled text; escape them
    # before putting them into the HTML used for GitHub's collapsible lists.
    heading = escape(f'{component.component_name}: commits since {component.latest_release_tag}')
    lines = [f'<details><summary>{heading}</summary>', '']
    if component.relevant_commits:
        lines.append('<ul>')
        for commit in component.relevant_commits:
            lines.append(commit_html(commit, repository_url))
        lines.append('</ul>')
    if no_filter and component.maintenance_commits:
        lines.extend(['<p>Maintenance commits:</p>', '<ul>'])
        for commit in component.maintenance_commits:
            lines.append(commit_html(commit, repository_url))
        lines.append('</ul>')
    lines.extend(['', '</details>', '', ''])
    return '\n'.join(lines)


def render_summary(report: ReleaseReport, repository: str, *, no_filter: bool = False) -> str:
    """Render a GitHub job summary with linked tags and collapsible commit lists.

    Omit whole commit lists if they exceed the summary budget; the terminal
    report in the step log still contains every relevant commit.
    """
    repository_url = f'https://github.com/{quote(repository, safe="/")}'
    head_url = f'{repository_url}/commit/{report.head_sha}'
    lines = [
        '## Monorepo releases',
        '',
        f'Repository: {escape(repository)} · [Commits through `{report.head_sha[:7]}`]({head_url})',
        '',
        '| Component | Latest release | Status |',
        '| --- | --- | --- |',
    ]
    for component in report.component_reports:
        release = '—'
        if component.latest_release_tag:
            tag = component.latest_release_tag
            release_url = f'{repository_url}/releases/tag/{quote(tag, safe="")}'
            release = f'[{tag}]({release_url})'
        lines.append(f'| `{component.component_name}` | {release} | {component.status} |')
    lines.append('')
    for note in report_notes(report):
        lines.extend([escape(note), ''])

    # GitHub limits UTF-8 bytes, not Python characters. Keep the overview and
    # warnings intact, then add only complete, properly closed details blocks.
    # Leave room below GitHub's 1 MiB limit for the omission note.
    max_summary_bytes = 900_000
    summary = '\n'.join(lines) + '\n'
    summary_bytes = len(summary.encode('utf-8'))
    for component in report.component_reports:
        if not component.relevant_commits and not (no_filter and component.maintenance_commits):
            continue
        commit_list = render_commit_list(component, repository_url, no_filter=no_filter)
        commit_list_bytes = len(commit_list.encode('utf-8'))
        if summary_bytes + commit_list_bytes > max_summary_bytes:
            summary += 'Further commits omitted to stay within the workflow summary size limit. '
            summary += 'See the full output in the step log.\n'
            break
        summary += commit_list
        summary_bytes += commit_list_bytes
    return summary


def main() -> int:
    """Check the caller's checkout, print the report and append a CI summary."""
    parser = argparse.ArgumentParser(description='Check component releases against the local working tree.')
    _ = parser.add_argument(
        '--no-filter',
        action='store_true',
        help='Show maintenance commits too; release status still depends only on relevant commits.',
    )
    options = parser.parse_args()
    no_filter = cast(bool, options.no_filter)
    try:
        # The tool may be installed outside the checkout. Find the Git root from
        # the invocation directory, including when run from a subdirectory.
        root = Path(git(Path.cwd(), 'rev-parse', '--show-toplevel').strip())
        report = check(root)
        print(render_text(report, no_filter=no_filter), end='')

        # GitHub provides this file per step. Append so any existing step
        # content is preserved; ordinary local runs only print to the terminal.
        summary_path = os.environ.get('GITHUB_STEP_SUMMARY')
        if summary_path:
            summary = render_summary(report, os.environ['GITHUB_REPOSITORY'], no_filter=no_filter)
            with Path(summary_path).open('a', encoding='utf-8') as output:
                _ = output.write(summary)
    except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError, subprocess.CalledProcessError) as error:
        print(f'Release check failed: {error}', file=sys.stderr)
        return 1
    return 0
