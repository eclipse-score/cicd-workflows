# Copyright (c) 2026 Contributors to the Eclipse Foundation
# SPDX-License-Identifier: Apache-2.0
"""Prepare and verify real-consumer calls to the candidate on-pr workflow.

Pinned source commits make compatibility failures reproducible. The catalog
records the checks each consumer must support, so broken capability
recognition cannot turn the suite green without executing its checks.

The preparation job calls matrix() to turn the shared YAML catalog into GitHub
matrix rows. Each row then runs the candidate workflow in its own scenario
wrapper. That wrapper calls verify() with the workflow's reported observations
and the catalog's required checks. Other checks may be added or skipped without
changing that minimum contract. No result is inferred from a job name or log.
"""

import json
import os
from pathlib import Path
import re
import sys

import yaml


ROOT = Path(__file__).resolve().parents[2]

# These names are the check identifiers reported by on-pr.yml. Catalog entries
# select a supported subset as their minimum contract; omitted checks make no
# claim about whether a repository supports them.
CHECKS = {'pre-commit', 'python-tests', 'format', 'copyright',
          'require-lock', 'module-tidy', 'lockfile'}

# require-lock reports a missing-lockfile error, not a successful capability.
# Keep it observable in reports, but never require it to execute successfully.
REQUIRED_CHECKS = CHECKS - {'require-lock'}


# Only existing check switches/targets may vary between scenarios. Keeping the
# allowlist narrow prevents runner selection, credentials or checkout settings
# from being hidden in a consumer-specific bag of workflow inputs.
TARGET_INPUTS = {'format-target', 'copyright-target'}
SKIP_INPUTS = {
    'skip-pre-commit': 'pre-commit',
    'skip-python-tests': 'python-tests',
    'skip-format': 'format',
    'skip-copyright': 'copyright',
    'skip-bzlmod-lock': 'module-tidy',
}


def unique_mapping(loader, node):
    """Reject duplicate YAML keys rather than silently replacing an expectation."""
    result = {}

    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)

        if key in result:
            raise ValueError(f'Duplicate catalog key: {key}')

        result[key] = loader.construct_object(value_node)

    return result


class CatalogLoader(yaml.SafeLoader):
    """Keep catalog-specific validation separate from PyYAML's global loader."""


CatalogLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping)


def load_catalog(content):
    """Validate catalog text and return its consumer scenarios.

    Each catalog entry produces exactly one scenario. There are no independent
    matrix dimensions that could accidentally combine one repository's ref with
    another repository's expectations. Accepting text lets the updater validate
    a proposed edit before replacing the file used by the rest of the suite.
    """
    catalog = yaml.load(content, Loader=CatalogLoader)

    # Reject misspelled fields early, before launching remote checks. The upper
    # bound is GitHub Actions' matrix job limit; an empty list would exercise no
    # consumers and therefore cannot establish compatibility.
    if not isinstance(catalog, dict) or set(catalog) != {'consumers'}:
        raise ValueError('Catalog must contain only consumers')

    rows = catalog['consumers']

    if not isinstance(rows, list) or not 1 <= len(rows) <= 256:
        raise ValueError('Expected between 1 and 256 consumers')

    names = set()

    for row in rows:
        required_fields = {'name', 'repository', 'ref', 'required-checks'}

        if (not isinstance(row, dict) or not required_fields <= set(row) or
                set(row) - required_fields - {'overrides'}):
            raise ValueError('Each consumer needs name, repository, ref and required-checks; overrides are optional')

        # Names identify scenarios in the Actions UI. Keep them unambiguous as
        # the catalog grows, even when two scenarios use the same repository.
        name = row['name']

        if not isinstance(name, str) or not re.fullmatch(r'[a-z][a-z0-9-]*', name) or name in names:
            raise ValueError(f'Invalid or duplicate consumer name: {name}')

        names.add(name)

        # Full commit pins isolate workflow regressions from consumer changes.
        # A moving branch could otherwise make the same workflow PR alternate
        # between passing and failing without any change in the candidate code.
        if not isinstance(row['repository'], str) or not re.fullmatch(
                r'[A-Za-z0-9-]+/[A-Za-z0-9_.-]+', row['repository']):
            raise ValueError(f'{name}: repository must be owner/name')

        if not isinstance(row['ref'], str) or not re.fullmatch(r'[0-9a-f]{40}', row['ref']):
            raise ValueError(f'{name}: ref must be a full commit SHA')

        required = row['required-checks']

        # A nonempty, explicit list records the minimum supported profile.
        # Unknown names are usually typos; duplicates obscure the intended list.
        if (not isinstance(required, list) or not required or
                any(not isinstance(name, str) or name not in REQUIRED_CHECKS for name in required) or
                len(set(required)) != len(required)):
            raise ValueError(f'{name}: required-checks must be a nonempty list of unique known checks')

        overrides(row)

    return rows


