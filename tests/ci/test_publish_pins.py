# Copyright (c) 2026 Contributors to the Eclipse Foundation
# SPDX-License-Identifier: Apache-2.0
"""Exercise publishing scripts with local Git repositories and no GitHub writes.

A temporary bare repository represents main. Separate checkouts represent the
scheduled publisher and a concurrent writer, including edits to the compatibility
contract that automated conflict resolution must never silently undo.
"""

from pathlib import Path
import os
import subprocess
import tempfile
import unittest

import yaml

from consumers import ROOT


class PublishPinsTests(unittest.TestCase):
    def git(self, path, *args):
        """Keep test commands independent of the user's existing worktrees."""
        return subprocess.check_output(['git', '-C', str(path), *args], stderr=subprocess.STDOUT, text=True).strip()

    def test_artifact_download_uses_preparation_output_across_attempts(self):
        """Retrying publish must keep the snapshot from the successful prepare job."""
        workflow = yaml.safe_load((ROOT / '.github/workflows/_update_consumer_pins.yml').read_text())
        prepare = workflow['jobs']['prepare']
        upload = next(step for step in prepare['steps'] if step.get('id') == 'snapshot')
        download = next(step for step in workflow['jobs']['publish']['steps']
                        if 'actions/download-artifact@' in step.get('uses', ''))

        # Attempt-specific upload names avoid collisions on a full rerun, while
        # partial reruns consume the immutable ID from the original preparation.
        self.assertIn('github.run_attempt', upload['with']['name'])
        self.assertEqual(prepare['outputs']['artifact-id'], '${{ steps.snapshot.outputs.artifact-id }}')
        self.assertEqual(download['with']['artifact-ids'], '${{ needs.prepare.outputs.artifact-id }}')
        self.assertNotIn('name', download['with'])

    def test_publishing_preserves_concurrent_changes_and_exposes_catalog_conflicts(self):
        """Pushes reject newer main; fallback never resolves conflicting contracts."""
        workflow = yaml.safe_load((ROOT / '.github/workflows/_update_consumer_pins.yml').read_text())
        steps = workflow['jobs']['publish']['steps']
        script = next(step['run'] for step in steps if step.get('id') == 'push')
        fallback = next(step['run'] for step in steps
                        if step.get('name') == 'Prepare catalog changes for the fallback PR')
        action = next(step for step in steps if 'peter-evans/create-pull-request@' in step.get('uses', ''))
        catalog_path = '.github/ci/consumers.yml'
        original = 'ref: old-pin\nrequired-checks: [format]\n'
        candidate_catalog = 'ref: tested-pin\nrequired-checks: [format]\n'
        concurrent_catalog = 'ref: newer-pin\nrequired-checks: [format, copyright]\n'

        # Exercise both fallbacks: a failed consumer skips the direct push,
        # whereas a successful consumer can still encounter a concurrent writer.
        for change, consumer_success in (('none', True), ('readme', True),
                                         ('catalog', True), ('catalog', False)):
            with self.subTest(change=change, consumer_success=consumer_success), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                remote, publisher, writer = (root / name for name in ('remote.git', 'publisher', 'writer'))
                self.git(root, 'init', '--bare', '--initial-branch=main', str(remote))
                self.git(root, 'clone', str(remote), str(publisher))
                self.git(publisher, 'config', 'user.name', 'Test publisher')
                self.git(publisher, 'config', 'user.email', 'publisher@example.invalid')

                catalog = publisher / catalog_path
                catalog.parent.mkdir(parents=True)
                catalog.write_text(original)
                self.git(publisher, 'add', '.')
                self.git(publisher, 'commit', '-m', 'Initial baseline')
                self.git(publisher, 'push', 'origin', 'main')
                self.git(publisher, 'checkout', '--detach')
                tested_base = self.git(publisher, 'rev-parse', 'HEAD')

                if change != 'none':
                    self.git(root, 'clone', str(remote), str(writer))
                    self.git(writer, 'config', 'user.name', 'Concurrent writer')
                    self.git(writer, 'config', 'user.email', 'writer@example.invalid')
                    if change == 'catalog':
                        (writer / catalog_path).write_text(concurrent_catalog)
                    else:
                        (writer / 'README.md').write_text('Concurrent change\n')
                    self.git(writer, 'add', '.')
                    self.git(writer, 'commit', '-m', 'Advance main')
                    self.git(writer, 'push', 'origin', 'main')

                main_before = self.git(remote, 'rev-parse', 'main')
                catalog.write_text(candidate_catalog)
                if consumer_success:
                    result = subprocess.run(['bash', '-eo', 'pipefail', '-c', script],
                                            cwd=publisher, text=True, capture_output=True)
                    if change == 'none':
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertEqual(self.git(remote, 'show', f'main:{catalog_path}'), candidate_catalog.strip())
                        continue
                    self.assertNotEqual(result.returncode, 0)

                self.assertEqual(self.git(remote, 'rev-parse', 'main'), main_before)
                subprocess.run(['bash', '-eo', 'pipefail', '-c', fallback], cwd=publisher,
                               env={**os.environ, 'GITHUB_SHA': tested_base},
                               check=True, capture_output=True)
                self.assertEqual(self.git(publisher, 'rev-parse', 'HEAD'), tested_base)
                self.assertEqual(self.git(publisher, 'diff', '--cached', '--name-only'), catalog_path)

                # Model the pinned action's named-base path: save the staged
                # diff on a temporary branch and reset the base to origin/main.
                # Matching working/base names bypass its automatic cherry-pick
                # with --strategy-option=theirs, retaining the tested ancestry.
                working_base = self.git(publisher, 'symbolic-ref', '--short', 'HEAD')
                self.assertEqual(working_base, action['with']['base'])
                self.git(publisher, 'checkout', '-b', 'fallback-candidate')
                self.git(publisher, 'commit', '-m', 'Fallback candidate')
                self.git(publisher, 'checkout', working_base)
                self.git(publisher, 'reset', '--hard', f'origin/{working_base}')
                self.git(publisher, 'checkout', 'fallback-candidate')
                self.assertEqual(self.git(publisher, 'rev-parse', 'HEAD^'), tested_base)
                self.assertEqual(catalog.read_text(), candidate_catalog)

                # GitHub merges this branch against current main normally. An
                # unrelated edit merges cleanly; overlapping catalog edits must
                # surface a conflict while remote main keeps its stronger checks.
                self.git(publisher, 'fetch', 'origin', 'main')
                merge = subprocess.run(['git', 'merge', '--no-edit', 'origin/main'], cwd=publisher,
                                       text=True, capture_output=True)
                if change == 'catalog':
                    self.assertNotEqual(merge.returncode, 0)
                    self.assertIn(catalog_path, self.git(publisher, 'diff', '--name-only', '--diff-filter=U'))
                    self.assertEqual(self.git(remote, 'show', f'main:{catalog_path}'), concurrent_catalog.strip())
                else:
                    self.assertEqual(merge.returncode, 0, merge.stderr)
                    self.assertEqual((publisher / 'README.md').read_text(), 'Concurrent change\n')
                    self.assertEqual(catalog.read_text(), candidate_catalog)


if __name__ == '__main__':
    unittest.main()
