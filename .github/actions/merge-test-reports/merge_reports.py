# Copyright (c) 2026 Contributors to the Eclipse Foundation
# SPDX-License-Identifier: Apache-2.0
"""Combine selected artifact trees without interpreting their report formats."""

import argparse
from pathlib import Path
import shutil

from prepare_download import artifact_names


def merge(sources, destination):
    """Keep relative paths, including logs and non-Bazel JUnit XML filenames.

    Existing destination files participate in conflict detection as well, so a
    report committed by a consumer cannot be silently replaced by an artifact.
    XML contents and testcase deduplication belong to the documentation parser.
    """
    destination = Path(destination)
    if destination.is_symlink():
        raise ValueError('Test report destination must not be a symlink')
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
    sources = [(name, args.downloads / name) for name in names]
    print(f'Merged {merge(sources, args.destination)} test report files')


if __name__ == '__main__':
    main()
