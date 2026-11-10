# Copyright (c) 2026 Contributors to the Eclipse Foundation
# SPDX-License-Identifier: Apache-2.0
"""Combine selected artifact trees without interpreting their report formats."""

import argparse
import base64
from fnmatch import fnmatchcase
import json
import os
from pathlib import Path
import re
import shutil
from urllib.request import Request, urlopen


class CollectionError(ValueError):
    """Identify an expected report error separately from service/runtime failures."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def artifact_patterns(value):
    """Read one exact artifact name or star pattern per nonblank line."""
    patterns = [line.strip() for line in value.splitlines() if line.strip()]
    if (not patterns or
            any(not re.fullmatch(r'[A-Za-z0-9_.*-]+', pattern)
                or pattern in ('.', '..') for pattern in patterns)):
        raise ValueError('Expected artifact names or * patterns, one per line')
    return patterns


def list_artifacts():
    """Use the same run-scoped artifact service as download-artifact.

    The runtime token limits listing to this workflow run without requiring
    additional GITHUB_TOKEN permissions from reusable-workflow callers. The
    backend IDs and request follow actions/toolkit's artifact client:
    https://github.com/actions/toolkit/tree/main/packages/artifact/src/internal
    """
    token = os.environ['ACTIONS_RUNTIME_TOKEN']
    payload = token.split('.')[1]
    claims = json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))
    scope = next(part.split(':') for part in claims['scp'].split()
                 if part.startswith('Actions.Results:'))
    if len(scope) != 3:
        raise ValueError('Missing artifact service backend IDs')
    request = Request(
        os.environ['ACTIONS_RESULTS_URL'].rstrip('/') +
        '/twirp/github.actions.results.api.v1.ArtifactService/ListArtifacts',
        data=json.dumps({'workflow_run_backend_id': scope[1], 'workflow_job_run_backend_id': scope[2]}).encode(),
        headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
    )
    with urlopen(request, timeout=60) as response:
        return [{'name': artifact['name'], 'id': int(artifact['database_id'])}
                for artifact in json.load(response).get('artifacts', [])]


def select_artifacts(patterns, artifacts):
    """Require a match per selector and download each artifact once.

    Reruns can leave several artifacts with the same name. Select the newest
    upload, matching download-artifact's latest-artifact behavior.
    """
    latest = {}
    for artifact in artifacts:
        name = artifact['name']
        if name not in latest or artifact['id'] > latest[name]['id']:
            latest[name] = artifact
    selected = {}
    for pattern in patterns:
        matches = [artifact for name, artifact in sorted(latest.items())
                   if fnmatchcase(name, pattern)]
        if not matches:
            raise CollectionError('artifact-not-found', f'No test artifacts match: {pattern}')
        for artifact in matches:
            # Artifact names become directory names during download. Only allow
            # plain names before they can be interpreted as filesystem paths.
            if not re.fullmatch(r'[A-Za-z0-9_.-]+', artifact['name']) or artifact['name'] in ('.', '..'):
                raise ValueError(f'Unsupported test artifact name: {artifact["name"]}')
            selected[artifact['id']] = artifact
    return list(selected.values())


def merge(sources, destination):
    """Keep relative paths, including logs and non-Bazel JUnit XML filenames.

    Existing destination files participate in conflict detection as well, so a
    report committed by a consumer cannot be silently replaced by an artifact.
    XML contents and testcase deduplication belong to the documentation parser.
    """
    destination = Path(destination).absolute()
    # Check the lexical path before mkdir or resolution can follow a symlink.
    for component in (destination, *destination.parents):
        if component.is_symlink():
            raise ValueError(f'Test report destination path contains a symlink: {component}')
    # Docs can read files absent from the incoming artifacts, so validate the
    # complete existing report tree as well as paths written during this merge.
    for existing in destination.rglob('*'):
        if existing.is_symlink():
            raise ValueError(f'Existing test report destination contains a symlink: {existing}')
    destination.mkdir(parents=True, exist_ok=True)
    seen = set()
    for name, root in sources:
        root = Path(root)
        if root.is_symlink():
            raise ValueError(f'Test artifact {name} directory is a symlink')
        if not root.is_dir():
            raise ValueError(f'Missing test artifact directory: {name}')
        files = []
        for source in sorted(root.rglob('*')):
            if source.is_symlink():
                raise ValueError(f'Test artifact {name} contains a symlink')
            if source.is_file():
                files.append(source)
        if not files:
            raise ValueError(f'Test artifact {name} contains no files')
        for source in files:
            relative = source.relative_to(root)
            target = destination / relative
            for parent in target.parents:
                if parent == destination:
                    break
                if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
                    raise ValueError(f'Conflicting test report directory: {relative}')
            if target.is_symlink():
                raise ValueError(f'Test report destination contains a symlink: {relative}')
            if target.exists():
                if not target.is_file() or target.read_bytes() != source.read_bytes():
                    raise CollectionError('file-conflict', f'Conflicting test report file: {relative} (artifact {name})')
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
            seen.add(relative)
    return len(seen)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest='command', required=True)
    select = subparsers.add_parser('select')
    select.add_argument('--artifacts', required=True)
    select.add_argument('--output', type=Path, required=True)
    collect = subparsers.add_parser('merge')
    collect.add_argument('--names', required=True)
    collect.add_argument('--downloads', type=Path, required=True)
    collect.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'select':
        patterns = artifact_patterns(args.artifacts)
        selected = select_artifacts(patterns, list_artifacts())
        with args.output.open('a') as output:
            output.write(f'ids={",".join(str(artifact["id"]) for artifact in selected)}\n')
            output.write(f'names={json.dumps([artifact["name"] for artifact in selected])}\n')
        return
    names = json.loads(args.names)
    # download-artifact flattens a single match even with merge-multiple false.
    # Resolve names before download so a missing selection cannot masquerade as
    # the flattened layout of one successfully downloaded artifact.
    sources = ([(names[0], args.downloads)] if len(names) == 1 else
               [(name, args.downloads / name) for name in names])
    print(f'Merged {merge(sources, args.destination)} test report files')


if __name__ == '__main__':
    try:
        main()
    except CollectionError as error:
        # Self-tests must distinguish the expected report rejection from an API
        # or programming failure that also produces a nonzero process exit.
        if output := os.environ.get('GITHUB_OUTPUT'):
            with Path(output).open('a') as output_file:
                output_file.write(f'error-code={error.code}\n')
        raise
