#!/usr/bin/env bash
set +e  # Allow execution to continue even if a command fails

# Expect these environment variables to be set from the GitHub Actions workflow:
#   REPO_URL (optional, defaults to the current repository URL)
#   BAZEL_TARGET
#   DASH_API_TOKEN (optional)
# These are set by GitHub Actions:
#   GITHUB_SERVER_URL
#   GITHUB_REPOSITORY

REPO_URL="${REPO_URL:-}"
if [[ -z "$REPO_URL" ]]; then
  REPO_URL="${GITHUB_SERVER_URL}/${GITHUB_REPOSITORY}"
fi

OUTPUT=""
EXIT_CODE=0

CMD="bazel ${BAZEL_TARGET} -- -review -project automotive.score -repo $REPO_URL"

if [[ -n "${DASH_API_TOKEN:-}" ]]; then
  CMD+=" -token $DASH_API_TOKEN"
fi

echo "Running: $CMD"

CHECK_OUTPUT=$($CMD 2>&1)
CHECK_EXIT_CODE=$?

OUTPUT="[License Check Output]\n$CHECK_OUTPUT"
if [ "$CHECK_EXIT_CODE" -ne 0 ]; then
  EXIT_CODE=$CHECK_EXIT_CODE
fi

echo -e "$OUTPUT" | tee license-check-output.txt
echo "exit_code=$EXIT_CODE" >> $GITHUB_ENV
echo "output<<EOF" >> $GITHUB_ENV
echo -e "$OUTPUT" >> $GITHUB_ENV
echo "EOF" >> $GITHUB_ENV
