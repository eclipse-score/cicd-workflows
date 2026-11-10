# Copyright (c) 2026 Contributors to the Eclipse Foundation
# SPDX-License-Identifier: Apache-2.0
"""Run a module's selected Bazel phases in the current workspace.

bazel.yml validates the request before setup and supplies its values through
environment variables. The composite action selects the working directory;
this helper uses the Bazel executable and cache already configured by setup.
It does not resolve paths relative to its own source directory.

Build and test targets independently enable their phases. Coverage replaces
the test phase and then runs the shared reporter. The first failed command
exits with its status; the workflow still collects available report files.
"""

import os
import shlex
import subprocess


def main():
    """Execute the validated request in order, preserving module argument values."""
    # Split arguments without shell expansion. Target exclusions follow --;
    # build and test share one checkout and the same Bazel/cache context.
    common = ['--lockfile_mode=error']
    # QNX setup exports an absolute helper path, independent of the checkout's
    # .bazelrc spelling. The helper uses the QNX action's home .netrc entry;
    # a separate NETRC file can still supply credentials for other download hosts.
    helper = os.environ.get('QNX_CREDENTIAL_HELPER', '')
    if os.environ.get('SETUP_QNX_SDP') == 'true' and helper:
        common.append(f'--credential_helper=*.qnx.com={helper}')
    common.extend(shlex.split(os.environ['BAZEL_ARGS']))
    # setup-qnx-sdp writes license-server flags in the checkout root. An explicit
    # extra bazelrc also loads those flags when the selected workspace is nested.
    # Startup options precede the subcommand and remain one literal argument.
    bazelrc = os.environ.get('QNX_BAZELRC', '') if os.environ.get('SETUP_QNX_SDP') == 'true' else ''
    startup = [f'--bazelrc={bazelrc}'] if bazelrc else []
    configs = [f'--config={config}' for config in os.environ['BAZEL_CONFIGS'].split()]

    def run_bazel(command, *args):
        """Stream a command's output into a log group and stop on failure."""
        argv = ['bazel', *startup, command, *common, *args]
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
        # The reporter runs on the host without the module's instrumentation
        # configs or test-only flags, which could change how the tool is built.
        # COVERAGE_THRESHOLD is inherited by the reporter as an environment
        # variable; its archive directory connects the report to artifact upload.
        reporter_args = ['--archive-dir', os.environ['COVERAGE_REPORT_DIRECTORY']]
        if os.environ['COVERAGE_JUSTIFICATIONS']:
            reporter_args.extend(['--yaml', os.environ['COVERAGE_JUSTIFICATIONS']])
        run_bazel('run', '@score_coverage//:generate_coverage_html', '--', *reporter_args)


if __name__ == "__main__":
    main()
