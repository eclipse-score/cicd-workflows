# Copyright (c) 2026 Contributors to the Eclipse Foundation
# SPDX-License-Identifier: Apache-2.0
"""Refresh consumer commit pins without changing their compatibility contracts.

The daily workflow resolves current default-branch heads, tests that candidate
catalog with the normal consumer matrix, and publishes it only after validation.
This helper owns just the catalog edit: required checks, exception reasons and
formatting remain reviewable and unchanged. Publishing belongs to the workflow.
"""

import json
import os
import re
import subprocess

import yaml

from consumers import CatalogLoader, ROOT, load_catalog, matrix


CATALOG = ROOT / '.github/ci/consumers.yml'


def latest_commit(repository):
    """Resolve the default branch through GitHub's commits API using read access.

    Omitting the API's sha parameter selects the repository's default branch.
    This also supports consumers whose default branch is not named main. gh
    handles authentication through GH_TOKEN; credentials never enter a URL.
    """
    return subprocess.check_output([
        'gh', 'api', f'repos/{repository}/commits?per_page=1', '--jq', '.[0].sha',
    ], text=True, timeout=60).strip()


def refresh(path=CATALOG, resolve=latest_commit):
    """Resolve every repository before replacing only the YAML ref scalars.

    API errors or malformed responses leave the original file untouched. YAML
    node offsets preserve comments, field order, quoted values and blank lines;
    dumping parsed YAML would discard the explanation of each consumer profile.
    The injected resolver lets tests exercise those guarantees without a network.
    """
    content = path.read_text(encoding='utf-8')
    scenarios = load_catalog(content)
    heads = {}

    # Several scenarios can refer to the same repository. Resolve it once so all
    # scenarios in this update use the same snapshot even if its branch moves.
    for scenario in scenarios:
        repository = scenario['repository']

        if repository not in heads:
            sha = resolve(repository)

            if not isinstance(sha, str) or not re.fullmatch(r'[0-9a-f]{40}', sha):
                raise ValueError(f'{repository}: API did not return a full commit SHA')

            heads[repository] = sha

    document = yaml.compose(content, Loader=CatalogLoader)
    entries = next(value for key, value in document.value if key.value == 'consumers')
    replacements = []

    for entry in entries.value:
        fields = {key.value: value for key, value in entry.value}
        ref = fields['ref']
        sha = heads[fields['repository'].value]

        if ref.value != sha:
            # A hexadecimal SHA made entirely of digits must remain a YAML
            # string; otherwise the next catalog validation would see an int.
            quote = ref.style or ("'" if sha.isdigit() else '')
            replacements.append((ref.start_mark.index, ref.end_mark.index, quote + sha + quote))

    # Work backwards so earlier offsets remain valid after each replacement.
    # A no-op run must not rewrite the file or create an empty update commit.
    for start, end, replacement in reversed(replacements):
        content = content[:start] + replacement + content[end:]

    if replacements:
        # Validate the rendered file before writing, including exact preservation
        # of every non-ref field. Unsupported YAML alias/anchor layouts must fail
        # here rather than corrupt the catalog or weaken a consumer's contract.
        expected = [{**scenario, 'ref': heads[scenario['repository']]}
                    for scenario in scenarios]

        if load_catalog(content) != expected:
            raise ValueError('Pin edits changed fields outside the requested refs')

        path.write_text(content, encoding='utf-8')

    return bool(replacements)


if __name__ == '__main__':
    changed = refresh()

    # The matrix and artifact originate from the same resolved catalog. Later
    # jobs must test these exact SHAs rather than resolve moving branches again.
    with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
        output.write(f'changed={str(changed).lower()}\n')
        output.write('matrix=' + json.dumps(matrix(), separators=(',', ':')) + '\n')
