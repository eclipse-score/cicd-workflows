# Bazel job (`bazel.yml`)

One call executes one Bazel job in one checkout and cache context. The caller
owns triggers, matrix expansion and job dependencies.
The workflow supports Linux builds and test or coverage execution. Coverage
reports are generated automatically by `coverage_tool`.
QNX setup and analysis-specific processing remain in their existing workflows.

## Build and test

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
      artifact-name: bazel-testlogs-x86_64-linux
```

`bazel-workflow` is a local review reference, not a published implementation.
Pin the reviewed immutable revision when adopting the workflow.
The equivalent commands are:

```sh
bazel build --lockfile_mode=error --config=x86_64-linux -- //examples/... //score/...
bazel test --lockfile_mode=error --config=x86_64-linux -- //...
```

Target inputs default to empty. Supply `build-targets`, `test-targets`, or both;
an empty job fails. Only enabled commands execute.
A failed command stops execution before the next command; available results
remain eligible for upload after command failures.

## Lifecycle matrix example

This replaces execution steps in the existing module job. Keep its original
workflow events, common checks, other jobs and dependency gates in the module.
Changing reusable job nesting may change check names used by branch protection;
review those names when adopting this example.

```yaml
jobs:
  build:
    name: Build and test ${{ matrix.name }}
    permissions:
      contents: read
    strategy:
      fail-fast: false
      matrix:
        include:
          - name: x86_64-linux
            configs: x86_64-linux
            build: //examples/... //score/...
            test: //...
          - name: arm64-linux
            configs: arm64-linux
            build: //examples/... //score/...
            test: ""
          - name: asan_ubsan_lsan
            configs: x86_64-linux asan_ubsan_lsan
            build: ""
            test: //...
          - name: tsan
            configs: x86_64-linux tsan
            build: ""
            test: //...
    uses: eclipse-score/cicd-workflows/.github/workflows/bazel.yml@bazel-workflow
    with:
      build-targets: ${{ matrix.build }}
      test-targets: ${{ matrix.test }}
      bazel-configs: ${{ matrix.configs }}
      cache-key: build-${{ matrix.name }}
      artifact-name: bazel-testlogs-${{ matrix.name }}
```

## Coverage

```yaml
jobs:
  coverage:
    permissions:
      contents: read
    uses: eclipse-score/cicd-workflows/.github/workflows/bazel.yml@bazel-workflow
    with:
      test-targets: //score/...
      test-command: coverage
      bazel-configs: llvm_cov
      test-args: --build_tests_only
      coverage-justifications: quality/coverage/coverage_justifications.yaml
      coverage-threshold: 66
      artifact-name: coverage-html
```

Bazel collects coverage for the module's selected targets. The reporter from
`coverage_tool` generates the HTML report, applies justifications and enforces
the threshold. The workflow runs these two commands directly, then uploads the
report directory. No module-specific shell wrapper target is needed.

`test-command` accepts `test` (default) or `coverage`. `test-args` configures
that phase, while `bazel-args` applies to every Bazel command, including the
reporter. Coverage requires test-targets and automatically calls
`@score_coverage//:generate_coverage_html`, then uploads its report from a
workflow-owned directory below the runner temporary directory. Module callers
need not specify the reporter executable, its options or the upload path.

`coverage-justifications` is an optional workspace-relative YAML path. Without
it, coverage_tool gates on raw coverage. `coverage-threshold` defaults to 100,
matching the tool's default; modules can set their own percentage, including 0
for report-only use. The workflow passes these values to the tool's existing
interfaces; coverage interpretation, justification handling and threshold
validation remain in coverage_tool. A failed command stops subsequent commands,
while available result files remain eligible for upload.

## Execution and results

- `bazel-configs` accepts space-separated config names, applied in order to build
  and test or coverage. Sanitizers use the same input; there is no separate
  sanitizer switch. Instrumentation configs are not applied to the coverage
  reporter.
- Every command starts with `--lockfile_mode=error`, so CI fails when dependency
  resolution would change the lockfile. `bazel-args` adds flags to every command
  after this default; an explicit lockfile-mode argument can override it. `build-args` adds build flags;
  `test-args` adds test or coverage flags. No diagnostic flags, timeouts or target scopes are added
  implicitly. Quote individual arguments with spaces; values are split into
  argument lists without executing shell substitutions or expanding variables.
- Target exclusions such as `-//pkg:slow_test` belong in `test-targets`,
  including when `test-command: coverage` is selected.
  Targets follow `--` so exclusions are not interpreted as Bazel options.
- `working-directory` selects an existing Bazel workspace inside the checkout.
  Cache setup and commands use it. For tests, every `result-paths` pattern is relative
  to that workspace, including `!` exclusions. Defaults collect XML and test.log.
  Coverage uploads the generated coverage report instead. If no report files
  were archived, it falls back to the test result paths, preserving XML and logs
  for diagnosis without changing the command failure status.
- `cache-key` defaults to the reusable job ID. Matrix callers must supply stable
  keys that distinguish variants. The cache action retains its own default
  restore/save policy and selects the Bazel version from the module workspace.
- `packages` adds APT packages. For example, Baselibs' AArch64 tests can request
  `qemu-user`, with their existing config, exclusions and timeouts in the caller.
- `disk-space-level` forwards the cleanup action's level (0–4), defaulting to 2.
- `runner-labels` accepts JSON labels; repository runner variables provide the
  fallback, followed by Ubuntu 24.04. `timeout-minutes` defaults to 360.
- Optional dependency credentials use `github-app-client-id` with the
  `github-app-private-key` secret, or the `token` secret. Privileged
  `pull_request_target` execution is rejected before checkout and credentials.
- Test and coverage jobs upload results with seven-day retention,
  including after failures.
  `artifact-name` sets the archive name for existing consumers; if omitted, a
  unique name avoids collisions between concurrent calls and matrix rows.
  Build-only jobs do not upload results. Missing reports produce a warning.
- The workflow output `artifact-name` is the actual uploaded name, or empty if
  no artifact was created. Use it in a dependent Docs job via
  `needs.<job>.outputs.artifact-name`. A matrix job has no combined per-row
  output; consumers selecting matrix reports should retain explicit unique
  artifact names. Logs are diagnostic; XML files contain structured test results.

Local validation does not establish successful builds or artifact handoffs on
GitHub. This draft does not alter existing `tests.yml` or its callers.
