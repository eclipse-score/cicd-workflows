# PR Checks (`on-pr.yml`)

`on-pr.yml` is the **main entry point for pull-request checks** across all S-CORE
repositories. It auto-detects what a repository contains and runs the relevant
checks — pre-commit, Python tests, Bazel format/copyright, and the Bazel bzlmod
lockfile checks — as a **single job**.

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

Every check runs in **one job**, and only if its capability is detected (and it is
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

- **All checks run, even if one fails.** A failing check does not stop the ones
  after it — each reports its own pass/fail, and the job as a whole fails if any
  check failed.
- **Failures come with guidance.** When a check fails, its own log explains what
  failed, how to reproduce it locally, and how to fix it. No annotations, run
  summaries, or PR comments are created.
- **Shared checkout stays clean.** All checks share one checkout, so a check that
  rewrites files (pre-commit, `bazel mod tidy`) restores the working tree
  afterwards. Later git-diff-based checks therefore see the original PR state, not
  the auto-fixed one.
- **Sequential.** Checks run one after another in a single runner rather than in
  parallel across jobs. This trades a little wall-clock time for far less runner
  spin-up overhead and no skipped-job clutter in the PR checks UI.
- **Nothing heavy installs unless needed.** `uv` and Bazel are set up only when a
  relevant capability is detected, and Bazel is set up **once** and shared by all
  Bazel checks.
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

## Consumer compatibility tests

Changes to this workflow are tested against pinned real SCORE repositories in
[the consumer catalog](../ci/consumers.yml). The suite calls the workflow from
the candidate PR, checks out each catalog commit and uses the existing default
checks. The complete workflow must succeed, and every entry in `required-checks`
must report successful execution on the pinned checkout. Additional checks may
run or be skipped without changing this minimum contract.

The catalog covers application modules, C++ foundations, documentation and
coverage tooling, Python tools, registry maintenance and GitHub Actions. These
profiles exercise both standard root targets and documented exceptions, as well
as repositories with and without Bazel or standalone Python tests.

Add consumers as entries in the shared catalog:

```yaml
- name: module-template
  repository: eclipse-score/module_template
  ref: <full-commit-sha>
  required-checks: [pre-commit, format, copyright, module-tidy, lockfile]
```

`required-checks` is a tested minimum compatibility profile, not an exhaustive
assessment of a repository. An omitted check does not imply a missing capability.
Update pins and minimum requirements together after reviewing consumer changes.

Repository-specific exceptions belong in the same catalog. Optional `overrides`
accepts `format-target`, `copyright-target` and the existing `skip-*` inputs;
every override requires a `value` and a nonempty `reason`. For example:

```yaml
overrides:
  format-target:
    value: //tools:format.check
    reason: The repository exposes its formatter only from //tools.
```

Explicit skip switches cannot disable a required check. Leave `overrides` absent
for a standard call. These exceptions make differences visible without requiring
separate consumer files or scripts.

The suite needs no consumer-specific files, write permissions or secrets. The workflow repository's `CI success` job covers
its own common checks and all consumer scenarios.

The optional `source-repository` and `source-ref` inputs select the repository
and revision to check. Omit them for ordinary calls to check the caller event.
The `check-results` output is JSON containing `commit` and a `checks` map with
`pre-commit`, `python-tests`, `format`, `copyright`, `require-lock`, `module-tidy`
and `lockfile` outcomes. Failed checks still fail the workflow; these outputs
provide observations for assertions rather than suppressing failures.

### Daily snapshot refresh

The `Update consumer pins` workflow runs daily at 04:43 UTC and can also be
started manually on `main`. It resolves each consumer's default branch once,
changes only commit pins and runs the same consumer scenarios against that exact
candidate catalog. Required checks and documented exceptions stay unchanged.

When every consumer succeeds, the workflow commits the catalog and attempts a
normal push to `main`. A concurrent change on `main` or branch protection rejects
the push; it is never forced. Failed consumer checks or a rejected push instead
create or update one PR on `bot/consumer-pin-update` for review. An API failure
leaves the catalog unchanged, and a run without new commits publishes nothing.

Publishing reuses `SCORE_BOT_CLASSIC_PAT`, already used by Renovate. Consumer
checks run separately with read-only permissions and receive no bot credential.
The fallback PR triggers the normal PR suite and is not merged automatically.
