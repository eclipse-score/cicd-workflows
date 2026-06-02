#!/usr/bin/env bash
set -euo pipefail

python3 -m scripts.coverage.generate_cpp_coverage_full "$@"
