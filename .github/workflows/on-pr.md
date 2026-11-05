# PR Checks (`on-pr.yml`)

`on-pr.yml` is the **main entry point for pull-request checks** across all S-CORE
repositories. It auto-detects what a repository contains and runs the relevant
checks — pre-commit, Python tests, Bazel format/copyright, and the Bazel bzlmod
lockfile checks — as a **single job**. Optional configured Bazel checks run in
additional matrix jobs when `workflow-config-yaml` is provided.

## Usage

Create `.github/workflows/pr.yml` in your repository:

```yaml
name: PR

on:
  pull_request:
    types: [opened, reopened, synchronize]
  merge_group:
    types: [checks_requested]

jobs:
  common:
    uses: eclipse-score/cicd-workflows/.github/workflows/on-pr.yml@main
    permissions:
      contents: read
```

That is all that is required. The workflow detects your repository's capabilities
and runs only the applicable checks — no per-repo configuration needed.

## Configured builds and tests (POC)

Provide `with.workflow-config-yaml: .github/ci.yml` to opt into configured Bazel
builds and tests. Without this input, no build or test matrix is created.
An invalid or missing configuration fails preparation.

```yaml
schema-version: 2
variants:
  - name: linux-native
    architecture:
      - name: x86_64
    os:
      - name: linux
    checks:
      tests:
        - test-type: unit
          bazel-configs: score-linux-x86_64
          bazel-build-targets: //my/library:library
          bazel-test-targets: //my/library:unit_tests
```

Use the Bazel config names and labels defined by your module. `bazel-configs` is
an explicit comma-separated list, applied in order. Target strings are split
into arguments without a shell and may include exclusions such as `-//pkg:test`.
A test entry runs its optional `bazel-build-targets` first, then its required
`bazel-test-targets`. A failed build stops that row. For compilation alone,
use `checks.builds` entries with `bazel-configs` and `bazel-build-targets`.

Each variant declares one OS and architecture. This POC supports Linux x86_64
builds/tests and Linux aarch64 builds on x86_64 runners. Each entry can declare
an optional unique `name`; otherwise it defaults to the test type or `build`.
Supported test types are `unit`, `component`, `integration`, and `sanitizer`;
these identify checks and do not install services or change Bazel semantics.

The shared workflow owns checkout, Bazel setup/cache, diagnostic flags, and
artifacts containing command reports, Bazel build events and available test logs.
The module owns its Bazel platforms, toolchains, configs and target selection.
Success requires both successful commands and build/test evidence. Cached test
passes count; an empty or entirely skipped test selection fails.
`configured-results` reports the verified checkout and per-row command outcomes.

Downstream jobs can consume the `configured-artifacts` JSON output, keyed by the
configured row ID. Each value has a `report` artifact name and a `testlogs`
artifact name (null for build-only rows). Test-log archives contain package paths
at their root, so existing documentation workflows can reconstruct Bazel labels:

```yaml
docs:
  needs: checks
  uses: eclipse-score/cicd-workflows/.github/workflows/docs.yml@main
  with:
    tests-report-artifact: ${{ fromJSON(needs.checks.outputs.configured-artifacts)['linux-native-tests-unit'].testlogs }}
```

The archive names include an invocation-specific prefix. Use the output rather
than constructing names or relying on a fixed artifact shared by all rows.

Only root Bazel workspaces with `MODULE.bazel` and `MODULE.bazel.lock` are
supported. The optional `workflow-config.bazel-test-custom-cli-args` accepts only
`--test_tag_filters=...` and `--build_tag_filters=...`. Arbitrary flags, shell
hooks and unrecognized fields fail explicitly. The supported schema 1 subset
accepts `quality-gates.tests` and adapts it to the same execution plan.
Unsupported legacy options are errors rather than silently ignored settings.

This is an experimental POC. See the [maintainer notes](../../tools/README.md)
for its bootstrap references, consumer examples and remaining validation.

## Private Bazel dependencies

`on-pr` supports the common, optional **inter-repo access** interface used by
newer workflows that need to fetch private Bazel dependencies:

