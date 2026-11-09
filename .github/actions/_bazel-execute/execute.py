# Copyright (c) 2026 Contributors to the Eclipse Foundation
# SPDX-License-Identifier: Apache-2.0
"""Execute the validated Bazel request supplied by bazel.yml through its environment."""

import os
import shlex
import subprocess


def main():
    # Split arguments without shell expansion. Target exclusions follow --;
    # build and test share one checkout and the same Bazel/cache context.
    common = ['--lockfile_mode=error', *shlex.split(os.environ['BAZEL_ARGS'])]
    configs = [f'--config={config}' for config in os.environ['BAZEL_CONFIGS'].split()]

    def run_bazel(command, *args):
        argv = ['bazel', command, *common, *args]
        print(f'::group::{shlex.join(argv)}', flush=True)
        result = subprocess.run(argv, check=False)
        print('::endgroup::', flush=True)
        # Stop immediately so a later command cannot hide this failure.
        if result.returncode:
            raise SystemExit(result.returncode)

    for command in ('build', 'test'):
        targets = shlex.split(os.environ[f'{command.upper()}_TARGETS'])
        if not targets:
            continue
        extra = shlex.split(os.environ[f'{command.upper()}_ARGS'])
        subcommand = os.environ['TEST_COMMAND'] if command == 'test' else command
        run_bazel(subcommand, *configs, *extra, '--', *targets)
    if os.environ['TEST_COMMAND'] == 'coverage':
        # coverage_tool owns interpretation, justifications and the gate.
        # This workflow only connects its report to the artifact upload.
        reporter_args = ['--archive-dir', os.environ['COVERAGE_REPORT_DIRECTORY']]
        if os.environ['COVERAGE_JUSTIFICATIONS']:
            reporter_args.extend(['--yaml', os.environ['COVERAGE_JUSTIFICATIONS']])
        run_bazel('run', '@score_coverage//:generate_coverage_html', '--', *reporter_args)


if __name__ == "__main__":
    main()
