# ADR 001: Use Shell Scripts Instead of Inline GitHub Actions Logic

**Date:** 2025-05-28  
**Status:** Accepted

## Decision

Once shell logic in GitHub Actions workflows grows beyond a few lines, we should move it to standalone shell scripts. This applies to all workflows in the `eclipse-score/cicd-workflows` repository.

## Reasoning

Inline shell in YAML becomes hard to read and maintain once it’s more than a few lines. Using shell scripts has several advantages:

- Easier to read and update
- Can be run and debugged locally
- Supports comments and formatting
- Works better with shell linters like `shellcheck`
- Can be reused across multiple jobs or workflows

## How We Do It

- Logic is placed in `.github/scripts/*.sh`
- Inputs are passed via environment variables
- Scripts are made executable via `chmod +x`
- GitHub Actions workflows call the script directly

Example usage in `.github/workflows/`:

```yaml
env:
  BAZEL_TARGET: ${{ inputs.bazel-target }}
  REPO_URL: ${{ inputs.repo-url }}
  ...
run: ./.github/scripts/license-check.sh
```