| Caller field                     | Purpose                                              |
| -------------------------------- | ---------------------------------------------------- |
| `with.github-app-client-id`      | GitHub App client ID                                 |
| `secrets.github-app-private-key` | Corresponding GitHub App private key                 |
| `secrets.token`                  | Alternative token; omit the App fields when using it |

This interface is deliberately opt-in: workflows that do not need private
dependencies should not declare these fields. Existing/legacy workflow APIs are
not retrofitted to it.

Example with a GitHub App:

```yaml
jobs:
  common:
    uses: eclipse-score/cicd-workflows/.github/workflows/on-pr.yml@main
    with:
      github-app-client-id: ${{ vars.PRIVATE_DEPENDENCY_APP_ID }}
    secrets:
      github-app-private-key: ${{ secrets.PRIVATE_DEPENDENCY_APP_PRIVATE_KEY }}
    permissions:
      contents: read
```

The credentials are configured only for Bazel-capable repositories and only
when one of the optional credential values is supplied.

## What it runs

Every common check runs in **one job**, and only if its capability is detected (and it is
not explicitly skipped — see [Skipping checks](#skipping-checks)).

| Check           | Runs when                                | Command                                   |
| --------------- | ---------------------------------------- | ----------------------------------------- |
| pre-commit      | `.pre-commit-config.yaml` exists         | `uvx pre-commit run --all-files`          |
| Python tests    | `pyproject.toml` **and** `uv.lock` exist | `uv run --frozen python -m pytest`        |
| Bazel format    | `//:format.check` target exists          | `bazel test //:format.check`              |
| Bazel copyright | `//:copyright.check` target exists       | `bazel run //:copyright.check`            |
| Bzlmod tidy     | `MODULE.bazel.lock` exists               | `bazel mod tidy` + `git diff --exit-code` |
| Bzlmod lockfile | `MODULE.bazel.lock` exists               | `bazel mod deps --lockfile_mode=error`    |
| Lockfile required | `require-bzlmod-lock: true`, `MODULE.bazel` exists, lockfile missing | fails with guidance |

Capability detection looks for:

- **Bazel** — `MODULE.bazel`, `WORKSPACE`, or `WORKSPACE.bazel`
- **Bazel lockfile** — `MODULE.bazel.lock`
- **Python/uv** — both `pyproject.toml` and `uv.lock`
- **pre-commit** — `.pre-commit-config.yaml`

The presence of the configured Bazel targets (by default `//:format.check` and
`//:copyright.check`) is
detected by querying Bazel directly, so those checks only run when the target
actually exists.

> A Bazel module-name check exists but is currently disabled until its detection
> script is fixed.

## Custom Bazel targets

Callers can override the check targets with optional string inputs:

| Input              | Default               | Command                  |
| ------------------ | --------------------- | ------------------------ |
| `format-target`    | `//:format.check`      | `bazel test <target>`    |
| `copyright-target` | `//:copyright.check`   | `bazel run <target>`     |

```yaml
jobs:
  common:
    uses: eclipse-score/cicd-workflows/.github/workflows/on-pr.yml@main
    with:
      format-target: "//tools/format:check"
      copyright-target: "//tools/license:check"
    permissions:
      contents: read
```

Each input accepts one Bazel target label, not a command or a list of targets.
Both detection and execution use the configured label. A check is skipped if
its target cannot be queried. Omitting these inputs preserves the default targets.

## Skipping checks

Sometimes a capability is present but you do not want `on-pr` to run that check
(for example, you already run pre-commit a different way). Set the matching input
to `true`:

| Input               | Skips                                    |
| ------------------- | ---------------------------------------- |
| `skip-pre-commit`   | pre-commit                               |
| `skip-python-tests` | Python / pytest                          |
| `skip-format`       | configured Bazel formatting check       |
| `skip-copyright`    | configured Bazel copyright check        |
| `skip-bzlmod-lock`  | both the bzlmod tidy and lockfile checks |

All skip inputs default to `false`, so existing callers are unaffected.
`skip-bzlmod-lock` cannot be combined with
[`require-bzlmod-lock`](#requiring-the-bazel-lockfile). Example:

```yaml
jobs:
  common:
    uses: eclipse-score/cicd-workflows/.github/workflows/on-pr.yml@main
    with:
      skip-pre-commit: true
    permissions:
      contents: read
```

## Requiring the Bazel lockfile

By default, the bzlmod tidy and lockfile checks only run when `MODULE.bazel.lock`
exists. If a repository uses bzlmod but the lockfile is missing (never committed,
deleted, or git-ignored), those checks are **silently skipped**.

Set `require-bzlmod-lock: true` to turn that into a failure:

| Input                | Default | Meaning                                                                                  |
| -------------------- | ------- | ---------------------------------------------------------------------------------------- |
| `require-bzlmod-lock` | `false` | Fail the PR check if `MODULE.bazel` exists but `MODULE.bazel.lock` does not             |

- Applies only to bzlmod repositories (`MODULE.bazel` present). `WORKSPACE`-only
  repositories are unaffected, since they have no lockfile.
- It only enforces that the lockfile **exists**. Whether it is tidy and up to date
  is still verified by the regular bzlmod tidy and lockfile checks.
- It is mutually exclusive with `skip-bzlmod-lock`. Setting both is a
  configuration error: the job fails immediately and no other checks run.

```yaml
jobs:
  common:
    uses: eclipse-score/cicd-workflows/.github/workflows/on-pr.yml@main
    with:
      require-bzlmod-lock: true
    permissions:
      contents: read
```

## Behavior notes

- **All common checks run, even if one fails.** A failing check does not stop the ones
  after it — each reports its own pass/fail, and the job as a whole fails if any
  check failed.
- **Failures come with guidance.** When a check fails, its own log explains what
  failed, how to reproduce it locally, and how to fix it. No annotations, run
  summaries, or PR comments are created.
- **Shared common-check checkout stays clean.** Common checks share one checkout, so a check that
  rewrites files (pre-commit, `bazel mod tidy`) restores the working tree
  afterwards. Later git-diff-based checks therefore see the original PR state, not
  the auto-fixed one.
- **Sequential common checks.** Common checks run one after another in a single
  runner. Configured Bazel rows run in separate matrix jobs. This trades a little wall-clock time for far less runner
  spin-up overhead and no skipped-job clutter in the PR checks UI.
- **Nothing heavy installs unless needed.** `uv` and Bazel are set up only when a
  relevant capability is detected, and Bazel is set up **once** for the common
  checks. Each opted-in configured row has its own Bazel/cache setup.
- **`pull_request_target` is refused.** The workflow exits immediately on that
  event, which would otherwise expose repository secrets to untrusted fork code.
  Use `pull_request` or `merge_group` instead.

## Permissions

No additional permissions required. Standard `contents: read` is sufficient.

```yaml
permissions:
  contents: read
```

## Runner selection

`on-pr` uses the standard S-CORE runner-label resolution:
`runner-labels` input → `runner_labels_ghub24_standard_x64` → `REPO_RUNNER_LABELS` → `ubuntu-24.04`. See
[Runner Selection Logic](../../README.md#️-runner-selection-logic) for details.

## Checkout and results

By default, the workflow checks the calling repository using the event revision
selected by `actions/checkout`. Optional inputs select a different checkout:

| Input        | Default             | Meaning                                  |
| ------------ | ------------------- | ---------------------------------------- |
| `repository` | Calling repository  | Repository to check, as `owner/name`      |
| `ref`        | Checkout default    | Commit SHA, tag or branch to check       |

When `repository` selects a different repository and `ref` is omitted,
`actions/checkout` selects that repository's default branch. Permissions and
credentials must allow access to the selected repository.

The `check-results` output is a JSON object containing the checked-out `commit`
and a `checks` map with `pre-commit`, `python-tests`, `format`, `copyright`,
`require-lock`, `module-tidy` and `lockfile` step outcomes. A skipped check is
reported as `skipped`. Failed checks still fail the workflow.
