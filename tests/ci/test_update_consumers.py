# Copyright (c) 2026 Contributors to the Eclipse Foundation
# SPDX-License-Identifier: Apache-2.0
"""Verify that automated pin refreshes cannot rewrite compatibility requirements.

Network calls are injected: these tests exercise file edits and failure behavior
without updating real repository branches or creating pull requests.
"""

from pathlib import Path
import tempfile
import unittest

import yaml

from update_consumers import refresh


class PinUpdateTests(unittest.TestCase):
    def catalog(self, path, second_repository='eclipse-score/other'):
        """Use quotes, comments and exceptions to detect destructive YAML rewrites."""
        content = f'''# Keep this explanation and the blank line below.

consumers:
  - name: first
    repository: eclipse-score/first
    ref: '{'a' * 40}' # A reviewed baseline.
    required-checks: [format]
    overrides:
      format-target:
        value: //tools:format.check
        reason: The formatter lives in //tools.

  - name: second
    repository: {second_repository}
    ref: {'b' * 40}
    required-checks: [lockfile]
'''
        path.write_text(content)
        return content

    def test_refresh_changes_only_refs_and_preserves_comments_and_contracts(self):
        """Each scenario gets its repository's head without YAML layout churn."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'consumers.yml'
            before = self.catalog(path)
            heads = {'eclipse-score/first': 'c' * 40, 'eclipse-score/other': 'd' * 40}

            self.assertTrue(refresh(path, heads.__getitem__))
            self.assertEqual(path.read_text(), before.replace('a' * 40, 'c' * 40).replace('b' * 40, 'd' * 40))

            # With unchanged default branches there must be no empty commit or
            # formatting-only update. Re-resolving heads remains a no-op.
            self.assertFalse(refresh(path, heads.__getitem__))

    def test_shared_repository_is_resolved_once_for_a_consistent_snapshot(self):
        """A moving branch cannot give two scenarios different pins in one update."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'consumers.yml'
            self.catalog(path, second_repository='eclipse-score/first')
            calls = []

            def resolve(repository):
                calls.append(repository)
                return 'c' * 40

            refresh(path, resolve)

            self.assertEqual(calls, ['eclipse-score/first'])
            self.assertEqual(path.read_text().count('c' * 40), 2)

    def test_numeric_sha_remains_a_yaml_string(self):
        """Even an all-digit SHA must survive the next catalog validation."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'consumers.yml'
            self.catalog(path)

            self.assertTrue(refresh(path, lambda repository: '1' * 40))
            self.assertFalse(refresh(path, lambda repository: '1' * 40))

    def test_unsupported_ref_aliases_fail_before_writing(self):
        """Offset edits must never destroy anchors shared by different consumers."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'consumers.yml'
            content = self.catalog(path)
            content = content.replace("ref: '" + 'a' * 40 + "'", "ref: &shared '" + 'a' * 40 + "'")
            content = content.replace('ref: ' + 'b' * 40, 'ref: *shared')
            path.write_text(content)

            with self.assertRaises((ValueError, yaml.YAMLError)):
                refresh(path, lambda repository: 'c' * 40)

            self.assertEqual(path.read_text(), content)

    def test_api_failure_or_invalid_sha_leaves_the_entire_catalog_unchanged(self):
        """An early successful lookup must not cause a partially refreshed file."""
        for invalid in (None, 'main'):
            with self.subTest(invalid=invalid), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'consumers.yml'
                before = self.catalog(path)

                def resolve(repository):
                    if repository == 'eclipse-score/first':
                        return 'c' * 40
                    if invalid is None:
                        raise RuntimeError('GitHub API unavailable')
                    return invalid

                with self.assertRaises((RuntimeError, ValueError)):
                    refresh(path, resolve)

                self.assertEqual(path.read_text(), before)


if __name__ == '__main__':
    unittest.main()
