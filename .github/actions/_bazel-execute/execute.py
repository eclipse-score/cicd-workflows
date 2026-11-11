# Copyright (c) 2026 Contributors to the Eclipse Foundation
# SPDX-License-Identifier: Apache-2.0
"""Execute a validated Bazel request in the caller's selected workspace.

Build and test targets enable their phases. Coverage runs the shared reporter
after measurement. A failed command stops execution with its exit status.
"""

import os
import shlex
import subprocess


def main():
    """Execute the validated request in order, preserving module argument values."""
    startup = []
    common_args = ['--lockfile_mode=error']
    setup_qnx_sdp = os.environ.get('SETUP_QNX_SDP') == 'true'
    if setup_qnx_sdp:
        # The absolute helper works from nested workspaces. Its QNX credentials
        # can coexist with NETRC credentials for other download hosts.
        helper = os.environ.get('QNX_CREDENTIAL_HELPER', '')
        if helper:
            common_args.append(f'--credential_helper=*.qnx.com={helper}')
        # QNX setup writes license-server flags in the checkout root. Nested
        # workspaces load that file through an additional startup option.
        bazelrc = os.environ.get('QNX_BAZELRC', '')
        if bazelrc:
            startup.append(f'--bazelrc={bazelrc}')
    common_args.extend(shlex.split(os.environ['BAZEL_ARGS']))
    configs = [f'--config={config}' for config in os.environ['BAZEL_CONFIGS'].split()]

    def run_bazel(command, *args):
        """Stream a command's output into a log group and stop on failure."""
        argv = ['bazel', *startup, command, *common_args, *args]
        print(f'::group::{shlex.join(argv)}', flush=True)
        result = subprocess.run(argv, check=False)
        print('::endgroup::', flush=True)
        # Stop immediately so a later command cannot hide this failure.
        if result.returncode:
            raise SystemExit(result.returncode)

    # shlex preserves quoted argument values without evaluating shell syntax.
    # The separator keeps target exclusions out of Bazel's option parsing.
    build_targets = shlex.split(os.environ['BUILD_TARGETS'])
    if build_targets:
        build_args = shlex.split(os.environ['BUILD_ARGS'])
        run_bazel('build', *configs, *build_args, '--', *build_targets)

    test_targets = shlex.split(os.environ['TEST_TARGETS'])
    test_command = os.environ['TEST_COMMAND']
    if test_targets:
        test_args = shlex.split(os.environ['TEST_ARGS'])
        run_bazel(test_command, *configs, *test_args, '--', *test_targets)

    if test_command == 'coverage':
        # The reporter runs on the host. Instrumentation configs belong to the
        # measurement above and could change how the reporter itself is built.
        # It reads COVERAGE_THRESHOLD from the environment and creates the archive
        # selected by the workflow's upload step.
        reporter_args = [
            '--archive-dir', os.environ['COVERAGE_REPORT_DIRECTORY'],
            '--platform', 'qnx' if setup_qnx_sdp else 'linux',
        ]
        if os.environ['COVERAGE_JUSTIFICATIONS']:
            reporter_args.extend(['--yaml', os.environ['COVERAGE_JUSTIFICATIONS']])
        run_bazel('run', '@score_coverage//:generate_coverage_html', '--', *reporter_args)


if __name__ == "__main__":
    main()
