# Build, test and collect coverage with Bazel

Use `bazel.yml` to run one Bazel job on a Linux runner. It handles checkout,
setup, caching and report uploads. You choose the targets, Bazel configurations,
triggers, matrices and job dependencies. Start with [Run a job](#run-a-job), or
look up a setting in the [Reference](#reference).

## Run a job

Add a job to your module's workflow. The selected targets and configurations
must already work in your module.

```yaml
jobs:
  tests:
    permissions:
      contents: read
    uses: eclipse-score/cicd-workflows/.github/workflows/bazel.yml@bazel-workflow
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

| Need | Input and example |
| --- | --- |
| Multiple configurations or sanitizers | `bazel-configs: x86_64-linux asan_ubsan_lsan` |
| Exclude tests | `test-targets: //... -//score/slow/...` |
| Extra test or coverage options | `test-args: --test_timeout=600` |
| Extra build options | `build-args: --keep_going` |
| Command options for every Bazel invocation | `bazel-args: --verbose_failures` |
| Nested Bazel workspace | `working-directory: examples/integration` |
| Extra system packages | `apt-packages: qemu-user` |
| Different job timeout | `timeout-minutes: 60` |

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

## Reference

### Inputs

Pass inputs under the calling job's `with:` key. Empty means the input can be
omitted. Supply at least one of `build-targets` and `test-targets`.

| Input | Type | Default | Meaning |
| --- | --- | --- | --- |
| `build-targets` | string | Empty | Targets to build before testing. Empty skips the build phase. |
| `test-targets` | string | Empty | Targets to test or measure coverage for, including exclusions. Empty skips this phase. |
| `test-command` | string | `test` | `test` or `coverage`. Coverage requires `test-targets` and the shared reporter target. |
| `bazel-configs` | string | Empty | Whitespace-separated config names, applied in order to build and test/coverage. |
| `bazel-args` | string | Empty | Extra command options for every Bazel invocation, including the coverage reporter. Follow the default `--lockfile_mode=error`. |
| `build-args` | string | Empty | Extra command options for the build phase. |
| `test-args` | string | Empty | Extra command options for the test or coverage phase. |
| `working-directory` | string | `.` | Bazel workspace relative to the checkout. Must be an existing directory inside it. |
| `cache-key` | string | Empty | Stable build-output cache name. Empty uses `bazel`; choose a distinct key for each job and matrix variant. |
| `coverage-justifications` | string | Empty | Justification YAML path relative to the Bazel workspace, used for coverage reports. |
| `coverage-threshold` | number | `100` | Minimum line coverage percentage after applying justifications. `0` disables the coverage gate. |
| `apt-packages` | string | Empty | Additional APT packages to install, separated by whitespace. |
| `disk-cleanup-level` | number | `2` | Disk cleanup level from 0 to 4, passed to `more-disk-space`. |
| `setup-qnx-sdp` | boolean | `false` | Configure QNX licensing and download access. Requires all three QNX secrets. Selects QNX justifications for coverage reports. |
| `qnx-license-server` | string | Empty | Optional license server, such as `6287@license-server`, used with QNX setup. |
| `qnx-credential-helper` | string | `.github/tools/qnx_credential_helper.py` | Download helper path relative to the checkout root, used with QNX setup. The file must exist. Empty disables helper setup. |
| `setup-qemu` | boolean | `false` | Install `qemu-system` and `qemu-utils` and enable KVM access. Requires a runner with KVM, sudo and udev. |
| `runner-labels` | string | Empty | JSON runner label or array, such as `["self-hosted", "linux", "x64"]`. Overrides repository runner variables. |
| `timeout-minutes` | number | `360` | Job timeout, including setup and execution. |
| `artifact-name` | string | Empty | Uploaded report artifact name. Empty generates a unique name for the invocation. |
| `retention-days` | number | `7` | Number of days to keep uploaded reports. |
| `github-app-client-id` | string | Empty | GitHub App client ID for private dependencies. Supply `github-app-private-key` as a secret. |

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

| Secret | Required when | Meaning |
| --- | --- | --- |
| `qnx-license` | `setup-qnx-sdp: true` | Base64-encoded QNX client license file. |
| `qnx-user` | `setup-qnx-sdp: true` | QNX account username for downloads. |
| `qnx-password` | `setup-qnx-sdp: true` | QNX account password for downloads. |
| `netrc` | Using Netrc authentication | Complete file content for authenticated HTTP downloads. Omission keeps existing authentication. |
| `github-app-private-key` | Using `github-app-client-id` | Private key for the GitHub App used to access private dependencies. |
| `github-token` | Using token-based GitHub authentication | GitHub token with access to the required private dependencies, as an alternative to App credentials. |

### Outputs

Read outputs from a dependent job through `needs.<job>.outputs.<name>`.

| Output | Meaning |
| --- | --- |
| `artifact-name` | Name of the uploaded report artifact. Empty if no artifact was created, including build-only jobs and runs with no report files. |
