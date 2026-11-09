# Bazel job (`bazel.yml`)

One call executes one Bazel job in one checkout and cache context. The caller
owns triggers, matrix expansion and job dependencies.
This initial draft supports Linux build-only, test-only and build followed by
test. Coverage, QNX setup and analysis-specific reports are later additions;
the existing reusable workflows continue to serve those callers.

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
      bazel-args: --lockfile_mode=error --config=x86_64-linux
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

Both target inputs default to empty. Supply at least one; an empty job fails.
Only enabled commands execute. A failed build stops execution before tests;
test reports remain eligible for upload after command failures.

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
            args: --config=x86_64-linux
            build: //examples/... //score/...
            test: //...
          - name: arm64-linux
            args: --config=arm64-linux
            build: //examples/... //score/...
            test: ""
          - name: asan_ubsan_lsan
            args: --config=x86_64-linux --config=asan_ubsan_lsan
            build: ""
            test: //...
          - name: tsan
            args: --config=x86_64-linux --config=tsan
            build: ""
            test: //...
    uses: eclipse-score/cicd-workflows/.github/workflows/bazel.yml@bazel-workflow
    with:
      build-targets: ${{ matrix.build }}
      test-targets: ${{ matrix.test }}
      bazel-args: --lockfile_mode=error ${{ matrix.args }}
      cache-key: build-${{ matrix.name }}
      artifact-name: bazel-testlogs-${{ matrix.name }}
```

## Execution and results

- `bazel-args` applies to both commands. `build-args` and `test-args` add flags
  for one command. No diagnostic flags, timeouts or target scopes are added
  implicitly. Quote individual arguments with spaces; values are split into
  argument lists without executing shell substitutions or expanding variables.
- Target exclusions such as `-//pkg:slow_test` belong in `test-targets`.
  Targets follow `--` so exclusions are not interpreted as Bazel options.
- `working-directory` selects an existing Bazel workspace inside the checkout.
  Both cache setup and commands use it. Every `result-paths` pattern is relative
  to that workspace, including `!` exclusions. Defaults collect XML and test.log.
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
- Test jobs upload reports with seven-day retention, including after failures.
  `artifact-name` sets the archive name for existing consumers; if omitted, a
  unique name avoids collisions between concurrent calls and matrix rows.
  Build-only jobs do not upload test reports. Missing reports produce a warning.
- The workflow output `artifact-name` is the actual uploaded name, or empty if
  no artifact was created. Use it in a dependent Docs job via
  `needs.<job>.outputs.artifact-name`. A matrix job has no combined per-row
  output; consumers selecting matrix reports should retain explicit unique
  artifact names. Logs are diagnostic; XML files contain structured test results.

Local validation does not establish successful builds or artifact handoffs on
GitHub. This draft does not alter existing `tests.yml` or its callers.
