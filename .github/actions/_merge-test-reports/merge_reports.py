# Copyright (c) 2026 Contributors to the Eclipse Foundation
# SPDX-License-Identifier: Apache-2.0
"""Combine selected artifact trees without interpreting their report formats."""

import argparse
import json
from pathlib import Path
import re
import shutil


def artifact_names(value):
    """Use exact, plain names so artifact selection cannot expand a glob."""
    names = json.loads(value)
    if (not isinstance(names, list) or not names or
            any(not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_.-]+', name)
                or name in ('.', '..') for name in names) or len(set(names)) != len(names)):
        raise ValueError('Expected a nonempty JSON array of unique plain artifact names')
    return names


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
                    raise ValueError(f'Conflicting test report file: {relative} (artifact {name})')
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
            seen.add(relative)
    return len(seen)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--artifacts', required=True)
    parser.add_argument('--downloads', type=Path, required=True)
    parser.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args()
    names = artifact_names(args.artifacts)
    # The pinned download action flattens exactly one matching artifact into
    # its download root, even when merge-multiple is false. Only use that layout
    # for a one-name selection; a multi-name selection must supply every named
    # directory rather than treating one downloaded artifact as several reports.
    sources = ([(names[0], args.downloads)] if len(names) == 1 else
               [(name, args.downloads / name) for name in names])
    print(f'Merged {merge(sources, args.destination)} test report files')


if __name__ == '__main__':
    main()
