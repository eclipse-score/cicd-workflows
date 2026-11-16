# Copyright (c) 2026 Contributors to the Eclipse Foundation
# SPDX-License-Identifier: Apache-2.0
"""Combine selected artifact trees without interpreting their report formats."""

import argparse
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
from zipfile import ZipFile


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


def merge_downloads(downloads, destination):
    """Extract separate ZIP downloads before checking their report paths.

    download-artifact flattens a single match but creates artifact directories
    for multiple matches. Keeping ZIPs intact makes both layouts unambiguous:
    every downloaded file is one archive, regardless of its enclosing directory.
    """
    downloads = Path(downloads)
    archives = []
    for path in sorted(downloads.rglob('*')):
        if path.is_symlink():
            raise ValueError('Test artifact download contains a symlink')
        if path.is_file():
            archives.append(path)
    if not archives:
        raise CollectionError('no-reports', 'No test report artifacts were downloaded')
    with tempfile.TemporaryDirectory(prefix='docs-report-extract-') as temporary:
        sources = []
        for index, path in enumerate(archives):
            root = Path(temporary) / str(index)
            root.mkdir()
            with ZipFile(path) as archive:
                entries = {}
                for entry in archive.infolist():
                    relative = Path(entry.filename)
                    if relative.is_absolute() or '..' in relative.parts or '\\' in entry.filename:
                        raise ValueError(f'Unsafe test report archive path: {entry.filename}')
                    if stat.S_ISLNK(entry.external_attr >> 16):
                        raise ValueError(f'Test report archive contains a symlink: {entry.filename}')
                    # Duplicate ZIP entries participate in conflict detection too;
                    # extraction must not silently overwrite a different result.
                    key = relative.as_posix()
                    if key in entries and not entry.is_dir():
                        if archive.read(entries[key]) != archive.read(entry):
                            raise CollectionError('file-conflict', f'Conflicting test report file: {entry.filename}')
                    entries[key] = entry
                archive.extractall(root)
            sources.append((path.relative_to(downloads).as_posix(), root))
        return merge(sources, destination)


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
    prepare = subparsers.add_parser('pattern')
    prepare.add_argument('--artifacts', required=True)
    prepare.add_argument('--output', type=Path, required=True)
    collect = subparsers.add_parser('merge')
    collect.add_argument('--downloads', type=Path, required=True)
    collect.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'pattern':
        patterns = list(dict.fromkeys(artifact_patterns(args.artifacts)))
        with args.output.open('a') as output:
            output.write(f'pattern=@({"|".join(patterns)})\n')
        return
    print(f'Merged {merge_downloads(args.downloads, args.destination)} test report files')


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
