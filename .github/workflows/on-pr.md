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

Capability detection looks for:

- **Bazel** — `MODULE.bazel`, `WORKSPACE`, or `WORKSPACE.bazel`
- **Bazel lockfile** — `MODULE.bazel.lock`
- **Python/uv** — both `pyproject.toml` and `uv.lock`
- **pre-commit** — `.pre-commit-config.yaml`

The presence of the Bazel `//:format.check` and `//:copyright.check` targets is
detected by querying Bazel directly, so those checks only run when the target
actually exists.

> A Bazel module-name check exists but is currently disabled until its detection
> script is fixed.

## Skipping checks

Sometimes a capability is present but you do not want `on-pr` to run that check
(for example, you already run pre-commit a different way). Set the matching input
to `true`:

| Input               | Skips                                    |
| ------------------- | ---------------------------------------- |
| `skip-pre-commit`   | pre-commit                               |
| `skip-python-tests` | Python / pytest                          |
| `skip-format`       | `bazel //:format.check`                  |
| `skip-copyright`    | `bazel //:copyright.check`               |
| `skip-bzlmod-lock`  | both the bzlmod tidy and lockfile checks |

All inputs default to `false`, so existing callers are unaffected. Example:

```yaml
jobs:
  common:
    uses: eclipse-score/cicd-workflows/.github/workflows/on-pr.yml@main
    with:
      skip-pre-commit: true
    permissions:
      contents: read
```

## Behavior notes

- **All checks run, even if one fails.** A failing check does not stop the ones
  after it — each reports its own pass/fail, and the job as a whole fails if any
  check failed.
- **Failures come with guidance.** When a check fails it writes a block to the
  run's **Summary** page explaining what failed, how to reproduce it locally, and
  how to fix it — so a red check is actionable without digging through logs.
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

No additional permissions required.
Standard `contents: read` is sufficient for all checks.

```yaml
permissions:
  contents: read
```

## Runner selection

`on-pr` uses the standard S-CORE runner-label resolution:
`runner_labels_ghub24_standard_x64` → `REPO_RUNNER_LABELS` → `ubuntu-24.04`. See
[Runner Selection Logic](../../README.md#️-runner-selection-logic) for details.
