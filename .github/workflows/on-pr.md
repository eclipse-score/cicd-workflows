# PR Checks (`on-pr.yml`)

`on-pr.yml` is the **main entry point for pull-request checks** across all S-CORE
repositories. It auto-detects what a repository contains and runs the relevant
checks — PR commit messages, pre-commit, Python tests, Bazel format/copyright,
and the Bazel bzlmod lockfile checks — as a **single job**. Optional configured
Bazel checks run in additional matrix jobs when `workflow-config-yaml` is
provided. The same workflow also orchestrates coverage, analysis, SBOM and Docs
with shared platform setup.

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
tests:
  linux-native:
    bazel-configs: score-linux-x86_64
    bazel-build-targets: //...
coverage:
  linux:
    os: linux
    architecture: x86_64
    bazel-configs: coverage-linux
    bazel-targets: //my/library/...
docs: {}
```

This builds all native targets, runs all native tests, collects coverage with
the shared LCOV/HTML reporter, and builds Docs with the verified reports.
Only declare families you want to run. The named Bazel configs must exist in
the module and select its toolchain and instrumentation.

Use the Bazel config names and labels defined by your module. `bazel-configs` is
an explicit comma-separated list, applied in order. Target strings are split
into arguments without a shell and may include exclusions such as `-//pkg:test`.
A test entry runs its optional `bazel-build-targets` first, then its
`bazel-test-targets` (default `//...` in schema 2). A failed build stops that row. For compilation alone,
use named `builds` profiles with `bazel-configs`; `bazel-build-targets` defaults to
`//...` in schema 2. Compiler config names always remain required.

Each named profile selects one OS and architecture; schema 2 defaults to Linux x86_64. This POC supports Linux x86_64
builds/tests, Linux aarch64 builds and explicit aarch64 tests under QEMU on
x86_64 runners, plus QNX builds and VM tests with optional SDK credentials. Profile names must be unique across `tests`, `builds` and `coverage`. An optional `name`
identifies a check within a profile when needed. Row IDs are `<profile>-tests`
or `<profile>-builds`, followed by an explicit check name or batch name when
present. SCORE does not classify tests: Bazel configs and targets determine
what runs, including sanitizer settings.

The shared workflow owns checkout, Bazel setup/cache, diagnostic flags, and
artifacts containing command reports, Bazel build events and available test logs.
The module owns its Bazel platforms, toolchains, configs and target selection.
Success requires both successful commands and build/test evidence. Cached test
passes count; an empty or entirely skipped test selection fails.
`configured-results` reports the verified checkout and per-row command outcomes.
Each job in `configured-results` and `quality-results` includes `metadata` with
`profile`, `platform` (`os`, `architecture`), `check`, `working-directory`,
`batch`. Unbatched jobs use an empty batch. These execution facts are verified against preparation.
Consumers should use them instead of parsing row IDs, which remain opaque
identifiers. Presentation paths and publication policy belong to the consumer.

