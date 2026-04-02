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
if [[ -z "${GITHUB_OUTPUT:-}" ]]; then
  echo "WARNING: This action is designed to be used in a GitHub Actions workflow. Running in 'local mode'." >&2
  GITHUB_OUTPUT=/dev/stdout
fi

echo "::group:: Detecting repository capabilities"

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

# Returns success if any provided file path exists.
any_file_exists() {
  local path
  for path in "$@"; do
    [[ -f "$path" ]] && return 0
  done
  return 1
}

# Returns success only if every provided file path exists.
all_files_exist() {
  local path
  for path in "$@"; do
    [[ -f "$path" ]] || return 1
  done
  return 0
}

emit_output has_bazel any_file_exists \
  "$repo/MODULE.bazel" \
  "$repo/WORKSPACE" \
  "$repo/WORKSPACE.bazel"
emit_output has_bazel_lock any_file_exists \
  "$repo/MODULE.bazel.lock"
emit_output has_devcontainer any_file_exists \
  "$repo/.devcontainer/devcontainer.json" \
  "$repo/devcontainer.json"
emit_output has_python_uv all_files_exist \
  "$repo/pyproject.toml" \
  "$repo/uv.lock"
emit_output has_precommit any_file_exists \
  "$repo/.pre-commit-config.yaml"

echo "::endgroup::"
