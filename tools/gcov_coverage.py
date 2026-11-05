# Copyright (c) 2026 ETAS GmbH and Contributors to the Eclipse Foundation
# SPDX-License-Identifier: Apache-2.0
"""GCC/QCC report generation for configured PR coverage."""

import os
from pathlib import Path
import subprocess
import sys


def generate(workspace, destination, threshold):
    from configured_coverage import normalized_lcov
    source = workspace / 'bazel-out/_coverage/_coverage_report.dat'
    data = normalized_lcov(source)
    destination.mkdir(parents=True, exist_ok=True)
    report = destination / 'coverage_report.dat'
    report.write_bytes(data)
    # genhtml owns HTML presentation; the LCOV record totals establish the
    # configured line-coverage gate independently of locale-specific HTML text.
    found = sum(int(line[3:]) for line in data.decode().splitlines() if line.startswith('LF:'))
    hit = sum(int(line[3:]) for line in data.decode().splitlines() if line.startswith('LH:'))
    if not found or hit > found:
        raise ValueError('GCC/QCC report has no valid line totals')
    subprocess.run(['genhtml', str(report), '--output-directory', str(destination),
                    '--show-details', '--source-directory', str(workspace / 'bazel-out/..'),
                    '--legend', '--function-coverage', '--branch-coverage'], cwd=workspace, check=True)
    if hit * 100 / found < threshold:
        raise ValueError(f'Line coverage {hit * 100 / found:.2f}% is below {threshold}%')


if __name__ == '__main__':
    try:
        generate(Path.cwd(), Path(sys.argv[1]), float(os.environ['COVERAGE_THRESHOLD']))
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