`docs-artifact` is `github-pages` after a successful configured Docs build and
empty when Docs were skipped. Trusted internal wrappers can use this output to
run `docs-publish.yml` as a dependent job in the same run, as described in
[the documentation guide](docs.md#trusted-internal-repositories). Publication
policy remains in the wrapper; SCORE build jobs need only read access.

Downstream jobs can consume the `configured-artifacts` JSON output, keyed by the
configured row ID. Each value has a `report` artifact name and a `testlogs`
artifact name (null for build-only rows). Test-log archives contain package paths
at their root, so existing documentation workflows can reconstruct Bazel labels:

```yaml
docs:
  needs: checks
  uses: eclipse-score/cicd-workflows/.github/workflows/docs.yml@main
  with:
    tests-report-artifact: ${{ fromJSON(needs.checks.outputs.configured-artifacts)['linux-native-tests'].testlogs }}
```

The archive names include an invocation-specific prefix. Use the output rather
than constructing names or relying on a fixed artifact shared by all rows.

Each selected Bazel workspace must contain `MODULE.bazel` and
`MODULE.bazel.lock`. In the compact schema 2 layout, `working-directory` selects a
checkout-relative workspace such as `examples/integration`; it defaults to `.`.
Paths must remain inside the checkout, including after symlink resolution.
Checkout stays at the repository root; cache setup, build/test and log collection
all use the selected workspace.

`execution` defaults to `native`. Set it to `qemu-user` for
Linux aarch64 tests on the shared x86_64 runner. This profile installs QEMU,
allows up to six hours for the job and applies
`--test_timeout=180,900,2700,10800`. Native rows have a two-hour job budget.
Your Bazel config must select the cross-toolchain and its `run_under` wrapper;
installing QEMU alone cannot configure a module's tests. Build-only ARM rows
need no execution override. Example with ARM tests and a separate integration workspace:

```yaml
tests:
  linux-arm:
    architecture: aarch64
    bazel-configs: score-linux-aarch64
    execution: qemu-user
  integration:
    bazel-configs: x86_64-linux
    working-directory: examples/integration
```

The optional `bazel-test-custom-cli-args` accepts only
tag filters and the [documented test controls](#test-profiles-and-batches). Arbitrary flags, shell
hooks and unrecognized fields fail explicitly. The supported schema 1 subset
adapts existing test configurations to the same execution plan.
Unsupported legacy options are errors rather than silently ignored settings.

The existing schema 2 `variants` layout remains supported for advanced callers,
including multiple checks per platform or per-variant coverage. It uses
`checks.tests`/`checks.builds` lists, lists of named OS/architecture values, and
`workflow-config` for execution/report options. Both layouts normalize to the
same execution plan. Do not mix `variants` with the compact top-level families.
The compact layout uses scalar `os`/`architecture` and direct execution options.
Coverage has its own named profiles and does not inherit a test profile's
platform, workspace or Bazel configs.

### Configuration defaults

Defaults are central and apply only to configured checks. An absent
`workflow-config-yaml` still runs no configured jobs. A missing check family is
disabled. Named `coverage` profiles enable coverage; `docs: {}` enables Docs. Explicit values override defaults; invalid or null values are errors.

| Key | Default |
| --- | --- |
| Profile `os`, `architecture` (schema 2) | `linux`, `x86_64` |
| Test `bazel-test-targets` (schema 2) | `//...` |
| Build-only `bazel-build-targets` (schema 2) | `//...` |
| Bazel report generator `generate-artifacts-archive` | `true` |
| Coverage `coverage-threshold`, retention | `0` (report-only), 10 days |
| Direct coverage report output | `coverage-report` |
| Docs `bazel-target`, retention | `//:docs`, 5 days |
| Docs `tests`, `coverage` | Unique suitable root-workspace row, if present |

Compiler configs for build/test entries stay required because they select the
module's toolchain. Tests build first only if `bazel-build-targets` is supplied.
ARM emulation stays opt-in via `architecture` and `execution`.
Coverage configs, target scopes, generator labels and module input paths have
no defaults. Scope checking stays opt-in via `scope-check`; all three fields
are required and its exclusions file must exist. Fixed
release artifact names, exclusions and nonzero coverage thresholds remain
module policy and need explicit configuration.

### Add coverage and documentation

Coverage profiles select their own `os`, `architecture`, `working-directory`
and `execution`, using the same platform rules as tests. Linux x86_64 is the
default. Linux aarch64 requires `execution: qemu-user`; QNX uses VM test setup.
The module's explicit `bazel-configs` select the actual toolchain and coverage
instrumentation. Selecting an OS alone cannot configure either.

For a module-provided Bazel report generator, declare its inputs explicitly:

```yaml
coverage:
  linux:
    os: linux
    architecture: x86_64
    bazel-configs: coverage-linux
    bazel-targets: //my/library/... -//my/library:excluded_test
    report-bazel-target: '@score_coverage//:generate_coverage_html'
    justifications-yaml: tools/coverage/justifications.yaml
    coverage-threshold: 80
```

Your module must define the instrumentation config and report generator. Target
exclusions remain ordinary Bazel target arguments. The generator must support
`--yaml`, an optional `--testlogs-subdir`, and `--archive-dir` when
`generate-artifacts-archive` is true. Otherwise it receives `coverage-report` as
its positional output directory. Coverage runs with `--build_tests_only` and
requires successful test summaries. The report generator requires successful
Bazel target evidence. A successful exit without HTML and LCOV fails the gate.

For an existing module-owned Bazel orchestrator, select its target and output
directory in a named profile:

```yaml
coverage:
  linux:
    os: linux
    architecture: x86_64
    bazel-target: //quality/coverage:run_coverage
    output-directory: coverage_artifacts
    coverage-threshold: 66
```

This form executes that Bazel target without imposing configs or test targets on
its internal pipeline. It requires successful Bazel target evidence and HTML/LCOV
output. Both forms pass `COVERAGE_THRESHOLD` to Bazel and the report generator;
the module must honor it. The default is **0 (report-only)**, so set the desired
threshold explicitly. Thresholds must be numbers from 0 to 100. Shell scripts and
arbitrary CLI hooks are not configuration fields.

All coverage profiles support `working-directory`, `artifact-name` and
`retention-days` (default 10, range 1–90). Leave `artifact-name` unset for a unique
invocation-specific name. Set a fixed name only when another consumer, such as a
release workflow, requires that interface; names must be unique in the plan.
`{repository}` in a fixed name expands to the selected repository name (without
the owner), preserving release interfaces in forks and external checkouts.
`configured-artifacts[row-id].coverage` exposes the resolved name.
Row IDs use the normalized profile name: `<profile>-coverage`.

An optional `scope-check` on a direct coverage profile checks production-scope
completeness. The module supplies every scope input explicitly:

```yaml
scope-check:
  scope-target: //quality/coverage:scope
  universe: //my/library/...
  exclusions: quality/coverage/exclusions.txt
```

Before coverage, the workflow queries non-testonly, non-QNX `cc_library` and
`rust_library` targets in the universe. Each must be reachable from the scope's
configured dependencies or explicitly excluded. Exclusions use one label per
line with optional comments. Excluded targets that become reachable fail as
stale exclusions. This is a built-in policy, with no consumer shell hook.

To build Docs after verification, add a top-level `docs` object:

```yaml
docs: {}
```

`tests` and `coverage` can select root-workspace row IDs explicitly. By default,
Docs selects the sole native root test row and the sole root coverage row. No
candidate means no report; multiple candidates require an explicit row ID and
fail preparation otherwise. Set a selector to `""` to disable that report. The workflow validates references before
execution and resolves artifact names automatically. Repository context is supplied
automatically through the environment read by the Docs CLI. Docs
always checks out the preparation commit and runs after every configured row
has passed verification. It uploads `github-pages`; deployment remains separate.
The existing `docs.yml` command-string interface remains supported for callers
that do not use this configuration.

This is an experimental POC. See the [maintainer notes](../../tools/README.md)
for its bootstrap references, consumer examples and remaining validation.

### Additional quality checks

Select these checks in the same `.github/ci.yml`; no module actions or execution
steps are required:

```yaml
tests:
  asan:
    bazel-configs: x86_64-linux,asan_ubsan_lsan
clippy: {}
codeql:
  bazel-configs: x86_64-linux
  reports: [cert-cpp-l1, cert-c-l1, cpp-code-scanning, code-complexity]
  fail-on-findings: true
docs:
  check: true
  link-check: true
```

Sanitizers are ordinary test profiles. Omitting `bazel-build-targets` keeps them
test-only. Docs verification runs `//:docs_check` and `//:docs_link_check` with
Graphviz installed; both must succeed before the documentation build starts.

Clippy reads the pinned version from `rust-toolchain.toml`, installs the minimal
Rust toolchain with Clippy, and runs Cargo with `--locked --all-features
--all-targets --workspace -- -D warnings`. It requires `Cargo.toml`, `Cargo.lock`
and evidence that Cargo checked workspace targets. No Rust version is duplicated
in CI configuration. Cargo JSON diagnostics are retained and rendered in logs.

CodeQL uses the module's Bazel analysis target, not a replacement GitHub ruleset.
Defaults are `//quality/static_analysis:codeql_lint`, production universe
`//score/...`, the four reports above, and failure on unsuppressed findings.
`bazel-target`, `target-universe`, `reports` and `fail-on-findings` can override
these defaults. Target queries must be nonempty; analysis and SARIF merge require
Bazel evidence. Suppression semantics match the module pipeline: any SARIF
suppression exempts that result from the failure gate.

CodeQL retains `codeql-sarif-results` and `codeql-compliance-reports` for 30 days,
including available diagnostics after failures. GitHub code-scanning upload is
best effort; the local findings gate remains required even on fork PRs. Callers
with CodeQL grant `contents: read`, `actions: read` and `security-events: write`.
Analysis jobs themselves use only `contents: read`; publication inherits the
caller grants. Callers without CodeQL keep their existing read-only permissions.

Scheduled/manual callers can use `configured-quality.yml` with `only-codeql:
true`. `fetch-only: true` fetches dependencies without claiming analysis success.
The optional string `fail-on-findings: "false"` overrides the configured failure
policy for a reporting-only nightly run. The source revision is frozen and all
quality reports are verified against that revision and the configuration hash.

### Test profiles and batches

The configured build/test contract above continues to apply. Profiles default to Linux x86_64 and the root workspace.
Only explicitly configured families run. Add batches when one test profile needs
several independently scheduled selections:

```yaml
schema-version: 2
tests:
  target:
    os: qnx
    bazel-configs: target-tests
    bazel-test-custom-cli-args: --test_timeout=600 --nocache_test_results
    batches:
      - batch-name: core
        bazel-test-targets: //core/...
      - batch-name: integration
        bazel-build-targets: //integration:image
        bazel-test-targets: //integration/...
```

Batches inherit configs and test options but select their own targets. A parent
build/test target selection is removed; a batch may explicitly request a build.
Names must be unique. The resulting row IDs are `target-tests-core` and
`target-tests-integration`.

`bazel-pre-test-targets` optionally declares Bazel run labels, executed with the
profile configs after its build and before tests. A failed precheck stops tests.
It replaces shell pre-test hooks with an explicit Bazel interface.

`bazel-test-custom-cli-args` accepts tag filters, test output mode, test-result
caching, build-tests-only, runs per test, flaky attempts, positive test timeout
classes and literal `--test_arg=...` values. Arguments are never evaluated by a
shell. Flags that replace execution infrastructure or evidence destinations are
rejected.

QNX builds install the SDK; QNX tests additionally install system VM prerequisites.
The module config still selects the QNX toolchain and VM test wrapper. Linux ARM64
tests retain the explicit `execution: qemu-user` profile. Emulation does not prove
behavior on target hardware.

### Shared coverage reporter, analysis and SBOM

For toolchains producing LCOV, omit `report-bazel-target` to use the central
reporter. No compiler-specific family name is required:

```yaml
schema-version: 2
coverage:
  target:
    os: qnx
    architecture: x86_64
    bazel-configs: target-coverage
    bazel-targets: //core/...
    coverage-threshold: 80
```

The central recipe runs Bazel coverage with a combined LCOV report, generates HTML
with genhtml, checks the line threshold and stages plain LCOV and Cobertura for
consumers. Available diagnostics are retained on threshold failure.

Named `sca` and `sbom` profiles can run alongside tests or on their own:

```yaml
schema-version: 2
sca:
  native:
    bazel-target: //analysis:run
    bazel-configs: native
    bazel-targets: //core/...
    reports: [cpp-code-scanning]
    safety-level: QM
sbom:
  native:
    bazel-target: //:sbom
    bazel-configs: native
    cyclonedx: bazel-bin/module.cdx.json
    spdx: bazel-bin/module.spdx.json
```

The SCA tool is a module-provided Bazel target implementing the CLI
`--config=NAME --platform=linux|qnx [--safety-level=QM|ASIL] [--report=NAME] TARGET...`.
It must produce SARIF under `_sca/`; additional analysis files are retained there.
This is an explicit tool protocol, not an adapter for any arbitrary analyzer.
Unsuppressed findings fail by default; `fail-on-findings: false` selects reporting
only. The existing `codeql` profile keeps its separate module tool protocol.

SBOM profiles build their declared target and validate/stage CycloneDX and/or SPDX
JSON from declared `bazel-bin/` paths. They do not update lockfiles or publish to
external services. `quality-results` and `quality-artifacts` expose verified
outcomes and artifact names by profile ID. Existing `configured-results` and
`configured-artifacts` continue to describe Bazel build/test/coverage rows.

Docs can select several test rows using `docs.tests: [ROW_ID, ROW_ID]`. The central
workflow merges their package-relative XML paths and rejects differing reports
at the same path. Select non-overlapping batches when assembling one Docs input.
The existing unique-row defaults and explicit empty selectors remain supported.

### Platform credentials and runners

Public modules need no credentials. Private dependencies use the existing
`github-app-client-id` input plus `github-app-private-key` secret, or a `token`
secret. All execution families, including analysis and Docs, share this setup.

For authenticated HTTPS dependency downloads, set `artifact-server-url` and pass
`artifact-server-user` and `artifact-server-token` secrets. The host is selected by
the caller; there is no organization-specific endpoint in the workflow.

QNX profiles require `qnx-license`, `qnx-user` and `qnx-password` secrets; an optional
`qnx-license-server` input is forwarded to SDK setup. `qnx-runner-labels` optionally
selects JSON runner labels. Otherwise the central large-runner variable is used
when configured, followed by the ordinary runner selection. Missing credentials
fail setup. A fork without those credentials cannot execute QNX profiles.
Privileged `pull_request_target` calls are rejected.

### Migration boundaries

`on-pr.yml` owns configured execution, shared setup, verification and report
handoff across tests, coverage, analysis and documentation. Organization-specific
report assembly and publication can consume its outputs separately.

Existing module configurations continue to work. Older source configurations need
an explicit migration: runner selection and credentials belong on the reusable
call; central profiles own installed prerequisites; shell hooks become Bazel
prechecks; analysis and SBOM use the named profiles above. Unknown fields fail
preparation, including obsolete publication and package-installation options.
The supported schema 1 subset is not a drop-in replacement for the entire source
schema.

Local validation covers configuration compatibility, command/evidence contracts,
report failures and real genhtml execution. QNX SDK/VM execution, analysis tool
compatibility, GitHub permissions and artifact transfers still require remote
validation. Helper/workflow references must be pinned together before release.

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
| Gitlint         | Root `.gitlint` exists and PR context applies | Pinned Gitlint with repository configuration |
| pre-commit      | `.pre-commit-config.yaml` exists         | `uvx pre-commit run --all-files`          |
| Python tests    | `pyproject.toml` **and** `uv.lock` exist | `uv run --frozen python -m pytest`        |
| Bazel format    | `//:format.check` target exists          | `bazel test //:format.check`              |
| Bazel copyright | `//:copyright.check` target exists       | `bazel run //:copyright.check`            |
| Bzlmod tidy     | `MODULE.bazel.lock` exists               | `bazel mod tidy` + `git diff --exit-code` |
| Bzlmod lockfile | `MODULE.bazel.lock` exists               | `bazel mod deps --lockfile_mode=error`    |
| Lockfile required | `require-bzlmod-lock: true`, `MODULE.bazel` exists, lockfile missing | fails with guidance |

Capability detection looks for:

- **Gitlint** — root `.gitlint` file and `pull_request` for the caller repository, without an explicit `ref`

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

## Commit-message checks

On `pull_request`, Gitlint runs only when the checked-out repository contains a
`.gitlint` file at its root. It checks the event's base-to-head commit range using
that file. Repositories own their commit-message rules; no shared policy is
fetched or supplied. Repositories without `.gitlint` skip the check.

No entry in a CI configuration file is needed. The Gitlint version is pinned
in the workflow; `skip-gitlint: true` disables the check even when `.gitlint` exists.

The check supports fork PRs and excludes checkout's synthetic merge commit.
Pushes, merge-queue events and other events skip it. An explicit `ref` or a
different `repository` also skips it, because the calling PR's commit range does
not describe that selected checkout. `skip-gitlint: true` disables the check when
it is already provided elsewhere. Only `contents: read` is needed.

## Custom Bazel targets

Callers can override the check targets with optional string inputs:

| Input              | Default               | Command                  |
| ------------------ | --------------------- | ------------------------ |
| `format-target`    | Automatic discovery   | `bazel test <target>`    |
| `copyright-target` | Automatic discovery   | `bazel run <target>`     |

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
its target cannot be queried. Explicit overrides never fall back to another
label. When an input is empty (the default), discovery tries these labels in order:

- Formatting: `//:format.check`, then `//tools:format.check`.
- Copyright: `//:copyright.check`, `//tools:copyright.check`, `//:copyright-check`, then `//tools:copyright-check`.

Existing root entry points keep priority. Standard `//tools` layouts need no
caller override. The selected label is printed in discovery logs and used by
the execution step.

## Skipping checks

Sometimes a capability is present but you do not want `on-pr` to run that check
(for example, you already run pre-commit a different way). Set the matching input
to `true`:

| Input               | Skips                                    |
| ------------------- | ---------------------------------------- |
| `skip-gitlint`     | PR commit messages                       |
| `skip-pre-commit`   | pre-commit                               |
| `skip-python-tests` | Python / pytest                          |
| `skip-format`       | configured Bazel formatting check       |
| `skip-copyright`    | configured Bazel copyright check        |
| `skip-bzlmod-lock`  | both the bzlmod tidy and lockfile checks |

All skip inputs default to `false`. Gitlint runs automatically for applicable PRs when `.gitlint` exists.
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

Standard checks need only `contents: read`. Opted-in CodeQL publication also
uses `actions: read` and `security-events: write`; analysis jobs stay read-only.

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
and a `checks` map with `gitlint`, `pre-commit`, `python-tests`, `format`, `copyright`,
`require-lock`, `module-tidy` and `lockfile` step outcomes. A skipped check is
reported as `skipped`. Failed checks still fail the workflow.


A Bazel coverage report generator can optionally set `report-bazel-configs` to a comma-separated
list of build configs for its HTML/report target. It defaults to no extra configs:
Bazel rc configs defined only under `coverage:` cannot be reused by `bazel run`.
Use this override when the module report target needs a configured build toolchain.


### Documentation after test failures

Configured documentation builds independently of quality-check outcomes. It waits
for the configured Bazel matrix to finish collecting reports, including failed
test results. Missing selected test or coverage artifacts (for example after a
compiler/setup failure) are listed in the Actions summary and do not block Docs.
Present but malformed or conflicting reports still fail the Docs job. The test,
coverage and aggregate verification jobs retain their failing statuses.

Standalone `docs.yml` callers retain strict report requirements unless they opt
into `allow-missing-reports: true`. To publish documentation despite unrelated
failures, a `workflow_run` caller can opt into `allow-failed-workflow: true` in
`docs-publish.yml` and invoke it for both success and failure conclusions. That
mode checks for the Docs artifact in the triggering run and skips publication
when no artifact was uploaded. Cancellation does not trigger publication.

The current matrix is a single Actions dependency: selecting fewer reports does
not yet let Docs start before the other matrix rows finish.
