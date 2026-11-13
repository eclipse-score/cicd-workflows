# Static Analysis CodeQL (`static-analysis-codeql.yml`)

`static-analysis-codeql.yml` is a **reusable workflow** that runs the Bazel based
CodeQL analysis from [`score_tools`](https://github.com/eclipse-score/score_tools)
(`//codeql`) for C/C++ code. It prepares the runner (disk space, Bazel caches,
optional QNX SDP), runs the analysis through `bazel run`, and publishes the results as

- a workflow **artifact** (SARIF, CSV, summaries, logs),
- a **job summary** (compiler table, findings per report/severity/rule), and
- a single **GitHub Code Scanning** analysis (optional).

The analysis itself (query packs, report definitions, presets, compile-command
replay) lives in `score_tools`; this workflow only provides the CI plumbing. Which
targets, configs and reports are analyzed is normally configured once in the
consuming repository with the `codeql_sca(...)` Bazel macro, so the caller workflow
stays small.

> **Not to be confused with [`codeql.yml`](codeql.yml) ("CodeQL Scan").**
> `codeql.yml` uses the `github/codeql-action` autobuild/`init`/`analyze` flow with
> GitHub's default C/C++ configuration plus MISRA C++. `static-analysis-codeql.yml`
> uses pinned CodeQL CLI and query pack versions, extracts the database by replaying
> the production compiler's compile commands (GCC for Linux, `qcc`/`q++` for QNX),
> supports QM/ASIL report presets and uploads one combined SARIF file.

## Usage

Create `.github/workflows/static-analysis-codeql.yml` in the consuming repository:

```yaml
name: QA / Static Analysis CodeQL

on:
  pull_request:
    types: [opened, reopened, synchronize]
  push:
    branches: [main]
  schedule:
    - cron: "0 2 * * *"
  workflow_dispatch:

permissions:
  contents: read

concurrency:
  group: ${{ github.workflow }}-${{ github.event.pull_request.number || github.ref }}
  cancel-in-progress: true

jobs:
  linux:
    uses: eclipse-score/cicd-workflows/.github/workflows/static-analysis-codeql.yml@main
    permissions:
      contents: read
      actions: read
      security-events: write
    with:
      sca-target: //tools/codeql:sca_linux
      # PRs: fast QM preset; main/nightly: full ASIL preset including MISRA C++
      safety-level: ${{ github.event_name == 'pull_request' && 'QM' || 'ASIL' }}
      category: static-analysis-codeql/linux
```

`sca-target` is the only required input. It is the Bazel label of a runnable
created by `codeql_sca(...)` in the consumer's `BUILD` file (or
`@score_tools//codeql:sca` if everything is passed through the inputs below).
The workflow runs

```text
bazel run "<sca-target>" -- --config=<c> --platform=<p> --safety-level=<l> --report=<r> \
  --output-dir=<dir> --sarif-category=<category> --bazel-option=<flag> <extra-args> <bazel-targets>
```

where every option is only added when the corresponding input is set. Explicit
`bazel-targets` override the targets baked into the macro.

### QNX

QNX analyses need the QNX SDP, so they use the same environment-gated approval
mechanism as [`qnx-build.yml`](qnx-build.yml). Secrets are only available to
pull requests from forks via `pull_request_target`; a maintainer must approve the
`workflow-approval` environment before the fork's code is checked out and run.

```yaml
name: QA / Static Analysis CodeQL (QNX)

on:
  pull_request_target:
    types: [opened, reopened, synchronize]
  schedule:
    - cron: "0 3 * * *"
  workflow_dispatch:

permissions:
  contents: read

jobs:
  qnx:
    uses: eclipse-score/cicd-workflows/.github/workflows/static-analysis-codeql.yml@main
    permissions:
      contents: read
      actions: read
      security-events: write
    with:
      sca-target: //tools/codeql:sca_qnx
      platform: qnx
      category: static-analysis-codeql/qnx
      # environment-name: workflow-approval        # optional, default shown
      # credential-helper: .github/tools/qnx_credential_helper.py   # optional, default shown
    secrets:
      score-qnx-license: ${{ secrets.SCORE_QNX_LICENSE }}
      score-qnx-user: ${{ secrets.SCORE_QNX_USER }}
      score-qnx-password: ${{ secrets.SCORE_QNX_PASSWORD }}
      qnx-sdp-github-token: ${{ secrets.SCORE_BOT_QNX_PKG_PAT }} # optional, for QNX SDP tarball release downloads
```

