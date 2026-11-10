# Build, test and collect coverage with Bazel

Use `bazel.yml` to run one Linux Bazel job. It handles checkout, setup, caching
and report uploads. Your workflow controls triggers, matrices and dependencies.

## Build and test

Add a job to your module's workflow:

```yaml
jobs:
  build:
    permissions:
      contents: read
    uses: eclipse-score/cicd-workflows/.github/workflows/bazel.yml@bazel-workflow
    with:
      build-targets: //examples/... //score/...
      test-targets: //...
      bazel-configs: x86_64-linux
      cache-key: linux-x86_64
```

The examples use the `bazel-workflow` review branch. Pin the reviewed commit
SHA when adopting the workflow.

Choose targets and configurations that exist in your module. `//...` selects
all targets; `//score/...` selects those below `score`. Configuration names come
from your module's `.bazelrc` files and are passed without `--config=`.

- **Build only:** supply `build-targets` and omit `test-targets`.
- **Test only:** supply `test-targets` and omit `build-targets`. Bazel builds the
  selected tests before running them.
- **Build, then test:** supply both, as above. This also checks build targets
  outside the test selection. A failed build stops the job before testing.

Keep `cache-key` stable across runs and distinct for each job or matrix variant,
for example `build-${{ matrix.name }}`. Without an explicit key, calls share the
fallback name `bazel`.

## Adapt the job to your module

Set only the inputs you need:

| Need | Input and example |
| --- | --- |
| Multiple configurations or sanitizers | `bazel-configs: x86_64-linux asan_ubsan_lsan` |
| Exclude tests | `test-targets: //... -//score/slow/...` |
| Extra test or coverage flags | `test-args: --test_timeout=600` |
| Extra build flags | `build-args: --keep_going` |
| Flags for every Bazel command | `bazel-args: --verbose_failures` |
| Nested Bazel workspace | `working-directory: examples/integration` |
| Extra system packages | `packages: qemu-user` |
| Different job timeout | `timeout-minutes: 60` |

Flags are passed literally, without shell expansion. Quote an argument that
contains spaces, for example `test-args: '--test_env="MESSAGE=hello world"'`.
Commands use `--lockfile_mode=error` by default: update the module's dependency
lockfile if CI reports that dependency resolution would change it.

Runner selection normally comes from repository variables, falling back to
Ubuntu 24.04. Use `runner-labels` only for a job-specific requirement.
For private dependencies, pass either the `token` secret or
`github-app-client-id` with the `github-app-private-key` secret.
All inputs, defaults and secret descriptions are listed in [bazel.yml](bazel.yml).

For authenticated HTTP downloads, store complete `.netrc` content in a repository
or organization secret (for example `machine packages.example.com login ci password TOKEN`).
Pass it alongside the job's `with:` block:

```yaml
secrets:
  netrc: ${{ secrets.BAZEL_NETRC }}
```

Multiple hosts may share one file. The workflow sets `NETRC` before cache setup
for [Bazel download rules that support Netrc](https://bazel.build/rules/lib/repo/utils#read_user_netrc).
The temporary file is private to the runner user and removed during job cleanup;
an existing `~/.netrc` is untouched. Omit the secret to keep existing authentication.
GitHub dependency credentials above can be supplied alongside it.

## Coverage

For a module with a coverage configuration and the `coverage_tool` dependency,
use this `with:` block in place of the build/test inputs above:

```yaml
with:
  test-targets: //score/...
  test-command: coverage
  bazel-configs: llvm_cov
  cache-key: coverage
  coverage-justifications: quality/coverage/coverage_justifications.yaml
  coverage-threshold: 66
```

The module must provide the target `@score_coverage//:generate_coverage_html`.
The workflow collects coverage, generates the report and uploads it automatically.
The threshold defaults to 100 percent. Set it to 0 for report-only use; build,
test and report-generation errors still fail the job. Omit
`coverage-justifications` when you have no justification file.

## Find and reuse reports

Test jobs upload XML results and `test.log` files; coverage jobs upload the
coverage report. Find them under **Artifacts** on the GitHub Actions run page.
Results are kept for seven days by default; change this with `retention-days`.
Build-only jobs do not upload reports.

Available files are uploaded after command failures too. If coverage fails
before a report is archived, test XML and logs are uploaded instead. Missing
report files produce a warning.

Set `artifact-name` when another job expects a fixed name. Otherwise a unique
name is generated. A dependent job can download that artifact using
`needs.<job>.outputs.artifact-name`; the output is empty if nothing was uploaded.
For matrix jobs, use distinct explicit artifact names to select each variant's
report, for example `bazel-testlogs-${{ matrix.name }}`.
