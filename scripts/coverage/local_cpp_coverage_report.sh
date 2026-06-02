#!/usr/bin/env bash
# *******************************************************************************
# Copyright (c) 2026 Contributors to the Eclipse Foundation
#
# See the NOTICE file(s) distributed with this work for additional
# information regarding copyright ownership.
#
# This program and the accompanying materials are made available under the
# terms of the Apache License Version 2.0 which is available at
# https://www.apache.org/licenses/LICENSE-2.0
#
# SPDX-License-Identifier: Apache-2.0
# *******************************************************************************

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$(git rev-parse --show-toplevel)" || {
    echo "Error: Not in a git repository" >&2
    exit 1
}

MIN_COVERAGE="${1:-0}"

echo "📊 Installing prerequisites..."
sudo apt-get update --quiet
sudo apt-get install --yes --quiet lcov

echo ""
echo "🔨 Generating full-denominator C++ coverage..."
"$SCRIPT_DIR/generate_cpp_coverage_full.sh" \
  --scope "//score/..." \
  --config "x86_64-linux" \
  --min-coverage "$MIN_COVERAGE" \
  --extra-bazel-flags "--test_output=errors --nocache_test_results --lockfile_mode=error"

echo ""
echo "✅ Validating denominator completeness..."
"$SCRIPT_DIR/validate_cpp_coverage_denominator.sh"

echo ""
echo "Coverage Summary:"
cat bazel-out/coverage/coverage_summary.txt || true

echo ""
echo "Coverage Report Files:"
echo "  HTML report:             bazel-out/coverage/coverage_html/index.html"
echo "  Summary stats:           bazel-out/coverage/coverage_summary.txt"
echo "  Missing files:           bazel-out/coverage/missing_coverage_files.txt"
echo "  Expected sources:        bazel-out/coverage/expected_sources.txt"
echo "  Covered sources:         bazel-out/coverage/covered_sources.txt"

if command -v xdg-open &>/dev/null; then
    echo ""
    read -p "Open HTML report in browser? (y/n): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        xdg-open bazel-out/coverage/coverage_html/index.html
    fi
fi
