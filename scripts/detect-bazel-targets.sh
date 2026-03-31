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

repo="${1:?Repository path is required}"

if ! [[ -f "$repo/MODULE.bazel" || -f "$repo/WORKSPACE" || -f "$repo/WORKSPACE.bazel" ]]; then
  echo "ERROR: Not a Bazel workspace; you cannot run this script here." >&2
  exit 1
fi

if ! command -v bazel >/dev/null 2>&1; then
  echo "ERROR: bazel is not available on PATH; you need that to run this script." >&2
  exit 1
fi

if [[ -z "${GITHUB_OUTPUT:-}" ]]; then
  echo "WARNING: This action is designed to be used in a GitHub Actions workflow. Running in 'local mode'." >&2
  GITHUB_OUTPUT=/dev/stdout
fi

echo "::group:: Detecting Bazel targets"

# Runs a boolean-style check function and writes to GITHUB_OUTPUT.
emit_output() {
  local name="$1"
  shift

  if "$@"; then
    echo "$name=true" >> "$GITHUB_OUTPUT"
  else
    echo "$name=false" >> "$GITHUB_OUTPUT"
  fi
}

# Returns success if the given Bazel label can be queried from the repo root.
has_bazel_target() {
  local target="$1"

  (
    cd "$repo"
    bazel query "$target" >/dev/null 2>&1
  )
}

emit_output has_copyright_check has_bazel_target "//:copyright.check"
emit_output has_format_check has_bazel_target "//:format.check"

echo "::endgroup::"
