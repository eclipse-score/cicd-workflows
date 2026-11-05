# Copyright (c) 2026 Contributors to the Eclipse Foundation
# SPDX-License-Identifier: Apache-2.0
"""Collect selected test artifacts centrally without silently losing a variant."""

import argparse
import json
import os
from pathlib import Path
import shutil
import re


def merge(sources, destination):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    seen = {}
    for name, root in sources:
        root = Path(root).resolve()
        files = list(root.rglob('test.xml'))
        if not files:
            raise ValueError(f'Test artifact {name} contains no test.xml files')
        for source in files:
            if not source.resolve().is_relative_to(root):
                raise ValueError(f'Test artifact {name} contains an escaping path')
            relative = source.relative_to(root)
            data = source.read_bytes()
            if relative in seen and seen[relative] != data:
                raise ValueError(f'Conflicting test reports at {relative}; select non-overlapping rows')
            seen[relative] = data
            target = destination / relative
            if target.is_symlink() or not target.resolve().is_relative_to(destination.resolve()):
                raise ValueError('Test report destination escapes its directory')
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
    return len(seen)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('merge',))
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--reports', type=Path, required=True)
    args = parser.parse_args()
    names = json.loads(os.environ['TEST_REPORT_ARTIFACTS'])
    if (not isinstance(names, list) or not names or
            any(not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_.-]+', name)
                or name in ('.', '..') for name in names) or len(set(names)) != len(names)):
        raise ValueError('Expected unique plain test artifact names')
    downloads = Path(os.environ['TEST_REPORT_DOWNLOADS']).resolve()
    sources = []
    for name in names:
        root = downloads / name
        if root.is_symlink() or not root.resolve().is_relative_to(downloads):
            raise ValueError('Test artifact directory escapes its download root')
        sources.append((name, root))
    print(f'Merged {merge(sources, args.reports)} test reports')



if __name__ == '__main__':
    main()
