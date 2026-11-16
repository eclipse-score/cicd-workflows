# Build, test and collect coverage with Bazel

Use `bazel.yml` to build, test or measure coverage on a Linux runner. You supply
your targets and Bazel configurations; the workflow handles setup, caching and
report uploads. Start with
[Run a job](#run-a-job), or look up a setting in the [Reference](#reference).

## Run a job

Create `.github/workflows/bazel-checks.yml` in your module with the following
content, or add the `tests` job to an existing workflow. Replace the targets and
configuration with ones that already work in your module.

```yaml
name: Bazel checks

on:
  pull_request:
  workflow_dispatch:

permissions:
  contents: read

jobs:
  tests:
    uses: eclipse-score/cicd-workflows/.github/workflows/bazel.yml@qnx-fork-approval
    with:
      test-targets: //score/...
      bazel-configs: x86_64-linux
      cache-key: linux-x86_64
```

The examples use the review branch. Pin the reviewed commit SHA when adopting
the workflow. `//score/...` selects targets below `score`; `//...` selects all
targets. Configuration names come from your module's `.bazelrc` files.

- **Test only** — supply `test-targets`. Bazel builds the selected tests first.
- **Build only** — supply `build-targets` and omit `test-targets`.
- **Build, then test** — supply both to also check build targets outside your
  test selection. A failed build stops the job before testing.

Choose a stable `cache-key` for each job and matrix variant, for example
`build-${{ matrix.name }}`. Omitted keys share the fallback name `bazel`.

## Adjust the job

| Need                                       | Input and example                             |
| ------------------------------------------ | --------------------------------------------- |
| Multiple configurations or sanitizers      | `bazel-configs: x86_64-linux asan_ubsan_lsan` |
| Exclude tests                              | `test-targets: //... -//score/slow/...`       |
| Extra test or coverage options             | `test-args: --test_timeout=600`               |
| Extra build options                        | `build-args: --keep_going`                    |
| Command options for every Bazel invocation | `bazel-args: --verbose_failures`              |
| Nested Bazel workspace                     | `working-directory: examples/integration`     |
| Extra system packages                      | `apt-packages: qemu-user`                     |
| Different job timeout                      | `timeout-minutes: 60`                         |

Pass configuration names without `--config=`. Arguments are literal, with no
shell expansion. Quote values containing spaces, for example
`test-args: '--test_env="MESSAGE=hello world"'`. `bazel-args` accepts options after
the Bazel command; startup options such as `--bazelrc` are not supported.

Commands use `--lockfile_mode=error`. Update the module's dependency lockfile if
CI reports that dependency resolution would change it. Runner selection comes
from repository variables, falling back to Ubuntu 24.04. Use `runner-labels`
for a job-specific requirement and `disk-cleanup-level` to adjust disk cleanup.
See the [Reference](#reference) for all inputs, secrets and outputs.

## Collect coverage

Your module needs a coverage configuration and the target
`@score_coverage//:generate_coverage_html` from `coverage_tool`. Replace the
example's `with:` block with:

```yaml
with:
  test-targets: //score/...
  test-command: coverage
  bazel-configs: llvm_cov
  cache-key: coverage
  coverage-justifications: quality/coverage/coverage_justifications.yaml
  coverage-threshold: 66
```

The workflow measures coverage, generates the report and uploads it. The minimum
line coverage after applying justifications defaults to 100 percent. Set
`coverage-threshold: 0` for report-only use; build, test and report-generation
errors still fail the job. Omit `coverage-justifications` if you have no file.
With `setup-qnx-sdp: true`, the report applies QNX justifications. Otherwise it
applies Linux justifications.

## Access private dependencies

For private GitHub dependencies, pass the `github-token` secret, or supply
`github-app-client-id` with the `github-app-private-key` secret.

For authenticated HTTP downloads, store complete `.netrc` content in a secret,
for example `machine packages.example.com login ci password TOKEN`. Pass it
alongside the job's `with:` block:

```yaml
secrets:
  netrc: ${{ secrets.BAZEL_NETRC }}
```

One file can contain multiple hosts. The workflow sets `NETRC` for
[Bazel download rules that support Netrc](https://bazel.build/rules/lib/repo/utils#read_user_netrc).
The file is readable only by the runner user and is removed during job cleanup.
An existing `~/.netrc` is preserved. Netrc and GitHub credentials can be combined.

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
configuration and targets with your module's values, and pin `@qnx-fork-approval`
to the reviewed commit SHA.

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
    uses: eclipse-score/cicd-workflows/.github/workflows/bazel.yml@qnx-fork-approval
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

## Find and reuse reports

Find reports under **Artifacts** on the GitHub Actions run page. Test jobs
upload XML results and all `.log` files under `bazel-testlogs`. Coverage jobs
upload the coverage report. Files are kept for seven days by default; change
this with `retention-days`. Build-only jobs do not upload reports.

Available reports are uploaded after failures too. If coverage fails before an
archive is available, the workflow uploads test XML and logs instead. Missing
files produce a warning.

Set `artifact-name` when another job needs a fixed name, or let the workflow
generate a unique name. Use `needs.<job>.outputs.artifact-name` to download the
artifact from a dependent job. The output is empty if nothing was uploaded.
For matrix jobs, choose distinct names such as `bazel-testlogs-${{ matrix.name }}`
to retrieve each variant's report.

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
| Secret `qnx-sdp-github-token` | No direct equivalent: `bazel.yml` does not expose it as `GH_TOKEN` for SDP release downloads. |
| Build check `Build QNX target` | Build check `Bazel`; update required-check entries to the full reported caller/check name. |

If your module downloads SDP release assets using `GH_TOKEN`, keep its existing
QNX caller until the download authentication is adapted. Passing `github-token`
configures Git access; it does not provide `GH_TOKEN` to the download tool.

Commands now use `--lockfile_mode=error`: update and commit the module's lockfile
before adopting the workflow. Per-phase arguments remain distinct; flags in
`build-args` are not automatically applied to tests. Argument values support
quoting but no shell expansion. The old workflow automatically installed QEMU
when tests were selected; the new workflow requires `setup-qemu: true` for VM
setup. QNX fork PRs require `pull_request_target` and execute the immutable head
commit from the event. Merge groups execute their merge-group commit.

Update required-check names and any existing collector dependencies after
replacing the call. In a merge queue, require the actual QNX build to succeed.

## Reference

### Inputs

Pass inputs under the calling job's `with:` key. Empty means the input can be
omitted. Supply at least one of `build-targets` and `test-targets`.

| Input                     | Type    | Default                                  | Meaning                                                                                                                           |
| ------------------------- | ------- | ---------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| `build-targets`           | string  | Empty                                    | Targets to build before testing. Empty skips the build phase.                                                                     |
| `test-targets`            | string  | Empty                                    | Targets to test or measure coverage for, including exclusions. Empty skips this phase.                                            |
| `test-command`            | string  | `test`                                   | `test` or `coverage`. Coverage requires `test-targets` and the shared reporter target.                                            |
| `bazel-configs`           | string  | Empty                                    | Whitespace-separated config names, applied in order to build and test/coverage.                                                   |
| `bazel-args`              | string  | Empty                                    | Extra command options for every Bazel invocation, including the coverage reporter. Follow the default `--lockfile_mode=error`.    |
| `build-args`              | string  | Empty                                    | Extra command options for the build phase.                                                                                        |
| `test-args`               | string  | Empty                                    | Extra command options for the test or coverage phase.                                                                             |
| `working-directory`       | string  | `.`                                      | Bazel workspace relative to the checkout. Must be an existing directory inside it.                                                |
| `fork-pr-label`           | string  | Empty                                    | Label required for fork PR execution. Empty disables label filtering. Internal PRs, merge groups and other events always execute. |
| `cache-key`               | string  | Empty                                    | Stable build-output cache name. Empty uses `bazel`; choose a distinct key for each job and matrix variant.                        |
| `coverage-justifications` | string  | Empty                                    | Justification YAML path relative to the Bazel workspace, used for coverage reports.                                               |
| `coverage-threshold`      | number  | `100`                                    | Minimum line coverage percentage after applying justifications. `0` disables the coverage gate.                                   |
| `apt-packages`            | string  | Empty                                    | Additional APT packages to install, separated by whitespace.                                                                      |
| `disk-cleanup-level`      | number  | `2`                                      | Disk cleanup level from 0 to 4, passed to `more-disk-space`.                                                                      |
| `setup-qnx-sdp`           | boolean | `false`                                  | Configure QNX licensing and download access. Requires all three QNX secrets. Selects QNX justifications for coverage reports.     |
| `environment-name`        | string  | `workflow-approval`                      | Protected caller environment for fork target-event approval. Must have environment `reviewers` configured in Otterdog.            |
| `qnx-license-server`      | string  | Empty                                    | Optional license server, such as `6287@license-server`, used with QNX setup.                                                      |
| `qnx-credential-helper`   | string  | `.github/tools/qnx_credential_helper.py` | Download helper path relative to the checkout root, used with QNX setup. The file must exist. Empty disables helper setup.        |
| `setup-qemu`              | boolean | `false`                                  | Install `qemu-system` and `qemu-utils` and enable KVM access. Requires a runner with KVM, sudo and udev.                          |
| `runner-labels`           | string  | Empty                                    | JSON runner label or array, such as `["self-hosted", "linux", "x64"]`. Overrides repository runner variables.                     |
| `timeout-minutes`         | number  | `360`                                    | Job timeout, including setup and execution.                                                                                       |
| `artifact-name`           | string  | Empty                                    | Uploaded report artifact name. Empty generates a unique name for the invocation.                                                  |
| `retention-days`          | number  | `7`                                      | Number of days to keep uploaded reports.                                                                                          |
| `github-app-client-id`    | string  | Empty                                    | GitHub App client ID for private dependencies. Supply `github-app-private-key` as a secret.                                       |

Without `runner-labels`, selection uses repository variable
`runner_labels_ghub_standard_x64`, then `REPO_RUNNER_LABELS`, then
`ubuntu-24.04`. Both variables contain a JSON label or array.

Target lists and argument inputs support quoted values. `bazel-args`,
`build-args` and `test-args` accept command options, with no shell expansion.
Bazel startup options are unsupported. Per-phase options follow `bazel-args`,
so a phase can override a shared option such as `--lockfile_mode`.

### Secrets

Pass secrets under the calling job's `secrets:` key. All are optional unless
the corresponding setup is enabled.

| Secret                   | Required when                           | Meaning                                                                                              |
| ------------------------ | --------------------------------------- | ---------------------------------------------------------------------------------------------------- |
| `qnx-license`            | `setup-qnx-sdp: true`                   | Base64-encoded QNX client license file.                                                              |
| `qnx-user`               | `setup-qnx-sdp: true`                   | QNX account username for downloads.                                                                  |
| `qnx-password`           | `setup-qnx-sdp: true`                   | QNX account password for downloads.                                                                  |
| `netrc`                  | Using Netrc authentication              | Complete file content for authenticated HTTP downloads. Omission keeps existing authentication.      |
| `github-app-private-key` | Using `github-app-client-id`            | Private key for the GitHub App used to access private dependencies.                                  |
| `github-token`           | Using token-based GitHub authentication | GitHub token with access to the required private dependencies, as an alternative to App credentials. |

### Outputs

Read outputs from a dependent job through `needs.<job>.outputs.<name>`.

| Output          | Meaning                                                                                                                                             |
| --------------- | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| `artifact-name` | Name of the uploaded report artifact. Empty if no artifact was created, including label-based skips, build-only jobs and runs with no report files. |
