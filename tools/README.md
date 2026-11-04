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

Three isolated worktrees use the local branch `central-workflow`:

| Repository | Local worktree | POC graph |
| --- | --- | --- |
| cicd-workflows | `score_cicd-workflows-ci-build` | Planner, executor, verifier, helper action and `on-pr.yml` |
| baselibs | `score_baselibs-ci-poc` | `score/result` native build/unit tests and ARM64 compilation |
| lifecycle | `score_lifecycle-ci-poc` | Health-monitor C++ native build/unit tests and ARM64 compilation |

Both consumer repositories add `.github/ci.yml` and a manual
`poc-configured-checks.yml`. The focused graphs exercise actual toolchains and
tests; they do not establish full module coverage. Existing module workflows
remain in place, including sanitizer, integration, coverage and documentation jobs.

The workflow and action references currently use
`eclipse-score/cicd-workflows@central-workflow`. They deliberately select the
experimental upstream branch for both workflow and helper action. GitHub execution
requires a way to trigger the consumer workflow from its branch.
A workflow_dispatch file normally must exist on the default branch to expose its
manual trigger. Do not merge this bootstrap wiring as a production release.
A release must pin the central helper action and reusable workflow to the same
reviewed implementation rather than fetching a moving main branch.

## Local execution evidence

Both consumer configurations were executed from the local worktrees on
2026-11-04 using the central executor and Bazel 8.8.2. All four rows and six
commands passed aggregate verification against their checkout and config hash.
The selected native tests passed: two Bazel test targets in baselibs and one in
lifecycle. Both ARM64 build-only rows passed too.

Base revisions were baselibs `19cab2bef2ff260ad5837dabd4f5cc75b22961bd` and
lifecycle `ff939fe2fc6bc118b434be3d5f7cc332597d7e14`, with the uncommitted POC
configurations. Local runs used isolated Bazel output roots and bounded compiler
parallelism. These results cover the planner/executor/verifier and selected
module graphs; GitHub cache setup and artifact transfer have not run yet.

## Limits to assess before migration

- The existing public setup-bazel-cache action always uses GITHUB_WORKSPACE and
  has no working-directory input. Nested modules are rejected until that action
  supports the same workspace contract. Do not override GitHub's reserved
  workspace variable to work around this limitation.
- The executor owns its build-event files. Cache setup disables only its test-hit
  reporter, which otherwise supplies a competing build-event destination.
- Only Linux x86_64 runners are supported. ARM64 rows cross-compile; they do not
  claim ARM runtime coverage. QNX requires a separate supported environment.
- Module configs may themselves use module-specific Bazel behavior. The POC
  deliberately offers no arbitrary shell hooks or package-installation options.
- Schema 1 compatibility covers the implemented tests subset only. Schema 2 adds
  `checks.builds` and explicit entry names. Unknown fields and duplicate keys fail.
- Workflow permissions, remote action/cache integration and artifact handoff need
  GitHub validation after review. Local executor tests cannot establish these.

## Reproduce locally

Use the central locked Python project for all operations; consumers do not need
Python project files. Bazel/Bazelisk must already be on PATH. In CI it is supplied
by `eclipse-score/cicd-actions/setup-bazel-cache`.

```sh
uv run --locked python -m pytest -v
uv run --locked ruff check tests/ci tools

uv run --locked python tools/configured_checks.py plan \
  --workspace ../score_baselibs-ci-poc --config .github/ci.yml --reports /tmp/poc-baselibs

uv run --locked python tools/configured_checks.py run \
  --workspace ../score_baselibs-ci-poc --config .github/ci.yml \
  --job linux-native-tests-unit --reports /tmp/poc-baselibs

uv run --locked python tools/configured_checks.py run \
  --workspace ../score_baselibs-ci-poc --config .github/ci.yml \
  --job linux-cross-builds-compiler --reports /tmp/poc-baselibs
```

Repeat with the lifecycle worktree and a distinct reports directory. Plan output
lists exact argv before any build starts. For local verification, import `plan`
and `verify` from `configured_checks`, passing the planned rows, reports directory
and the checkout's observed `git rev-parse HEAD`. The CLI verification mode reads
`EXPECTED` (the JSON plan) and `SOURCE_COMMIT`, matching the workflow's handoff.
