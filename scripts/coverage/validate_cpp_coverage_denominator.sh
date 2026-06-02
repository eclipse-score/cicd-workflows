#!/usr/bin/env bash
set -euo pipefail

python3 -m scripts.coverage.validate_cpp_coverage_denominator "$@"
