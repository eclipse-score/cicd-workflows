# Copyright (c) 2026 Contributors to the Eclipse Foundation
# SPDX-License-Identifier: Apache-2.0
"""Execute a validated Bazel request in the caller's selected workspace.

Build and test targets enable their phases. Coverage runs the shared reporter
after measurement. Build dependency analysis retries recognized download
failures before execution. A failed command stops execution with its exit status.
"""

import os
import re
import shlex
import subprocess
import time


RESOLUTION_ATTEMPTS = 6
RESOLUTION_RETRY_DELAY = 30


def retryable_download_failure(output: str) -> bool:
    """Recognize retry causes in dependency resolution output.

    Only the --nobuild preparation uses this matcher. Compilation and test
    failures are handled separately and never retried.
    """
    output = output.lower()
    retryable_causes = (
        "checksum was",
        "socketexception",
        "sockettimeoutexception",
        "unknownhostexception",
        "contentlengthmismatchexception",
        "timed out",
        "connection reset",
        "connection refused",
        "network is unreachable",
        "temporary failure in name resolution",
    )
    if any(cause in output for cause in retryable_causes):
        return True
    # Retry HTTP timeouts, rate limits and selected server errors. The word
    # boundary matches complete status codes, so e.g. 5030 cannot match 503.
    return bool(
        re.search(
            r"(?:get returned|http|response code:)\s+(?:408|429|500|502|503|504)\b",
            output,
        )
    )


def run_resolution_attempt(argv: list[str], attempt: int) -> tuple[int, str]:
    """Stream one resolution attempt and retain its output for error recognition."""
    print(
        f"::group::Resolve dependencies ({attempt}/{RESOLUTION_ATTEMPTS}): "
        + shlex.join(argv),
        flush=True,
    )
    lines: list[str] = []
    process: subprocess.Popen[str]
    with subprocess.Popen(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
    ) as process:
        assert process.stdout is not None
        for line in process.stdout:
            print(line, end="", flush=True)
            lines.append(line)
        returncode = process.wait()
    print("::endgroup::", flush=True)
    output = "".join(lines)
    # Ignore ANSI colors for matching, preserving the original output for logs.
    plain_output = re.sub(r"\x1b\[[0-9;]*m", "", output)
    # run --nobuild leaves no built target for RunCommand to select. --norun
    # also prevents execution if an executable already exists in the cache.
    # The actual run still checks the target and propagates its own failures.
    if (returncode == 2 and "--nobuild" in argv and "--norun" in argv
            and plain_output.rstrip().endswith("ERROR: No targets found to run")):
        returncode = 0
    return returncode, output


def download_dependencies(argv: list[str]) -> None:
    """Retry transient download failures, retaining successful downloads locally."""
    for attempt in range(1, RESOLUTION_ATTEMPTS + 1):
        returncode, output = run_resolution_attempt(argv, attempt)
        if returncode == 0:
            return

        if not retryable_download_failure(output):
            print(
                "Dependency resolution failed without a recognized download error; "
                + "not retrying.",
                flush=True,
            )
            raise SystemExit(returncode)
        if attempt == RESOLUTION_ATTEMPTS:
            print(
                f"::error::Dependency download failed; all {RESOLUTION_ATTEMPTS} "
                + "resolution attempts exhausted.",
                flush=True,
            )
            raise SystemExit(returncode)

        print(
            f"::warning::Dependency download failed on attempt {attempt}/{RESOLUTION_ATTEMPTS}; "
            + f"retrying in {RESOLUTION_RETRY_DELAY} seconds.",
            flush=True,
        )
        time.sleep(RESOLUTION_RETRY_DELAY)


def main() -> None:
    """Execute the validated request in order, preserving module argument values."""
    startup: list[str] = []
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

    def dependency_download_command(command: str, *args: str) -> list[str]:
        """Construct the phase's download command without executing build actions."""
        separator = args.index('--')
        before_separator, after_separator = args[:separator], args[separator:]
        if command == 'run':
            # Keep run-specific rc options. Its -- introduces program arguments,
            # which are unnecessary here; --norun prevents program execution.
            after_separator = ('--norun',)
        # Place --nobuild after caller flags so retries cannot execute actions.
        return ['bazel', *startup, command, *common_args,
                *before_separator, '--nobuild', *after_separator]

    def run_bazel(command: str, *args: str) -> None:
        """Prepare dependencies, stream command output and stop on failure."""
        # Only build supports --nobuild without a post-analysis execution error.
        # Other phases execute directly and propagate their own exit status.
        if command == 'build':
            download_dependencies(dependency_download_command(command, *args))
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
