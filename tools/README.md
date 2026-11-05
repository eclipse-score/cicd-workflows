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
| lifecycle | Native and ARM64 rows and their build/test steps in `on-pr.yml` | Test-only sanitizer matrix, docs verification/link checks, QNX, Clippy and CodeQL |

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
- Only Linux x86_64 runners are supported. ARM64 build rows cross-compile;
  ARM64 test rows require the explicit QEMU user-mode profile. Emulated passes
  do not establish native ARM hardware coverage. QNX needs a separate environment.
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
  --job linux-native-tests-unit --reports /tmp/poc-baselibs

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

`configured_coverage.py` owns the two coverage contracts and Docs selection.
LLVM coverage preserves the template's `bazel-configs`, `bazel-targets`,
`justifications-yaml`, `coverage-html-bazel-target`, `coverage-threshold` and
`generate-artifacts-archive` fields as one object per variant. The SCORE report
CLI uses `--archive-dir`; the template's script hooks are deliberately absent.
The default threshold is report-only; both migrated consumers set their intended
values explicitly. `checks.coverage` is a distinct module Bazel orchestrator
contract, not an adapter for the template's GCOV options.

The built-in scope policy preserves baselibs' production universe query,
configured dependency query and missing/stale exclusion comparisons. The local
scope-check script remains available for developer use. Lifecycle keeps its
module-owned coverage target, including the combined C++/Rust orchestration.
Both consumers keep their release artifact names and ten-day retention. Their
local coverage workflows are replaced by configuration; baselibs' Linux entry
point also supports manual dispatch. Lifecycle's Docs verification and link-check
jobs remain independent. The central call includes coverage and Docs in its
success result, so the caller's merge/release gates cover them too.

Coverage collection stages HTML, plain LCOV at both supported root filenames,
and Cobertura (preserving the generator's `coverage.xml` or converting via the
centrally locked dependency). ZIP input uses only `lcov_report/lcov.dat` and is
never extracted as a directory tree. Missing/invalid reports fail execution.
Threshold failures retain available reports without turning the row green.

`docs.yml` adds optional `repository`, `ref`, `bazel-label` and `github-context`
inputs. The central caller uses argv execution and the preparation SHA, retaining
the legacy command-string path for existing callers. It reuses existing artifact
download/normalization, Graphviz and `github-pages` upload. No publishing job or
write permissions are added.

Local unit tests cover planning, scope differences, execution evidence, missing
reports, threshold propagation and plain/ZIP LCOV normalization. The local
consumer comparison checks the complete pre-existing build/test selections and
coverage exclusions/thresholds. Full module coverage and documentation builds,
GitHub artifact handoff and release downloads still need end-to-end validation.

Configuration conventions are centralized in `configured_coverage.py` and the
schema 2 planner defaults. Consumers omit standard platform/test fields, report
paths, generator labels and retention values. Defaults are merged before type
and path validation, so explicit invalid/null overrides still fail. Docs resolves
unique native-root test and root coverage sources; ambiguity fails preparation
instead of choosing by variant order. Explicit empty selectors disable a report.
The consumer migration comparison must continue to produce identical commands,
coverage metadata and Docs plans when redundant configuration is removed.

## Module-facing configuration

The compact schema 2 layout names profiles directly under `tests` and `builds`,
with top-level `llvm-coverage`/`coverage` and `docs`. Platform, workspace and
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
