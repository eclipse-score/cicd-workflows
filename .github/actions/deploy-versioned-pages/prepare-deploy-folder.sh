#!/usr/bin/env bash
# *******************************************************************************
# Copyright (c) 2025 Contributors to the Eclipse Foundation
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

# Prepare the deploy folder
mkdir -p deploy_root
mkdir -p version_root
# Move the files to the deploy folder
mv "$SOURCE_FOLDER"/* deploy_root/
# Ensure that the folder is not treated as a Jekyll site
touch deploy_root/.nojekyll

# Add the target folder to the versions file
BASE_REPO="${SERVER_URL}/${REPOSITORY}.git"

echo "Fetching gh-pages from BASE_REPO: $BASE_REPO"
git remote add base "$BASE_REPO" || git remote set-url base "$BASE_REPO"
git fetch base gh-pages --depth 1

# Checkout only the versions file from gh-pages branch of the base repo
git checkout base/gh-pages -- "$VERSIONS_FILE"

new_version="$TARGET_FOLDER"

if jq -e --arg version "$new_version" 'map(select(.version == $version)) | length > 0' "$VERSIONS_FILE" > /dev/null; then
  echo "Version '$new_version' already exists in $VERSIONS_FILE"
else
  REPO_NAME=$(basename "$REPOSITORY")
  USER_NAME=$(echo "$REPOSITORY" | cut -d'/' -f1)
  GITHUB_PAGES_URL="https://${USER_NAME}.github.io/${REPO_NAME}"
  if [ "$TARGET_FOLDER" = "/" ]; then
    new_url="${GITHUB_PAGES_URL}/"
  else
    new_url="${GITHUB_PAGES_URL}/${TARGET_FOLDER}/"
  fi

  jq --arg version "$new_version" --arg url "$new_url" '. + [{"version": $version, "url": $url}]' "$VERSIONS_FILE" > tmp_versions.json
  mv tmp_versions.json "$VERSIONS_FILE"
fi
mv "$VERSIONS_FILE" version_root/
ls -al .
ls -al deploy_root
ls -al version_root
cat version_root/"$VERSIONS_FILE"
