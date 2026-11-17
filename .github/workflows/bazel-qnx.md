# Run QNX checks with Bazel

Use the shared `bazel.yml` workflow to build or test with QNX. This guide covers
SDK and VM setup, PR approval, optional label filtering, manual runs and merge
queues. For general Bazel usage and the full input, secret and output reference,
see the [Bazel workflow guide](bazel.md).

## Set up QNX and virtual machines

For QNX tests in a VM, use the following inputs and secrets. All three secrets
must be available to the job; `qnx-license` contains a base64-encoded license file.

```yaml
with:
  setup-qnx-sdp: true
  setup-qemu: true
  bazel-configs: score-qnx-x86_64
  test-targets: //score/...
  cache-key: qnx-x86_64
secrets:
  qnx-license: ${{ secrets.SCORE_QNX_LICENSE }}
  qnx-user: ${{ secrets.SCORE_QNX_USER }}
  qnx-password: ${{ secrets.SCORE_QNX_PASSWORD }}
```

The setup switches are independent and default to `false`. QNX setup configures
licensing and download access. Its download helper defaults to
`.github/tools/qnx_credential_helper.py` in the checkout root. Set
`qnx-credential-helper` for a different path, or to `''` to disable it. If you use
`qnx-license-server`, a root workspace's `.bazelrc` must import `user.bazelrc`.

QEMU setup installs `qemu-system` and `qemu-utils`, including `qemu-img`, and
enables KVM access. The runner needs KVM, sudo and udev. Your Bazel targets or test
wrapper start and stop the VM. Enable QEMU setup for other platforms as needed;
QNX build-only jobs can omit it. For Linux user-mode emulation, use
`apt-packages: qemu-user`.

## Download SDP release assets from GitHub

For SDP release downloads from `eclipse-score/qnx_sdp_pkg`, pass the shared
SCORE secret `SCORE_BOT_QNX_PKG_PAT` through `github-token`. Supply it alongside
the three QNX secrets in the calling job:

```yaml
secrets:
  qnx-license: ${{ secrets.SCORE_QNX_LICENSE }}
  qnx-user: ${{ secrets.SCORE_QNX_USER }}
  qnx-password: ${{ secrets.SCORE_QNX_PASSWORD }}
  github-token: ${{ secrets.SCORE_BOT_QNX_PKG_PAT }}
```

The workflow uses this token for Git access and exports it as `GH_TOKEN` for
subsequent download commands. If you use GitHub App credentials instead, its
installation token is selected in preference to `github-token`; the App must
have read access to `qnx_sdp_pkg` as well as any other private dependencies.

## Run QNX checks on pull requests

Use this setup to run QNX checks on internal and fork PRs. Internal PRs run
automatically; fork PRs require approval by a committer before code runs with
QNX credentials. A merge queue and PR labels are optional.

### Configure fork approval with Otterdog

Before enabling the workflow, add the following `environments` field to the
repository's Otterdog JSONnet object, using the existing `orgs` import from the
Otterdog defaults:

```jsonnet
environments+: [
  orgs.newEnvironment('workflow-approval') {
    deployment_branch_policy: 'all',
    reviewers+: ['@eclipse-score/automotive-score-committers'],
  },
],
```

