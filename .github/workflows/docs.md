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
| `tests-report-artifacts` | empty | Artifact names or `*` patterns, one per line, to combine into `tests-report`; mutually exclusive with `tests-report-artifact`. |
| `coverage-report-artifact` | empty | Optional artifact containing plain LCOV as `coverage.lcov` or `coverage_report.dat` at its root, downloaded to `coverage-report` before the docs build. |
| `deployment_type` | `workflow` | Deprecated compatibility input; publishing is always handled by `docs-publish.yml`. |

## Build outputs

| Output | Description |
| ------ | ----------- |
| `artifact-name` | Name of the uploaded documentation artifact (`github-pages`). Use it to process the built documentation in the calling workflow, for example to attach it to a release. |

## Multiple test report artifacts

Matrix jobs can upload their reports separately and pass selected artifact names
directly to Docs. No intermediate merge job or combined artifact upload is needed:

```yaml
jobs:
  docs:
    needs: tests
    uses: eclipse-score/cicd-workflows/.github/workflows/docs.yml@main
    with:
      tests-report-artifacts: |
        tests-bazel-linux
        tests-pytest-*
```

Set only one of `tests-report-artifact` and `tests-report-artifacts`. Leave both
empty to build without downloading test reports.

Names and patterns refer to the current workflow run. Put one selection on each
line; surrounding whitespace and blank lines are ignored. Use letters, numbers,
underscores, hyphens and periods, with `*` matching zero or more characters.
Matching is case-sensitive. Other glob syntax such as `?` and bracket expressions
is unsupported.

Upload reports with `actions/upload-artifact` using its default ZIP format, and
include the producing jobs in `needs` so their uploads finish before Docs starts.
At least one artifact must match the supplied names and patterns. Individual
lines may match nothing; overlapping patterns include an artifact only once.
The build fails if no artifacts match or a selected artifact contains no files.

Reports are combined in `tests-report` using their paths within each artifact.
For example, `unit/test.xml` becomes `tests-report/unit/test.xml`, without an
extra artifact-name directory. XML files, logs and other companion files keep
their names and contents.

Files with identical relative paths and bytes are accepted once. Different
contents at the same path fail before the documentation build, including conflicts
with files already in the checkout's `tests-report` directory. Symlinks and
file/directory conflicts also fail. Select a representative platform run when
multiple platforms produce different reports at the same paths, or arrange for
non-overlapping paths in the producing jobs.

Use a runner at version 2.336.0 or newer for multiple report artifacts.

This combines files, not testcase results. The docs-as-code parser currently
looks for files named `test.xml`. A pytest producer can use paths such as
`docs-bzl/3.12/cached/test.xml` and `docs-bzl/3.12/runtime/test.xml`; copying other
XML filenames does not make the parser consume them. Producers must also provide
the testcase metadata required by their documentation parser. Multiple Python
versions of the same testcase may need a representative selection or parser-level
deduplication even when their report paths differ.

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
