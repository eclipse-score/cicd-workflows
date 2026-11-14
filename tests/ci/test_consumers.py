# Copyright (c) 2026 Contributors to the Eclipse Foundation
# SPDX-License-Identifier: Apache-2.0
"""Protect the compatibility suite against false green runs and ambiguous pins.

These tests exercise the suite's own validation and reporting, without cloning
or building consumers. Real compatibility is covered separately by the matrix
calls to on-pr.yml. Both layers are needed: a passing consumer cannot establish
that the suite would notice a skipped check or an incorrectly selected commit.
"""

import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml

from consumers import CHECKS, ROOT, matrix, verify, workflow_defaults


class ConsumerTests(unittest.TestCase):
    def setUp(self):
        """Give each test a fresh catalog so negative cases cannot affect others."""
        self.catalog = yaml.safe_load((ROOT / '.github/ci/consumers.yml').read_text())
        self.row = self.catalog['consumers'][0]

    def load_catalog(self, catalog):
        """Exercise the real YAML-loading path with a modified, isolated catalog."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'consumers.yml'
            path.write_text(yaml.safe_dump(catalog))
            return matrix(path)

    def test_catalog_covers_real_consumers_with_minimum_requirements(self):
        """The committed suite must retain explicit minimum compatibility profiles."""
        rows = matrix()['include']
        # Keep coverage across a broad set of repositories rather than silently
        # reducing the compatibility suite to one or two representative callers.
        self.assertGreaterEqual(len(rows), 10)

        for row in rows:
            required = json.loads(row['required-checks'])
            self.assertTrue(required)
            self.assertLessEqual(set(required), CHECKS)

    def test_duplicate_names_and_floating_refs_are_rejected(self):
        """Every scenario must be identifiable and reproducible across reruns."""
        self.catalog['consumers'].append(copy.deepcopy(self.row))

        with self.assertRaisesRegex(ValueError, 'duplicate'):
            self.load_catalog(self.catalog)

        # Remove the duplicate so the next failure comes specifically from the
        # moving ref, rather than from the independently invalid scenario name.
        self.catalog['consumers'].pop()
        self.row['ref'] = 'main'

        with self.assertRaisesRegex(ValueError, 'full commit SHA'):
            self.load_catalog(self.catalog)

    def test_empty_unknown_and_duplicate_requirements_are_rejected(self):
        """A minimum profile must exercise known checks and identify them clearly."""
        for required in ([], ['formatt'], ['require-lock'], ['format', 'format'], 'format'):
            with self.subTest(required=required):
                self.row['required-checks'] = required

                with self.assertRaisesRegex(ValueError, 'unique known checks'):
                    self.load_catalog(self.catalog)

    def test_documented_overrides_become_workflow_inputs(self):
        """Reasons stay reviewable in YAML while execution receives typed values."""
        self.row['overrides'] = {
            'format-target': {'value': '//tools:format.check', 'reason': 'Formatter lives in //tools.'},
            'skip-python-tests': {'value': True, 'reason': 'Needs a service unavailable on PR runners.'},
        }

        row = self.load_catalog(self.catalog)['include'][0]

        expected = workflow_defaults(ROOT / '.github/workflows/on-pr.yml')
        expected.update({'format-target': '//tools:format.check', 'skip-python-tests': True})
        self.assertEqual(json.loads(row['check-inputs']), expected)
        self.assertEqual(json.loads(row['required-checks']), self.row['required-checks'])

    def test_candidate_defaults_reach_scenarios_without_wrapper_fallbacks(self):
        """Accidental target/skip defaults must affect consumers, not be masked."""
        workflow = yaml.safe_load((ROOT / '.github/workflows/on-pr.yml').read_text())
        definitions = workflow[True]['workflow_call']['inputs']
        definitions['format-target']['default'] = '//:changed-format.check'
        definitions['skip-format']['default'] = True
        definitions['skip-python-tests']['default'] = True
        self.row['overrides'] = {
            'skip-python-tests': {'value': False, 'reason': 'This scenario must run Python tests.'},
        }

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'consumers.yml'
            candidate = Path(directory) / 'on-pr.yml'
            path.write_text(yaml.safe_dump(self.catalog))
            candidate.write_text(yaml.safe_dump(workflow))
            inputs = json.loads(matrix(path, candidate)['include'][0]['check-inputs'])

        self.assertEqual(inputs['format-target'], '//:changed-format.check')
        self.assertTrue(inputs['skip-format'])
        self.assertFalse(inputs['skip-python-tests'])

        # Check the wrapper forwards these resolved values without replacements.
        # Any fallback such as "|| false" or a hardcoded target must fail here,
        # because it could hide future changes to the candidate's public API.
        wrapper = yaml.safe_load((ROOT / '.github/workflows/_test_consumer_scenario.yml').read_text())
        call = wrapper['jobs']['checks']['with']
        for name in inputs:
            self.assertEqual(call[name], '${{ fromJSON(inputs.check-inputs).' + name + ' }}')

    def test_overrides_require_reasons_supported_inputs_and_valid_types(self):
        """Exceptions must be explicit and cannot conceal unrelated workflow settings."""
        for setting in (
                {'format-target': {'value': '//tools:format.check', 'reason': ' '}},
                {'runner-labels': {'value': '"self-hosted"', 'reason': 'Custom machine.'}},
                {'skip-python-tests': {'value': 'true', 'reason': 'Needs a service.'}},
                {'format-target': {'value': False, 'reason': 'Missing target.'}}):
            with self.subTest(setting=setting):
                self.row['overrides'] = setting

                with self.assertRaises(ValueError):
                    self.load_catalog(self.catalog)

    def test_overrides_cannot_disable_required_checks(self):
        """Both lockfile commands must respect the shared skip-bzlmod-lock switch."""
        for required, switch in (
                ('format', 'skip-format'), ('module-tidy', 'skip-bzlmod-lock'),
                ('lockfile', 'skip-bzlmod-lock')):
            with self.subTest(required=required):
                self.row['required-checks'] = [required]
                self.row['overrides'] = {switch: {'value': True, 'reason': 'Temporary exception.'}}

                with self.assertRaisesRegex(ValueError, 'cannot skip a required check'):
                    self.load_catalog(self.catalog)

    def test_duplicate_yaml_keys_are_rejected(self):
        """A later YAML value must not silently overwrite an earlier assertion."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'consumers.yml'
            path.write_text('consumers: []\nconsumers: []\n')

            with self.assertRaisesRegex(ValueError, 'Duplicate catalog key'):
                matrix(path)

    def test_required_checks_must_succeed_and_use_the_pinned_checkout(self):
        """Missing, skipped and failed minimum checks must all reject a green call."""
        required = self.row['required-checks']
        actual = {'commit': self.row['ref'], 'checks': dict.fromkeys(required, 'success')}
        verify(actual, required, self.row['ref'])

        for outcome in ('skipped', 'failure', 'cancelled', None):
            with self.subTest(outcome=outcome):
                actual['checks']['format'] = outcome

                with self.assertRaisesRegex(ValueError, 'Required checks did not succeed'):
                    verify(actual, required, self.row['ref'])

        del actual['checks']['format']

        with self.assertRaisesRegex(ValueError, 'missing'):
            verify(actual, required, self.row['ref'])

        actual['commit'] = '0' * 40

        with self.assertRaisesRegex(ValueError, 'Wrong checkout'):
            verify(actual, required, self.row['ref'])

    def test_additional_checks_and_skips_do_not_change_the_minimum_contract(self):
        """The suite can evolve without catalog assertions for every internal step."""
        actual = {'commit': self.row['ref'], 'checks': {
            'format': 'success', 'new-check': 'success', 'python-tests': 'skipped',
        }}

        verify(actual, ['format'], self.row['ref'])

        # A failure outside the minimum set is handled by the wrapper's job
        # dependency, not ignored by the overall suite. Inspect that dependency
        # here so this relaxed minimum assertion cannot replace the success gate.
        workflow = yaml.safe_load((ROOT / '.github/workflows/_test_consumer_scenario.yml').read_text())
        job = workflow['jobs']['verify']
        self.assertEqual(job['needs'], 'checks')
        self.assertNotIn('if', job)
        self.assertNotIn('continue-on-error', workflow['jobs']['checks'])

    def test_workflow_report_preserves_failed_and_skipped_outcomes(self):
        """The workflow's JSON must preserve individual outcomes, including failures."""
        # Execute the actual workflow script rather than reproducing its logic.
        # A failed check must remain visible alongside successful/skipped checks.
        workflow = yaml.safe_load((ROOT / '.github/workflows/on-pr.yml').read_text())
        script = next(step['run'] for step in workflow['jobs']['pr-checks']['steps']
                      if step.get('id') == 'results')

        outcomes = dict.fromkeys(CHECKS, 'skipped')
        outcomes.update(dict.fromkeys(self.row['required-checks'], 'success'))
        outcomes['format'] = 'failure'
        steps = {name: {'outcome': outcome} for name, outcome in outcomes.items()}

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'output'
            subprocess.run(['python3', '-c', script], check=True, env={
                **os.environ, 'CHECK_STEPS': json.dumps(steps),
                'SOURCE_SHA': self.row['ref'], 'GITHUB_OUTPUT': str(output),
            })
            report = json.loads(output.read_text().removeprefix('check-results='))

        self.assertEqual(report, {'commit': self.row['ref'], 'checks': outcomes})


if __name__ == '__main__':
    unittest.main()
