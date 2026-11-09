# Configured Bazel POC

The POC extends `on-pr.yml` with explicit module configuration. Review the public
[configuration contract](../.github/workflows/on-pr.md#configured-builds-and-tests-poc)
for caller behavior. This directory contains the implementation and maintainer notes;
consumer-test maintenance remains in [tests/ci](../tests/ci/README.md).

The central workflow plans a matrix from one observed checkout, runs each row at
that exact commit and verifies all returned reports against the plan. Build and
test commands have separate observations. A compiler failure prevents the row's
tests; missing commands, missing reports and zero successful Bazel build/test
events fail verification. Test passes restored from cache remain valid evidence.

The composite action uses its own repository's locked Python project, located
relative to `github.action_path`. It never loads implementation code from the
consumer checkout. Do not use `github.workflow_ref` to discover the reusable
workflow implementation: in a called workflow that context identifies the caller.
The matrix keeps unique artifacts per invocation and per row; matrix workflow
outputs cannot represent all rows reliably.

## Local review scope

The workflow and consumer worktrees use `central-workflow`. The cache-action
change was merged independently in
[cicd-actions#74](https://github.com/eclipse-score/cicd-actions/pull/74):

| Repository | Local worktree | POC graph |
| --- | --- | --- |
| cicd-workflows | `score_cicd-workflows-ci-build` | Planner, executor, verifier, helper action and `on-pr.yml` |
| cicd-actions | `score_cicd-actions-central-workflow` | Cache action workspace selection and nested lockfile/version handling |
| baselibs | `score_baselibs-ci-poc` | Full native and QEMU `//...` builds/tests, plus nested integration |
| lifecycle | `score_lifecycle-ci-poc` | Full native build/test selection and ARM64 compilation |

Both consumer repositories select `.github/ci.yml` from their existing CI entry
points. The temporary manual POC workflows are removed. The migration replaces
complete rows rather than narrowing the old CI to the initial smoke-test targets.

| Consumer | Removed local execution | Retained module execution |
| --- | --- | --- |
| baselibs | Standalone common-check workflow and complete Linux execution matrix | QNX and lint |
| lifecycle | Native/ARM64 rows, sanitizers, Clippy, CodeQL, Gitlint and Docs checks | QNX; thin nightly CodeQL caller |

The central documentation job resolves reports from `configured-artifacts`.
Report/BEP artifacts are separate from package-rooted test-log archives, because
the docs source linker derives Bazel labels from those relative XML paths.
`cicd-workflows` itself has no module compiler matrix to replace; its existing
common/consumer tests remain. Other users still rely on `tests.yml`, so that
public reusable workflow is retained.

The workflow and action references currently use
`eclipse-score/cicd-workflows@central-workflow`. They deliberately select the
experimental upstream branch for both workflow and helper action. Consumer PRs
exercise the existing pull_request/merge_group entry points; main/tag pushes and
release callers keep their existing event coverage. Do not merge this branch
wiring as a production release.
The cache action is pinned to merged commit
`ee1eb410c572e2a97ae27488f8401887ec331de3` from cicd-actions#74. Before release,
also replace the central helper action/reusable workflow branch references with
reviewed immutable revisions.

## Local execution evidence

The initial focused consumer configurations were executed from the local worktrees on
2026-11-04 using the central executor and Bazel 8.8.2. All four rows and six
commands passed aggregate verification against their checkout and config hash.
The selected native tests passed: two Bazel test targets in baselibs and one in
lifecycle. Both ARM64 build-only rows passed too.

Base revisions were baselibs `19cab2bef2ff260ad5837dabd4f5cc75b22961bd` and
lifecycle `ff939fe2fc6bc118b434be3d5f7cc332597d7e14`, with the uncommitted POC
configurations. Local runs used isolated Bazel output roots and bounded compiler
parallelism. These results cover the planner/executor/verifier and selected
module graphs. On 2026-11-05 the executor also ran two ARM result-library test
targets under a real QEMU user-mode emulator and the complete nested integration
workspace (four test targets); both rows passed evidence verification. The local
QEMU package was extracted into an isolated tools directory with its path passed
to Bazel's test environment; CI installs it into the standard runner path.
Full root native/ARM suites have not run locally. GitHub cache setup and artifact
transfer have not run yet.

## Limits to assess before migration

- Cache setup uses the merged implementation from cicd-actions#74, pinned by
  commit. It resolves the selected workspace without overriding GITHUB_WORKSPACE,
  and saves that same directory for its post step.
- The executor owns its build-event files. Cache setup disables only its test-hit
  reporter, which otherwise supplies a competing build-event destination.
- Execution uses Linux x86_64 runners. ARM64 build rows cross-compile;
  ARM64 test rows require the explicit QEMU user-mode profile. Emulated passes
  do not establish native ARM hardware coverage. QNX profiles use the shared SDK/VM environment described in
  [PR checks](../.github/workflows/on-pr.md).
- Module configs may themselves use module-specific Bazel behavior. The POC
  deliberately offers no arbitrary shell hooks or package-installation options.
- Schema 1 compatibility covers the implemented tests and LLVM coverage subset.
  Schema 2 adds `checks.builds`, module-orchestrated `checks.coverage` and explicit entry names. Unknown fields and duplicate keys fail.
- Workflow permissions, remote action/cache integration and artifact handoff need
  GitHub validation after review. Local executor tests cannot establish these.
- Review branch-protection check names before adoption: the removed local native
  matrix rows and baselibs common-check workflow no longer publish their old names.

## Reproduce locally

Use the central locked Python project for all operations; consumers do not need
Python project files. Bazel/Bazelisk must already be on PATH. In CI it is supplied
by `eclipse-score/cicd-actions/setup-bazel-cache`.

```sh
uv run --locked python -m pytest -v
uv run --locked ruff check tests/ci tools

uv run --locked python tools/configured_checks.py plan \
  --workspace ../score_baselibs-ci-poc --repository eclipse-score/baselibs \
  --config .github/ci.yml --reports /tmp/poc-baselibs

uv run --locked python tools/configured_checks.py run \
  --workspace ../score_baselibs-ci-poc --config .github/ci.yml \
  --job linux-native-tests --reports /tmp/poc-baselibs

uv run --locked python tools/configured_checks.py run \
  --workspace ../score_lifecycle-ci-poc --config .github/ci.yml \
  --job linux-cross-builds-compiler --reports /tmp/poc-lifecycle
```

The baselibs config has native, QEMU and nested-integration rows; lifecycle has
native and cross-build rows.
Use a distinct reports directory per consumer. Plan output
lists exact argv before any build starts. For local verification, import `plan`
and `verify` from `configured_checks`, passing the planned rows, reports directory
and the checkout's observed `git rev-parse HEAD`. The CLI verification mode reads
`EXPECTED` (the JSON plan) and `SOURCE_COMMIT`, matching the workflow's handoff.

## Runtime and workspace profiles

`working-directory` is a workspace selection, not a shell hook. Plan validation,
the cache action, executor cwd and test-log collection must all select the same
directory. Each report records that selection and aggregation checks it. Nested
modules require their own lockfile; identical labels in two workspaces must not
produce interchangeable evidence or caches.

The `qemu-user` execution profile supports Linux aarch64 tests on an x86_64 host.
It installs only the standard emulator package through the shared apt action.
The module supplies its Bazel toolchain and run_under wrapper. The workflow owns
the longer test timeout classes and six-hour runner budget. No package lists,
setup commands or arbitrary shell hooks are accepted from consumer YAML.

## Coverage and Docs integration

`configured_coverage.py` owns the coverage contracts and Docs selection.
Named `coverage` profiles select platform, workspace and execution independently
of test profiles. A profile either supplies explicit instrumentation configs and
targets, optionally with a Bazel report generator, or delegates the complete
pipeline to a module Bazel target with an explicit output directory. Direct
coverage without a report target uses central LCOV/genhtml processing.
`report-bazel-target` requires `justifications-yaml`; its CLI uses `--yaml` and
`--archive-dir`. No module-specific configs, labels or file paths are defaults.
The report-only threshold, retention and normalized reporting conventions remain
central. Internal setup profiles retain tool-specific names to select packages;
those names are not schema 2 check families.

The optional built-in scope policy queries the explicitly configured production
universe, configured scope dependencies and exclusions file. It rejects missing
coverage and stale exclusions. Module-owned targets can retain combined C++/Rust
orchestration. Explicit artifact names preserve module release interfaces.
Coverage and Docs contribute to the central success result.

Coverage collection stages HTML, plain LCOV at both supported root filenames,
and Cobertura (preserving the generator's `coverage.xml` or converting via the
centrally locked dependency). ZIP input uses only `lcov_report/lcov.dat` and is
never extracted as a directory tree. Missing/invalid reports fail execution.
Threshold failures retain available reports without turning the row green.

`docs.yml` provides optional `repository`, `ref` and `bazel-label` inputs.
The deprecated `github-context` boolean remains accepted for compatibility; the
Docs subprocess always receives repository context through its environment.
The central caller uses argv execution and the preparation SHA, retaining
the legacy command-string path for existing callers. It reuses existing artifact
download/normalization, Graphviz and `github-pages` upload. No publishing job or
write permissions are added.

Local unit tests cover planning, scope differences, execution evidence, missing
reports, threshold propagation and plain/ZIP LCOV normalization. The local
consumer comparison checks the complete pre-existing build/test selections and
coverage exclusions/thresholds. Full module coverage and documentation builds,
GitHub artifact handoff and release downloads still need end-to-end validation.

Configuration conventions are centralized in `configured_coverage.py` and the
schema 2 planner defaults. Consumers may omit standard platform/test fields and retention values.
Instrumentation configs, target scopes, generator labels, justification paths
and module output directories stay explicit. Defaults are merged before type
and path validation, so explicit invalid/null overrides still fail. Docs resolves
unique native-root test and root coverage sources; ambiguity fails preparation
instead of choosing by variant order. Explicit empty selectors disable a report.
The consumer migration comparison must continue to produce identical commands,
coverage metadata and Docs plans when redundant configuration is removed.

## Module-facing configuration

The compact schema 2 layout names profiles directly under `tests` and `builds`,
and `coverage`, with a top-level `docs` object. Platform, workspace and
execution options live on the profile rather than under `variants` and
`workflow-config`. `normalize()` adapts it to the existing planner so command
execution, workspace validation, evidence and artifact contracts remain shared.
The variants layout remains compatible; mixing the two forms is rejected.

Common check discovery now resolves standard root and `//tools` targets without
consumer overrides. Root labels have priority; an explicit override selects only
that label. Consumer catalog scenarios exercise discovery with their existing
required checks and pins. Baselibs retains only its explicit pre-commit skip:
its hooks auto-fix the same formatting/copyright already checked directly.
Its Python skip was redundant because it has no uv project. Configured workspaces
already require lockfiles, making a second lockfile requirement input redundant.

`test_common_target_detection.py` executes the workflow's actual shell script
against a controlled Bazel CLI, checking root/tools preference, absent targets
and explicit overrides. Compact-layout regression tests compare complete plans,
IDs and Docs selection with the variants form. Local consumer comparison also
covers both complete configurations, including ARM, integration and module-owned
coverage, rather than only the small reference example.

## Typed quality execution

`configured_quality.py` plans and executes Docs verification, Cargo Clippy,
and module-owned Bazel CodeQL. `configured-quality.yml` freezes
its checkout, executes independent profiles with read-only tokens and verifies
all reports. The main entry passes its already frozen SHA and requires quality
verification before Docs build. A setup or post-step failure also fails the gate.

Command recipes are shared by execution and verification. Reports must match the
full plan and commit, include all commands, and contain profile-specific evidence:
Bazel target events, Cargo workspace compiler artifacts,
or a nonempty queried CodeQL target set and parsed merged SARIF. Fetch-only CodeQL
is a distinct planned mode and cannot claim SARIF analysis. Gitlint belongs to the automatic common checks, with a `skip-gitlint` workflow
input; it is not part of the module configuration or quality matrix.

Only the separate SARIF publication job inherits caller write permissions. Fork
upload failures remain best effort as in the module workflow; unsuppressed finding
failures remain required. Scheduled CodeQL uses the same configuration and helper,
with an explicit reporting-only override. Existing consumers retain their QNX callers until migrated; configured QNX
profiles now use the shared SDK/VM setup without privileged PR events. New check names require branch-protection review.

Local regression tests cover failure propagation, empty selections, suppression
policy, source/config mismatches, missing/duplicate reports, command completeness,
real Gitlint success/failure, sanitizer scope and automatic Docs report selection.
Remote permissions, CodeQL tool execution and artifact transfers still need a
GitHub run after review.


## Configured PR execution

`on-pr.yml` directly owns configured execution, including
commit freezing, matrix orchestration, quality verification and Docs handoff.
`build-environment` consolidates authentication and fixed prerequisite profiles;
`configured_profiles.py` adapts batch inheritance and literal test controls from the
source workflow. `gcov_coverage.py` adds the genhtml recipe, and
`configured_quality.py` adapts its SCA/SBOM execution protocols. Public configuration
and migration boundaries are documented in [PR checks](../.github/workflows/on-pr.md).

The existing planner/executor/verifier remains authoritative. Do not add a second
execution path for migrated modules. Batch rows must preserve their own targets;
analysis-only configurations must not synthesize empty test evidence. Docs merges
selected test artifacts centrally through `test_reports.py`; differing reports at
the same package path fail rather than overwriting each other.

Local comparison against the preceding implementation preserves Baselibs and
Lifecycle commands, coverage policies and Docs selection; platform/prerequisite
metadata is additive. Regression tests cover QNX selection, batches, test options,
precheck failures, SCA findings, missing/invalid analysis and SBOM outputs, and Docs
merge conflicts. These checks establish local contracts, not remote execution of
the QNX SDK or a module analyzer.


Verified result metadata records the original profile, platform, check family,
workspace and exact batch name. Both executors emit the same contract;
aggregation rejects missing or changed facts against the frozen plan. Preserve
batch names during expansion rather than reconstructing them from row IDs.
Downstream adapters can derive their own layouts without module-specific aliases
for execution facts already known to the planner.

The schema-1 compatibility reader preserves its old `test-type` value solely as
an opaque row name. Schema 2 rejects that field. Test categories never enter
result metadata, execution policy or Docs source selection. Multiple native test
profiles require `docs.tests` to select the intended report explicitly.
