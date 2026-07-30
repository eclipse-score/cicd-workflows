# Documentation Workflows (`docs.yml` and `docs-publish.yml`)

The documentation workflow is split into two stages:

1. `docs.yml` builds documentation in the unprivileged pull request or push
   workflow and uploads a `github-pages` artifact.
2. `docs-publish.yml` runs after a successful build, retrieves that artifact,
   and publishes it to GitHub Pages with the required write permissions.

Keeping the build and publishing stages separate prevents untrusted pull request
code from running with repository write permissions.

## Usage

Create `.github/workflows/docs.yml` in the consuming repository:

```yaml
name: Documentation CI

on:
  pull_request:
  push:
    branches:
      - main
    tags:
      - "v*"
  release:
    types: [published]
  merge_group:
    types: [checks_requested]

jobs:
  docs:
    uses: eclipse-score/cicd-workflows/.github/workflows/docs.yml@main
    with:
      retention-days: 3
      # bazel-target: "//:docs" # optional, default shown
```

Create `.github/workflows/docs-publish.yml` alongside it:

```yaml
name: Publish Documentation

on:
  workflow_run:
    workflows: ["Documentation CI"]
    types: [completed]

jobs:
  docs-deploy:
    if: github.event.workflow_run.conclusion == 'success'
    uses: eclipse-score/cicd-workflows/.github/workflows/docs-publish.yml@main
    permissions:
      actions: read
      contents: write
      id-token: write
      pages: write
      pull-requests: write
```

The value in `workflows` must exactly match the build workflow's `name`.
Merge-queue builds are validated but not published.

For a tag push or published release, the publish workflow resolves the unique
Git tag that points to the completed build's commit and uses that tag as the
documentation version. Do not configure both events for the same release
unless publishing the same version twice is intended.

## Build inputs

| Input | Default | Description |
| ----- | ------- | ----------- |
| `retention-days` | `1` | Number of days to retain the documentation artifact. |
| `bazel-target` | `//:docs` | Bazel target invoked with `bazel run`. |
| `tests-report-artifact` | empty | Optional artifact downloaded to `tests-report` before the docs build. |
| `deployment_type` | `workflow` | Deprecated compatibility input; publishing is always handled by `docs-publish.yml`. |

## Publishing behavior

Documentation from the default branch is published under its branch name, such
as `/main/`. Pull requests are published under `/pr-<number>/`; a link to that
preview is added to the pull request. Tags and releases are published under the
tag name, such as `/v1.2.3/`. The workflow initializes the `gh-pages` branch
when necessary and maintains its `versions.json` file.
