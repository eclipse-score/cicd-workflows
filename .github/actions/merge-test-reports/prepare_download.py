# Copyright (c) 2026 Contributors to the Eclipse Foundation
# SPDX-License-Identifier: Apache-2.0
"""Validate artifact selection and prepare an isolated download directory."""

import argparse
import json
import os
import re
import tempfile


def artifact_names(value):
    """Use exact, plain names so artifact selection cannot expand a glob."""
    names = json.loads(value)
    if (not isinstance(names, list) or not names or
            any(not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_.-]+', name)
                or name in ('.', '..') for name in names) or len(set(names)) != len(names)):
        raise ValueError('Expected a nonempty JSON array of unique plain artifact names')
    return names


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--artifacts', required=True)
    args = parser.parse_args()
    names = artifact_names(args.artifacts)
    downloads = tempfile.mkdtemp(prefix='docs-test-reports-', dir=os.environ['RUNNER_TEMP'])
    with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
        output.write(f'pattern=@({"|".join(names)})\ndownloads={downloads}\n')


if __name__ == '__main__':
    main()