def workflow_defaults(path):
    """Read execution defaults from the candidate rather than freezing them in tests.

    The wrapper has to declare individual scalar inputs because workflow calls
    cannot accept a dynamically expanded mapping. Resolving their defaults here
    ensures an accidental skip or target change is exercised by real consumers.
    Catalog overrides are applied afterwards, including explicit false values.
    """
    workflow = yaml.safe_load(path.read_text(encoding='utf-8'))
    # PyYAML's YAML 1.1 loader recognizes the Actions 'on' key as boolean true.
    # Accept the literal key too so this remains valid with a YAML 1.2 loader.
    trigger = workflow.get('on', workflow.get(True))
    definitions = trigger['workflow_call']['inputs']
    defaults = {}

    for name in sorted(TARGET_INPUTS | SKIP_INPUTS.keys()):
        definition = definitions[name]
        boolean = name in SKIP_INPUTS
        expected_type = 'boolean' if boolean else 'string'
        value = definition.get('default', False if boolean else '')

        # Fail preparation on incompatible API changes rather than coercing
        # strings into truthy booleans and testing a different workflow call.
        if definition['type'] != expected_type or type(value) is not (bool if boolean else str):
            raise ValueError(f'{name}: expected a {expected_type} workflow default')

        defaults[name] = value

    return defaults


def matrix(path=ROOT / '.github/ci/consumers.yml',
           workflow_path=ROOT / '.github/workflows/on-pr.yml'):
    """Encode the validated catalog for GitHub's reusable-workflow matrix inputs."""
    rows = load_catalog(path.read_text(encoding='utf-8'))
    defaults = workflow_defaults(workflow_path)

    # Reusable workflow inputs are scalar. Encode only the structured fields;
    # the human-readable reasons remain in the catalog for review, while the
    # wrapper receives the validated input values it needs for execution.
    return {'include': [{
        'name': row['name'], 'repository': row['repository'], 'ref': row['ref'],
        'required-checks': json.dumps(row['required-checks']),
        'check-inputs': json.dumps(defaults | overrides(row)),
    } for row in rows]}


def overrides(row):
    """Validate and unwrap documented departures from the default workflow call.

    Every override needs a reason beside its value. This keeps repository-specific
    exceptions visible in the same catalog as the supported minimum profile.
    Requiring a check while explicitly disabling it is a configuration error.
    """
    configured = row.get('overrides', {})

    if not isinstance(configured, dict):
        raise ValueError('overrides must be a mapping')

    values = {}

    for key, setting in configured.items():
        if key not in TARGET_INPUTS | SKIP_INPUTS.keys():
            raise ValueError(f'Unsupported consumer override: {key}')

        if (not isinstance(setting, dict) or set(setting) != {'value', 'reason'} or
                not isinstance(setting['reason'], str) or not setting['reason'].strip()):
            raise ValueError(f'{key}: override needs value and a nonempty reason')

        value = setting['value']

        if key in TARGET_INPUTS:
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f'{key}: target must be a nonempty string')
        elif type(value) is not bool:
            raise ValueError(f'{key}: skip value must be boolean')
        elif value:
            disabled = {SKIP_INPUTS[key]}

            # This switch disables both lockfile commands in on-pr.yml.
            if key == 'skip-bzlmod-lock':
                disabled.add('lockfile')

            if disabled.intersection(row['required-checks']):
                raise ValueError(f'{key}: cannot skip a required check')

        values[key] = value

    return values


def verify(actual, required, commit):
    """Require the pinned checkout and successful execution of minimum checks.

    The wrapper separately requires the called workflow to succeed, including
    checks outside this minimum set. These assertions protect against false
    green runs where capability detection skipped work the consumer must support.
    Additional observations do not change the minimum contract.
    """
    if not isinstance(actual, dict) or not isinstance(actual.get('checks'), dict):
        raise ValueError('Workflow report must contain a checks mapping')

    if actual.get('commit') != commit:
        raise ValueError(f'Wrong checkout: expected {commit}, got {actual.get("commit")}')

    observed = actual['checks']
    failed = {name: observed.get(name, 'missing') for name in required
              if observed.get(name) != 'success'}

    if failed:
        raise ValueError(f'Required checks did not succeed: {failed}')


if __name__ == '__main__':
    # Pass observations through environment variables so JSON never becomes
    # shell source. A failed assertion exits nonzero and fails the scenario.
    if sys.argv[1:] == ['verify']:
        verify(json.loads(os.environ['ACTUAL']), json.loads(os.environ['REQUIRED_CHECKS']),
               os.environ['SOURCE_SHA'])
    else:
        # GITHUB_OUTPUT is the handoff to the preparation job's matrix output.
        # Compact JSON keeps the entire matrix on a single output-file line.
        planned = json.dumps(matrix(), separators=(',', ':'))
        with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
            output.write(f'matrix={planned}\n')
