# Copyright (c) 2026 Contributors to the Eclipse Foundation
# SPDX-License-Identifier: Apache-2.0
"""Collect release status and relevant commits for the configured components."""

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
import re
import subprocess
from typing import cast

import yaml


@dataclass
class Commit:
    sha: str
    # subject = 1st line of the commit message, without body or footers.
    subject: str


class ReleaseStatus(StrEnum):
    UNRELEASED = "Unreleased"
    CHANGED = "Changes need review"
    UNCHANGED = "Unchanged"


@dataclass
class ComponentReport:
    """Release assessment for one configured component."""

    component_name: str
    latest_release_tag: str | None
    relevant_commits: list[Commit]
    maintenance_commits: list[Commit]

    @property
    def status(self) -> ReleaseStatus:
        if self.latest_release_tag is None:
            return ReleaseStatus.UNRELEASED
        if self.relevant_commits:
            return ReleaseStatus.CHANGED
        return ReleaseStatus.UNCHANGED


@dataclass
class ReleaseReport:
    """Release assessments for the current checkout."""

    head_sha: str
    component_reports: list[ComponentReport]
    has_uncommitted_changes: bool
    has_untracked_files: bool


@dataclass
class Config:
    """Map component names to repo-relative file and glob patterns."""

    components: dict[str, list[str]]


def single_labeler_rule(rules: object, key: str, label: str) -> object:
    """Extract one rule, rejecting conditions the release check cannot evaluate."""
    error = f"{label}: expected a single {key} rule"
    if not isinstance(rules, list):
        raise ValueError(error)
    rules = cast(list[object], rules)
    if len(rules) != 1:
        raise ValueError(error)
    rule = rules[0]
    if not isinstance(rule, dict):
        raise ValueError(error)
    rule = cast(dict[str, object], rule)
    if list(rule) != [key]:
        raise ValueError(error)
    return rule[key]


def read_config(root: Path) -> Config:
    """Read component names and path rules shared with actions/labeler.

    A final dot suffix of one to three characters is omitted from the component
    name, so bazel.yml uses bazel/v… tags. Rules must use a single changed-files /
    any-glob-to-any-file selection so Git can use the same list of file patterns.
    """
    config_path = root / ".github/labeler.yml"
    contents = cast(object, yaml.safe_load(config_path.read_text()))
    if not isinstance(contents, dict) or not contents:
        raise ValueError("Configuration must contain a nonempty labels mapping")

    components: dict[str, list[str]] = {}
    for label, rules in cast(dict[object, object], contents).items():
        if not isinstance(label, str):
            raise ValueError("Label names must be strings")
        name = re.sub(r"\.[^.]{1,3}\Z", "", label)
        if not name.strip():
            raise ValueError("Component names must be nonempty strings")
        # Different labels can normalize to the same tag prefix. Reject them
        # rather than silently dropping one label's file selection.
        if name in components:
            raise ValueError(f"{label}: duplicate release component {name!r}")

        changed_files = single_labeler_rule(rules, "changed-files", label)
        paths = single_labeler_rule(changed_files, "any-glob-to-any-file", label)
        if isinstance(paths, str):
            paths = [paths]
        if not paths:
            raise ValueError(f"{label}: paths must not be empty")
        if not isinstance(paths, list) or not all(
            isinstance(path, str) and path.strip() for path in cast(list[object], paths)
        ):
            raise ValueError(f"{label}: paths must be a list of nonempty strings")
        components[name] = cast(list[str], paths)

    return Config(components=components)


def git(root: Path, *arguments: str) -> str:
    """Run a Git query without optional index writes and propagate failures."""
    # Disabling optional index refreshes keeps the check read-only, even when
    # Git inspects staged and unstaged changes in the user's working tree.
    return subprocess.check_output(
        ["git", "--no-optional-locks", "-C", str(root), *arguments],
        text=True,
    )


def latest_tag(name: str, tags: list[str]) -> str | None:
    """Find the component's first full-version tag in a descending-version list."""
    # Git has already sorted tags by descending version, so v0.10.0 precedes
    # v0.2.0. Only accept this component's full versions, excluding prereleases.
    pattern = re.compile(re.escape(name) + r"/v(\d+)\.(\d+)\.(\d+)")
    for tag in tags:
        if pattern.fullmatch(tag):
            return tag
    return None


def commits_since(
    root: Path, ref: str, head_sha: str, pathspecs: list[str]
) -> list[Commit]:
    """Read commits after ref through head_sha that touch the selected paths."""
    # A NUL separates the full SHA from the subject, which can contain spaces.
    history = git(
        root, "log", "--format=%H%x00%s", f"{ref}..{head_sha}", "--", *pathspecs
    )
    commits: list[Commit] = []
    for line in history.splitlines():
        sha, subject = line.split("\0", 1)
        commits.append(Commit(sha=sha, subject=subject))
    return commits


def check(root: Path) -> ReleaseReport:
    """Assess releases from commits on the current branch since each local tag.

    Maintenance commits are kept separately. Local edits and untracked files
    produce global warnings; they do not affect the release assessment.
    No refs are fetched and no tags, releases or checkout files are modified.
    """
    # Match chore:, test: and ci:, optionally with a scope such as chore(deps):.
    # A breaking marker (!) does not match, so chore!: and ci(build)!: stay visible.
    maintenance_commit_pattern = re.compile(
        r"^(chore|test|ci)(\([^()\r\n]+\))?:[ \t]+", re.IGNORECASE
    )

    config = read_config(root)
    head_sha = git(root, "rev-parse", "--verify", "HEAD").strip()
    tags = git(root, "tag", "--list", "--sort=-version:refname").splitlines()
    results: list[ComponentReport] = []

    for name, paths in config.components.items():
        # Git's glob matching also includes deleted files in the history.
        # Keep the Git-specific prefix out of the YAML configuration.
        pathspecs = [f":(glob){path}" for path in paths]
        release_tag = latest_tag(name, tags)
        commits: list[Commit] = []
        # Without a baseline, report the component as unreleased rather than listing its
        # entire history. Fully qualified refs also resolve tag names
        # unambiguously when a branch happens to have the same name.
        if release_tag:
            ref = f"refs/tags/{release_tag}"
            commits = commits_since(root, ref, head_sha, pathspecs)

        # Retain both groups: relevant commits determine release status, while
        # maintenance commits can be included in the output with --no-filter.
        relevant_commits: list[Commit] = []
        maintenance_commits: list[Commit] = []
        for commit in commits:
            if maintenance_commit_pattern.match(commit.subject):
                maintenance_commits.append(commit)
            else:
                relevant_commits.append(commit)

        results.append(
            ComponentReport(
                component_name=name,
                latest_release_tag=release_tag,
                relevant_commits=relevant_commits,
                maintenance_commits=maintenance_commits,
            )
        )

    # Only warn about the presence of non-ignored untracked files. They are not
    # read, matched to components or included in release status.
    untracked_files = git(root, "ls-files", "--others", "--exclude-standard", "-z")
    return ReleaseReport(
        head_sha=head_sha,
        component_reports=results,
        has_uncommitted_changes=bool(
            git(root, "status", "--porcelain", "--untracked-files=no")
        ),
        has_untracked_files=bool(untracked_files),
    )