For QNX the workflow

- gates fork PRs behind the `approval` job (`environment-name`), exactly like `qnx-build.yml`;
- fails early with a clear message when `score-qnx-license`, `score-qnx-user` or
  `score-qnx-password` is missing;
- installs the QNX license/credential helper (`setup-qnx-sdp`) and passes
  `--credential_helper=*.qnx.com=<helper>` to the nested Bazel invocations;
- keeps the **repository and external Bazel caches disabled**, because the proprietary
  QNX SDP must not end up in the Actions cache (the disk cache only holds build outputs
  and stays enabled);
- exposes `qnx-sdp-github-token` as `GH_TOKEN` for the SDP download from
  [eclipse-score/qnx_sdp_pkg](https://github.com/eclipse-score/qnx_sdp_pkg).

## Inputs

| Input | Default | Description |
| ----- | ------- | ----------- |
| `sca-target` | _required_ | Bazel label of the runnable created by `codeql_sca()` (or `@score_tools//codeql:sca`). Must be a single label that does not start with `-`. |
| `bazel-targets` | empty | Override of the analyzed Bazel targets (whitespace separated, negative patterns like `-//foo/...` allowed). Empty uses the macro's targets. |
| `bazel-config` | empty | Override of the Bazel `--config` values for the analysis build (comma or whitespace separated). Empty uses the macro's configs. |
| `extra-bazel-flags` | empty | Bazel flags (whitespace separated), each forwarded as `--bazel-option=<flag>`. |
| `platform` | `linux` | `linux` or `qnx`. |
| `safety-level` | empty | Report preset: empty (macro default), `QM` or `ASIL`. |
| `report` | empty | Reports to generate (whitespace separated). Empty uses the preset's/macro's reports. |
| `extra-args` | empty | Additional `sca` arguments, split on whitespace. No quoting and no shell evaluation, so values cannot contain spaces. |
| `category` | `static-analysis-codeql/<platform>[/<safety-level>]` | Code Scanning category of the uploaded analysis. |
| `artifact-name` | `static-analysis-codeql-<platform>[-<safety-level>]` | Name of the results artifact; only letters, digits, `.`, `_` and `-`. |
| `upload-sarif` | `true` | Publish `results/code-scanning.sarif` to Code Scanning (always skipped for PRs from forks and for `pull_request_target` events). |
| `bazel-disk-cache` | empty | Suffix of the Bazel disk cache key (`static-analysis-codeql-<platform><suffix>`); use it to separate callers that analyze different things on the same platform. |
| `disk-space-level` | `4` | Level passed to `eclipse-score/more-disk-space` (0-4). |
| `runner-labels` | empty | JSON-encoded runner label(s); falls back to `vars.runner_labels_ghub_standard_x64`, then `vars.REPO_RUNNER_LABELS`, then `ubuntu-24.04`. |
| `github-app-client-id` | empty | GitHub App client ID for private dependencies (with the `github-app-private-key` secret). |
| `environment-name` | `workflow-approval` | QNX only: environment that gates fork PRs. |
| `credential-helper` | `.github/tools/qnx_credential_helper.py` | QNX only: path to the QNX credential helper script. |
| `score-qnx-license-server` | empty | QNX only: license server address, e.g. `6287@license-server-hostname`. |

## Secrets

| Secret | Required | Description |
| ------ | -------- | ----------- |
| `github-app-private-key` | no | GitHub App private key used with `github-app-client-id` to access private dependencies. |
| `token` | no | Token for private dependencies; alternative to the GitHub App credentials. |
| `qnx-sdp-github-token` | no | Token with read access to `eclipse-score/qnx_sdp_pkg` for QNX SDP release assets. |
| `score-qnx-license` | QNX | Base64-encoded QNX license content. |
| `score-qnx-user` | QNX | QNX account username. |
| `score-qnx-password` | QNX | QNX account password. |

The QNX secrets are optional in the interface so that Linux callers do not need to pass
them; the workflow validates their presence at runtime when `platform` is `qnx`.

## Outputs

| Output | Description |
| ------ | ----------- |
| `artifact-name` | Name of the uploaded results artifact. |
| `sarif-file` | Path of the combined SARIF file **inside the artifact** (`results/code-scanning.sarif`); empty if none was produced. |

## Presets and reports

The report definitions and presets are part of `score_tools` (`codeql/config/reports.toml`)
and can be extended by the consumer through `codeql_sca(...)`. `score_tools` currently provides:

| Preset (`safety-level`) | Reports |
| ----------------------- | ------- |
| `QM` | `codeql-report` (CodeQL C/C++ code-scanning suite, CERT C/C++ level 1), `codeql-report-complexity-qm` |
| `ASIL` | `codeql-report-safety` (the `codeql-report` queries plus MISRA C++:2023 mandatory and required rules), `codeql-report-complexity-asil` |

Both presets also run `codeql/cpp-queries:AlertSuppression.ql`, so
`// codeql[<rule-id>]` comments are processed and the affected results appear as
suppressed instead of as findings. The presets differ in the query set and in the
complexity thresholds:

| Complexity metric | `QM` | `ASIL` |
| ----------------- | ---- | ------ |
| Lines of code per function | 200 | 100 |
| Lines of code per file | 4000 | 2000 |
| Cyclomatic complexity per function | 20 | 10 |
| Parameters per function | 7 | 5 |
| `goto` statements per function | 0 | 0 |
| Recursive paths per function | 0 | 0 |
| Public interfaces per component | 10 | 3 |

All checks are reported as warnings except "public interfaces per component": it
is advisory (`@problem.severity recommendation`, SARIF level `note`), so it shows
up in Code Scanning and the job summary but never trips `--fail-on warning`.

An empty `safety-level` uses whatever the `codeql_sca(...)` macro defines. `report`
selects individual reports instead of a preset. Run `bazel run <sca-target> -- --list-reports`
locally to see the current definitions. CodeQL CLI and query pack versions are pinned
exactly (see `pins.json` in the results); the workflow never downloads "latest" packs.

All reports run in one database query pass. Each report gets its own
`<report>.sarif` / `<report>.csv`; additionally one combined `code-scanning.sarif`
(a single SARIF run carrying the category) is created for Code Scanning, so that
overlapping reports do not produce duplicate alerts.

## Required caller permissions

GitHub caps the permissions of a reusable workflow at the permissions the calling job
grants, so the **caller job must grant**:

```yaml
permissions:
  contents: read          # checkout
  actions: read           # required by the SARIF upload on private repositories
  security-events: write  # upload to Code Scanning
```

The `analyze` job requests exactly these three permissions (no `actions: write`, no
`packages`). If `upload-sarif: false` is set, `security-events: write` is still
declared by the workflow, so the caller must grant it anyway (or the workflow fails
to start); use `upload-sarif: false` only when you do not want alerts published.

## Behavior notes

### Pull requests from forks

- Linux: for `pull_request` events from forks the token is read-only, so the SARIF upload
  is **skipped** (the analysis, artifact and job summary still run). Results stay
  available in the artifact and the job summary.
- QNX: use `pull_request_target`; the `approval` job blocks on the environment
  (`environment-name`) until a maintainer approves. The fork's code is only checked out
  after approval. The SARIF upload is skipped for every `pull_request_target` run, because
  such runs execute in the context of the base branch and their alerts must not be attributed
  to it; the artifact and job summary hold the results. Scheduled/`push` runs upload normally.
- `pull_request` runs of QNX analyses from forks have no access to secrets and fail
  the input validation with an explicit message.

### Code Scanning category

The category is the identity of an analysis in Code Scanning. Keep it **stable per
analysis context** and unique per workflow/platform: GitHub rejects multiple runs of
the same tool and category in one upload, and re-using a category for a different
analysis replaces its alerts. The default
`static-analysis-codeql/<platform>[/<safety-level>]` is used when `category` is empty.
Using one constant category per job (regardless of preset) lets PR (QM) and main
(ASIL) results be compared in the same category, but the PR then shows the ASIL-only
alerts of `main` as fixed. Use per-preset categories (and run both presets on `main`)
if that matters.

### Caching

- Bazel disk/repository/external caches via `setup-bazel-cache` (QNX: disk cache only).
- The CodeQL query pack cache (`~/.cache/score-codeql/packs`) is cached with
  `actions/cache`, key `static-analysis-codeql-packs-<hash of MODULE.bazel.lock>`.
  Pack directories are version-addressed, so stale cache entries are harmless.

### Artifacts and retention

The artifact (14 days) contains `results/` (SARIF, CSV, `summary.md`, `pins.json`,
`compilers.json`, extraction and SARIF summaries) and, if present, `work/**/*.log` and
`work/compile_commands.filtered.json`. The CodeQL database itself is not uploaded.
The artifact name must be unique per workflow run; callers using a matrix must pass a
unique `artifact-name` and `category` per job.

### Findings do not fail the run

The job fails on tool or extraction failures (including the compiler check below), not on
findings. Findings are reported in Code Scanning and the job summary. Callers that
want gating can pass `extra-args: --fail-on error` (or `warning`).

### Security

All inputs are passed to the scripts as environment variables; nothing from `inputs.*`
or `secrets.*` is interpolated into shell code, and the whitespace splitting of
`bazel-config`, `report`, `extra-bazel-flags`, `extra-args` and `bazel-targets` happens
in Bash arrays without `eval` or globbing. The workflow intentionally offers no
"setup command" input; add your own job or step in a calling workflow if you need
one. All third-party actions are pinned by commit SHA.

## Compiler identity gate

The analysis must extract with the **same compiler the product ships with** (the
hermetic GCC toolchain for Linux, `qcc`/`q++` for QNX). There is no Clang fallback of
any kind. `score_tools` therefore verifies the compiler at three points and fails with
a message such as

```text
ERROR: compiler check failed: expected GCC (^x86_64-unknown-linux-gnu-(gcc|g\+\+|c\+\+)$),
       found 'clang++' in 57/57 compile commands (bazel --config=...).
       The CodeQL analysis requires the production GCC toolchain; no fallback is used.
```

1. **Before the replay** (fail fast): every compiler found in the filtered compile commands
   must match the expected basename pattern, must not be Clang, and must be GCC 12 on Linux.
2. **During the replay**: statically linked compilers (like the hermetic GCC) are started through a tracing
   wrapper; a static compiler that was not rewritten is an error, because the tracer could not see it.
3. **After the extraction**: the database is queried for the compilers that were really
   observed; they must match the pattern, must live inside the Bazel output base (the hermetic toolchain, which rejects a silent
   fall-back to a host `/usr/bin/g++`), and the number of extracted compilations must match
   the number of replayed commands (within the extraction thresholds).

The expectation is defined by the consumer's `codeql_sca(expected_compilers = [...])`
(sensible defaults per platform); this workflow has no separate input. For
experiments it can be overridden via `extra-args: --expect-compiler=<regex>`.

### Reading `compilers.json` and the job summary

The job summary starts with a **Compiler** table, for example:

| Compiler | Version | Linkage | Wrapper | Compile commands | Extracted |
| -------- | ------- | ------- | ------- | ---------------- | --------- |
| `x86_64-unknown-linux-gnu-g++` | 12.2.0 | static | via wrapper | 57 | 57 |

`results/compilers.json` (part of the artifact) carries the same information for each
compiler: basename, resolved path, `-dumpfullversion` output, static/dynamic, whether the
wrapper was used (`via_wrapper`), and the number of compile commands vs. extracted
compilations. Use it to trace every SARIF file back to the compiler that produced the
database. Expect **only** the production compiler there (for Linux only the GCC
driver) and `commands == extracted`; anything else means the analysis does not
represent the shipped binary.

## Results layout

```text
results/
  <report>.sarif, <report>.csv   one pair per selected report
  code-scanning.sarif            combined single-run SARIF (the only file uploaded to Code Scanning)
  summary.md                     job summary: compilers, extraction stats, findings, pinned versions
  extraction-summary.txt         extraction quality (failed commands, parse errors)
  sarif-summary.txt              SARIF checks (including result location URIs)
  pins.json                      CodeQL CLI and query pack versions actually used
  compilers.json                 compiler identity report
work/                            only logs and compile_commands.filtered.json are uploaded
```

## Runner selection

The jobs run on `runner-labels` if set, otherwise on `vars.runner_labels_ghub_standard_x64`,
then `vars.REPO_RUNNER_LABELS`, otherwise `ubuntu-24.04`. The analysis needs a host
C compiler to build the tracing wrapper and several GB of memory/disk; large runners
are recommended for big code bases (`disk-space-level` frees disk on standard runners).

## Troubleshooting

| Symptom | Likely cause and fix |
| ------- | -------------------- |
| `Invalid platform/safety-level/artifact-name/sca-target` | Input validation failed; the message names the offending input. |
| `platform 'qnx' requires the secret(s): ...` | Pass the `score-qnx-*` secrets; PRs from forks need `pull_request_target`. |
| Workflow does not start: "requesting `security-events: write` ... only allowed `none`" | The caller job lacks the permissions listed in [Required caller permissions](#required-caller-permissions). |
| `compiler check failed: expected GCC ... found 'clang++'` | The Bazel `--config` used for the analysis selects a Clang toolchain. Pass the config of the production GCC/QNX toolchain (`bazel-config` or the macro). There is no fallback. |
| Compiler check: "tracer did not observe the static GCC driver" | The tracing wrapper was not built or not used; check the replay log in the artifact and that a host `cc` exists on the runner. |
| Extraction check failed (failed command %, commands with issues %, parse errors) | Inspect `results/extraction-summary.txt` and `work/**/*.log` in the artifact. Fix the build errors, or tune the thresholds in the macro or via `extra-args` (`--max-failed-command-pct`, `--max-commands-with-issues-pct`, `--max-unique-parse-errors`) with a written justification. |
| Code Scanning shows no alerts or alerts at the wrong files | Check `results/sarif-summary.txt`: result locations must be repository-relative; absolute or `bazel-out/...` paths are dropped by Code Scanning. Findings in Bazel external repositories, the toolchain or system headers are removed from `results/code-scanning.sarif` on purpose (they are not part of the repository); they stay in the per-report files and are counted in `summary.md`. Make sure the compile commands refer to workspace sources. |
| Upload rejected: "only one run per tool and category" / duplicate category | Two jobs use the same `category`; give each job a unique, stable category. |
| `Upload SARIF` step skipped | Intended on forks' PRs, for `pull_request_target`, when `upload-sarif: false`, or when no `code-scanning.sarif` was produced (analysis failed). |
| Artifact name conflict on re-run or matrix | Pass a unique `artifact-name` per job (include `github.run_attempt` if re-runs must not conflict). |
| Out of memory / out of disk | Use larger runners via `runner-labels`, increase `disk-space-level`, or reduce parallelism with `extra-args` (`--threads`, `--ram`, `--replay-jobs`). |
| Query pack download fails or is rate limited | The workflow passes `github.token` for GHCR; check the job log of the pack download and re-run. Cached packs are reused. |
