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
  docs-publish:
    if: github.event.workflow_run.conclusion == 'success'
    uses: eclipse-score/cicd-workflows/.github/workflows/docs-publish.yml@main
    with:
      # Omit for GitHub Actions Pages deployments. Set to "legacy" when
      # GitHub Pages is configured to publish from the gh-pages branch.
      deployment_type: workflow
    permissions:
      actions: read
      contents: write
      id-token: write
      pages: write
      pull-requests: write
```

The value in `workflows` must exactly match the build workflow's `name`.
Merge-queue builds are validated but not published.

## Publish an existing central build manually

When `on-pr.yml` already builds Docs, a publishing workflow can reuse its
`github-pages` artifact directly. It does not need a download/re-upload job:

```yaml
name: Publish Documentation
on:
  workflow_dispatch:
    inputs:
      source-run-id:
        description: Optional successful build run ID
        type: string
        default: ''
jobs:
  docs-publish:
    uses: eclipse-score/cicd-workflows/.github/workflows/docs-publish.yml@main
    with:
      source-workflow: on-pr.yml
      source-run-id: ${{ inputs.source-run-id }}
    permissions:
      actions: read
      contents: write
      pages: write
      id-token: write
      pull-requests: write
```

`source-workflow` is the build workflow's filename, not its display name. With
no `source-run-id`, the publisher selects a successful push/manual build of the
exact commit and ref selected for the publishing dispatch. An explicit run ID
receives the same validation: completed successfully, same repository, workflow,
commit and ref, and a push or manual trigger. PR and merge-queue artifacts cannot
be selected through this manual path. Missing or expired artifacts fail publication.

This mode also supports repositories owned by `eclipse-score`: the build stays
in its original run and only the finished artifact enters the publishing job.
Existing `workflow_run` callers and direct callers in trusted internal repositories
keep their behavior when both inputs are omitted. Build selection inputs are
supported only for `workflow_dispatch`; use the existing triggering run for
`workflow_run` publication.

## Trusted internal repositories

Repositories that do not accept fork-origin pull requests may run build and
publish as dependent jobs in the same workflow. This direct configuration is
not permitted for repositories owned by `eclipse-score`.

```yaml
jobs:
  docs:
    uses: eclipse-score/cicd-workflows/.github/workflows/docs.yml@main
  docs-publish:
    needs: docs
    uses: eclipse-score/cicd-workflows/.github/workflows/docs-publish.yml@main
    with:
      # Omit for GitHub Actions Pages deployments. Set to "legacy" when
      # GitHub Pages is configured to publish from the gh-pages branch.
      deployment_type: workflow
    permissions:
      actions: read
      contents: write
      id-token: write
      pages: write
      pull-requests: write
```

For repositories using the legacy "Deploy from a branch" Pages source, set
`deployment_type: legacy`; the workflow updates `gh-pages` but does not run the
GitHub Actions Pages deployment.

For a tag push or published release, the publish workflow uses the triggering
run's source ref (`github.event.workflow_run.head_branch`) as the documentation
version. Do not configure both events for the same release unless publishing
the same version twice is intended.

## Build inputs

| Input | Default | Description |
| ----- | ------- | ----------- |
| `retention-days` | `1` | Number of days to retain the documentation artifact. |
| `bazel-target` | `//:docs` | Bazel target invoked with `bazel run`. |
| `tests-report-artifact` | empty | Optional artifact downloaded to `tests-report` before the docs build. |
| `coverage-report-artifact` | empty | Optional artifact containing plain LCOV as `coverage.lcov` or `coverage_report.dat` at its root, downloaded to `coverage-report` before the docs build. |
| `deployment_type` | `workflow` | Deprecated compatibility input; publishing is always handled by `docs-publish.yml`. |

## Coverage in documentation

To display line and branch coverage in module verification reports, pass a
coverage artifact to `coverage-report-artifact` and use a docs-as-code version
that supports coverage reports. Associate each component's source files with
its documentation bundle using `primary_need_id` and `code_targets`.

The C++ coverage workflow includes `coverage.lcov` at the root of its
`<repository-name>_cpp_coverage<artifact-name-suffix>` artifact, alongside the
HTML report. Connect both jobs in the same calling workflow:

```yaml
jobs:
  coverage:
    uses: eclipse-score/cicd-workflows/.github/workflows/cpp-coverage.yml@main
    permissions:
      contents: read
    with:
      bazel-target: "//..."
      extra-bazel-flags: "--combined_report=lcov"

  docs:
    needs: coverage
    uses: eclipse-score/cicd-workflows/.github/workflows/docs.yml@main
    permissions:
      contents: read
    with:
      coverage-report-artifact: ${{ format('{0}_cpp_coverage', github.event.repository.name) }}
```

Keep any repository-specific coverage configuration and instrumentation flags.
If the coverage job sets `artifact-name-suffix`, append that same suffix to
`coverage-report-artifact`. Both jobs must run in the same workflow run;
`needs: coverage` ensures the artifact is available before the docs build starts.
By default, a failed coverage job also prevents the dependent docs job from running.

For Rust or another coverage producer, provide an artifact with a plain UTF-8
LCOV file named `coverage.lcov` or `coverage_report.dat` at its root, and pass
that artifact's name to `coverage-report-artifact`. When using the Rust workflow,
configure `coverage-target` to generate this file directly in the uploaded
directory selected by `coverage-artifact-path`. HTML alone or an archive inside
the artifact cannot supply the measurements.

LCOV source paths must match the documentation bundles' selected code files.
Use repository-relative paths (or supported Bazel external-repository paths)
from the same workspace context; absolute runner paths are unsupported.

When `coverage-report-artifact` is empty, no report is downloaded and documentation
can build without measurements. When it is set, a missing artifact or the absence
of both `coverage.lcov` and `coverage_report.dat` fails the docs job.

## Publishing behavior

Documentation from the default branch is published under its branch name, such
as `/main/`. Pull requests are published under `/pr-<number>/`; a link to that
preview is added to the pull request. Tags and releases are published under the
tag name, such as `/v1.2.3/`. The workflow initializes the `gh-pages` branch
when necessary and maintains its `versions.json` file.

## Configured central builds

`on-pr.yml` can orchestrate this workflow after configured tests and coverage.
See [coverage and documentation configuration](on-pr.md#add-coverage-and-documentation)
for a complete example. This path checks out the observed preparation commit,
resolves report artifacts automatically and leaves publishing separate.

Direct callers may also set `repository` and `ref` to select a checkout.
Omitting them preserves the caller event defaults. `bazel-label` selects one
Bazel run target with `--lockfile_mode=error`, using argv instead of the legacy
`bazel-target` command string. Repository context is always supplied through
`GITHUB_REPOSITORY` in the Docs subprocess environment. The deprecated
`github-context` boolean is accepted for compatibility; it adds no CLI arguments. Existing
`bazel-target` callers keep their established behavior when `bazel-label` is empty.
