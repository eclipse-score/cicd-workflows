# Copyright (c) 2026 Contributors to the Eclipse Foundation
# SPDX-License-Identifier: Apache-2.0
"""Exercise the workflow's actual push script against isolated local Git repos.

No GitHub branches are touched. A bare temporary repository represents main;
separate checkouts represent the scheduled publisher and a concurrent writer.
The tests protect the non-forced push on which the PR fallback depends.
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

    def test_push_updates_main_and_rejects_a_concurrent_change(self):
        """The same script must succeed on its tested base and fail on a newer main."""
        workflow = yaml.safe_load((ROOT / '.github/workflows/_update_consumer_pins.yml').read_text())
        script = next(step['run'] for step in workflow['jobs']['publish']['steps']
                      if step.get('id') == 'push')

        fallback = next(step['run'] for step in workflow['jobs']['publish']['steps']
                        if step.get('name') == 'Prepare catalog changes for the fallback PR')

        for concurrent_change in (False, True):
            with self.subTest(concurrent_change=concurrent_change), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                remote, publisher, writer = (root / name for name in ('remote.git', 'publisher', 'writer'))
                self.git(root, 'init', '--bare', '--initial-branch=main', str(remote))
                self.git(root, 'clone', str(remote), str(publisher))
                self.git(publisher, 'config', 'user.name', 'Test publisher')
                self.git(publisher, 'config', 'user.email', 'publisher@example.invalid')

                catalog = publisher / '.github/ci/consumers.yml'
                catalog.parent.mkdir(parents=True)
                catalog.write_text('original catalog\n')
                self.git(publisher, 'add', '.')
                self.git(publisher, 'commit', '-m', 'Initial baseline')
                self.git(publisher, 'push', 'origin', 'main')

                # Publishing uses a detached checkout of the tested github.sha.
                # A later main commit must survive rather than be overwritten.
                self.git(publisher, 'checkout', '--detach')
                tested_base = self.git(publisher, 'rev-parse', 'HEAD')

                if concurrent_change:
                    self.git(root, 'clone', str(remote), str(writer))
                    self.git(writer, 'config', 'user.name', 'Concurrent writer')
                    self.git(writer, 'config', 'user.email', 'writer@example.invalid')
                    (writer / 'README.md').write_text('Concurrent change\n')
                    self.git(writer, 'add', '.')
                    self.git(writer, 'commit', '-m', 'Advance main')
                    self.git(writer, 'push', 'origin', 'main')

                main_before = self.git(remote, 'rev-parse', 'main')
                catalog.write_text('updated catalog\n')
                result = subprocess.run(['bash', '-eo', 'pipefail', '-c', script],
                                        cwd=publisher, text=True, capture_output=True)

                if concurrent_change:
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(self.git(remote, 'rev-parse', 'main'), main_before)
                    self.assertEqual(self.git(remote, 'show', 'main:README.md'), 'Concurrent change')

                    # The rejected push leaves a clean checkout on the local
                    # candidate commit. Normalize it before the PR action takes
                    # its detached-HEAD working base, so its replay range
                    # includes the pin update rather than starting after it.
                    self.assertEqual(self.git(publisher, 'status', '--porcelain'), '')
                    subprocess.run(['bash', '-eo', 'pipefail', '-c', fallback],
                                   cwd=publisher, env={**os.environ, 'GITHUB_SHA': tested_base},
                                   check=True, capture_output=True)
                    self.assertEqual(self.git(publisher, 'rev-parse', 'HEAD'), tested_base)
                    self.assertEqual(self.git(publisher, 'diff', '--cached', '--name-only'),
                                     '.github/ci/consumers.yml')

                    # Exercise the action's local handoff: commit the staged
                    # catalog, then replay that commit onto the newer main.
                    # The resulting PR branch must retain both sets of changes.
                    self.git(publisher, 'checkout', '-b', 'fallback-candidate')
                    self.git(publisher, 'commit', '-m', 'Fallback candidate')
                    candidate = self.git(publisher, 'rev-parse', 'HEAD')
                    self.assertEqual(self.git(publisher, 'rev-list', '--count',
                                              f'{tested_base}..{candidate}'), '1')
                    self.git(publisher, 'fetch', 'origin', 'main')
                    self.git(publisher, 'checkout', '-b', 'fallback-pr', 'origin/main')
                    self.git(publisher, 'cherry-pick', candidate)
                    self.assertEqual((publisher / 'README.md').read_text(), 'Concurrent change\n')
                    self.assertEqual(catalog.read_text(), 'updated catalog\n')
                else:
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(self.git(remote, 'show', 'main:.github/ci/consumers.yml'), 'updated catalog')


if __name__ == '__main__':
    unittest.main()