Apply this configuration first so that `workflow-approval` requires approval
from the `automotive-score-committers` team. Merely naming the environment in a
workflow does not protect it. These environment approvals are separate from PR
reviews. The configuration matches the organization's
[existing QNX environments](https://github.com/eclipse-score/.eclipsefdn/blob/main/otterdog/eclipse-score.jsonnet).

Store `SCORE_QNX_LICENSE`, `SCORE_QNX_USER` and `SCORE_QNX_PASSWORD` as repository
or organization secrets. Secrets stored only on `workflow-approval` are not
available to the build. See [Set up QNX and virtual machines](#set-up-qnx-and-virtual-machines)
for the license format and VM setup.

### Call the workflow

Add this workflow to the repository's trusted default branch. Replace the Bazel
configuration and targets with your module's values, and replace `<sha>` with
the full commit SHA of the reviewed workflow version.

```yaml
name: QNX checks

on:
  pull_request_target:
    types: [opened, synchronize, reopened]
  workflow_dispatch:

permissions:
  contents: read

# A new PR revision cancels pending approval or execution of the previous one.
concurrency:
  group: qnx-${{ github.workflow }}-${{ github.event.pull_request.number || github.ref }}
  cancel-in-progress: true

jobs:
  qnx:
    # Fork PRs require environment approval; internal PRs run automatically.
    uses: eclipse-score/cicd-workflows/.github/workflows/bazel.yml@<sha>
    with:
      setup-qnx-sdp: true
      build-targets: //...
      bazel-configs: x86_64-qnx
      cache-key: qnx-x86_64
      # For VM tests, also supply test-targets and setup-qemu: true.
    secrets:
      qnx-license: ${{ secrets.SCORE_QNX_LICENSE }}
      qnx-user: ${{ secrets.SCORE_QNX_USER }}
      qnx-password: ${{ secrets.SCORE_QNX_PASSWORD }}
```

For a fork PR, open the pending `approval` job and review the linked commit,
including its Bazel rules and QNX credential helper, before approving execution.
Rejection or cancellation leaves the build skipped. Each new PR revision needs
approval again. Internal PRs run without this additional approval.

The workflow tests the exact PR head commit recorded in the triggering event.
Rerunning an older run still tests that older commit. Keep ordinary
`pull_request` checks to test the PR's merge revision; `pull_request_target`
checks the head commit and reports in the base context. QNX fork checks require
`pull_request_target`; using `pull_request` for them fails with an explanation.
`pull_request_target` is supported only with `setup-qnx-sdp: true`.

### Start QNX checks manually (optional)

The example includes `workflow_dispatch`. In Actions, select **QNX checks**,
choose an internal feature or release branch, and run the workflow. The caller
must exist on the default branch to offer manual runs, and on the selected
branch with the settings needed to build that branch.

A dispatch uses the selected branch's caller workflow and event commit, so a
release branch can retain its build settings and pinned reusable-workflow
version. By contrast, `pull_request_target` uses the default branch's workflow
even for PRs targeting a release branch. Dispatch runs without fork approval
or label filtering. It does not select code in a fork or grant a fork access to
upstream secrets; use the approved PR-target path for fork QNX checks.

Manual checks provide early feedback. If you use a merge queue, its required
checks still need to validate the final merge-group revision.

### Select fork PRs by label (optional)

Without a label filter, every fork PR waits for approval. To request QNX builds
only for selected fork PRs, add `fork-pr-label` to the calling job's `with:` block:

```yaml
with:
  fork-pr-label: test-qnx
  # Keep the QNX settings from the example above.
```

Also extend the caller's PR event types so label changes trigger a new run:

```yaml
on:
  pull_request_target:
    types: [opened, synchronize, reopened, labeled, unlabeled]
```

| PR | Without a label filter | With `fork-pr-label: test-qnx` |
| --- | --- | --- |
| Internal PR | Runs automatically | Runs automatically |
| Fork PR without `test-qnx` | Waits for approval | Skipped |
| Fork PR with `test-qnx` | Waits for approval | Waits for approval |

Label matching is case-insensitive. Labels select which fork PRs need a build;
they do not replace approval. The workflow reads labels from the event, so no
additional token permission is needed. Pushes, manual dispatches and merge groups
run independently of the filter when their triggers are configured.

This input implements one policy: automatic internal checks and optional fork
checks. To make internal PR checks opt-in too, add this condition to the calling
`qnx` job and keep the `labeled` and `unlabeled` triggers:

```yaml
jobs:
  qnx:
    # Labels select all early PR checks; queue and manual runs always execute.
    if: >-
      github.event_name != 'pull_request_target' ||
      contains(github.event.pull_request.labels.*.name, 'test-qnx')
    # Keep uses, with and secrets from the QNX example above.
```

With label filtering enabled, unselected PR builds are skipped. Rejected fork
approval also leaves the build skipped. GitHub accepts skipped jobs as satisfied
required checks, so requiring `Bazel` does not enforce an early build on every
PR. In a merge queue, QNX runs regardless of labels; configure its `Bazel` check
as required so the build must succeed before merging.

### Use a merge queue (optional)

To validate queued changes with QNX, add `merge_group` to the caller's triggers:

```yaml
on:
  pull_request_target:
    types: [opened, synchronize, reopened]
  merge_group:
    types: [checks_requested]
  workflow_dispatch:
```

If you use the label filter, keep `labeled` and `unlabeled` in the PR event types.
The existing `qnx` job builds the queue's merge-group revision (`github.sha`).
Every merge group runs, regardless of PR labels, without additional fork approval.

In the repository's Otterdog `rulesets`, enable the queue with
`required_merge_queue` and configure `required_status_checks` to require the
QNX `Bazel` check emitted by the caller for merge groups. See the
[Otterdog ruleset fields](https://otterdog.readthedocs.io/en/latest/reference/organization/repository/ruleset/)
for these settings. A failed check blocks the queued change. Use the merge-group
check for queue validation; the `pull_request_target` check does not test the
queue's combined revision.

## Replace an existing `qnx-build.yml` call

Enable `setup-qnx-sdp: true` and translate the existing call as follows. Keep
the protected `workflow-approval` environment and explicitly forward secrets.

| Existing QNX call | Shared Bazel call |
| --- | --- |
| `bazel-target` (default `//...`) | `build-targets: //...`; no default target is selected. |
| `bazel-config` (default `x86_64-qnx`) | `bazel-configs: x86_64-qnx`; no default configuration is selected. |
| `bazel-test-target` | `test-targets`; also set `setup-qemu: true` for VM tests. |
| `extra-bazel-flags` / `extra-bazel-test-flags` | `build-args` / `test-args`; use `bazel-args` for options shared by both phases. |
| `credential-helper` | `qnx-credential-helper`; same default path, relative to the checkout root. |
| `score-qnx-license-server` | `qnx-license-server` |
| `bazel-disk-cache` | `cache-key`; choose a stable name per job or matrix variant. Existing cache names are not preserved. |
| `test-log-artifact-name` (default `qnx-testlogs`) | `artifact-name`; omitted names are generated per run. Update artifact consumers or set the old name explicitly. |
| `disk-space-level` (default `4`) | `disk-cleanup-level` (default `2`); set `4` to retain the cleanup level. |
| `fetch-attempts` / `fetch-retry-delay` | No corresponding inputs; recognized dependency-download failures are retried automatically. |
| `working-directory`, `environment-name`, `runner-labels`, `github-app-client-id` | Same input names. |
| Secrets `score-qnx-license`, `score-qnx-user`, `score-qnx-password` | Secrets `qnx-license`, `qnx-user`, `qnx-password`; the backing repository secret names can stay unchanged. |
| Secret `token` | Secret `github-token` for private GitHub dependencies. |
| Secret `github-app-private-key` | Same secret name. |
| Secret `qnx-sdp-github-token` | Pass `secrets.SCORE_BOT_QNX_PKG_PAT` as `github-token`; it is exported as `GH_TOKEN` for SDP release downloads. |
| Build check `Build QNX target` | Build check `Bazel`; update required-check entries to the full reported caller/check name. |

For SDP release downloads, follow [Download SDP release assets from GitHub](#download-sdp-release-assets-from-github).

Commands now use `--lockfile_mode=error`: update and commit the module's lockfile
before adopting the workflow. Per-phase arguments remain distinct; flags in
`build-args` are not automatically applied to tests. Argument values support
quoting but no shell expansion. The old workflow automatically installed QEMU
when tests were selected; the new workflow requires `setup-qemu: true` for VM
setup. QNX fork PRs require `pull_request_target` and execute the immutable head
commit from the event. Merge groups execute their merge-group commit.

Update required-check names and any existing collector dependencies after
replacing the call. In a merge queue, require the actual QNX build to succeed.
