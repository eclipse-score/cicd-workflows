# Reusable GitHub Actions Workflows

This repository contains **reusable GitHub Actions workflows** designed to standardize CI/CD processes across multiple repositories
in `SCORE`.  
These workflows integrate with **Bazel** and provide a consistent way to run **documentation builds, license checks, static analysis, tests, formatting checks and copyright verification**

## Available Workflows

| Workflow                                       | Description                                                                                                                 |
| ---------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| **[PR Checks](.github/workflows/on-pr.md)**    | Main PR entry point: auto-detects capabilities and runs pre-commit, tests, format, copyright and lockfile checks in one job |
| **[Bazel Cache Maintenance](.github/workflows/cache-maintenance.md)** | Maintains lockfile-keyed repository and job-specific Bazel caches                                                           |
| **[Documentation](.github/workflows/docs.md)** | Builds and securely publishes documentation to GitHub Pages                                                                 |
| **[Daily Maintenance](.github/workflows/daily.md)** | Handles stale pull requests, cleans old documentation, and prunes obsolete caches                                  |
| **License Check**                              | Verifies OSS licenses and compliance                                                                                        |
| **Static Code Analysis**                       | Runs Clang-Tidy, Clippy, Pylint, and other linters                                                                          |
| **Tests**                                      | Executes tests using GoogleTest, Rust test, or pytest                                                                       |
| **Rust Coverage**                              | Computes Rust code coverage and uploads HTML reports                                                                        |
| **C++ Coverage**                               | Computes C++ code coverage using LCOV and uploads HTML reports                                                              |
| **Required Approvals**                         | Enforces stricter CODEOWNERS rules for multi-team approvals                                                                 |
| **QNX Build (Gated)**                          | Builds QNX Bazel targets with environment-gated secrets for forks                                                           |
| **CodeQL Scan**                                | Performs security and quality analysis using GitHub CodeQL                                                                  |

---

## Removed Workflows

The following standalone workflows were removed because their functionality is now
covered by `on-pr.yml` or `daily.yml`. Existing callers must migrate to the
replacement workflow.

| Removed workflow        | Covered by                    | Notes                                                                                                   |
| ------------------------ | ------------------------------ | --------------------------------------------------------------------------------------------------------- |
| `bzlmod-lock-check.yml`  | `on-pr.yml`                    | Runs the same `bazel mod tidy` + `bazel mod deps --lockfile_mode=error` checks whenever `MODULE.bazel.lock` exists. |
| `copyright.yml`          | `on-pr.yml`                    | Runs `bazel run //:copyright.check` whenever that target exists.                                          |
| `docs-cleanup.yml`       | `daily.yml`                    | Already deprecated; `daily.yml` performs the cleanup itself when GitHub Pages is enabled.                 |
| `docs-verify.yml`        | `docs.yml`                     | `docs.yml`'s build step (`bazel run //:docs`) already fails the job if the documentation does not build. |
| `format.yml`             | `on-pr.yml`                    | Runs `bazel test //:format.check` whenever that target exists.                                            |
| `score-pr-checks.yml`    | `on-pr.yml`                    | Same module-name validation logic; currently disabled in `on-pr.yml` pending a fix to the detection script. |
| `template-sync.yml`      | _none_                         | No longer needed; removed without replacement.                                                            |

---

## Using the Workflows in Your Repository

To use a reusable workflow, create a workflow file inside **your repository** (e.g., `.github/workflows/ci.yml`) and reference the appropriate workflow from this repository.

See the [Documentation workflows](.github/workflows/docs.md) guide for the
separate build and publishing workflows.

### **1. License Check Workflow**
**Usage Example**
```yaml
name: License Check CI

on:
  pull_request:
  push:
    branches:
      - main

jobs:
  license-check:
    uses: eclipse-score/cicd-workflows/.github/workflows/license-check.yml@main
    with:
      repo-url: "${{ github.server_url }}/${{ github.repository }}" # optional, this is the default
      bazel-target: "run //:license-check" # optional, this is the default
    secrets:
      dash-api-token: ${{ secrets.ECLIPSE_GITLAB_API_TOKEN }} # mandatory - the Eclispe DASH API token 
```

This workflow:

✅ Runs **DASH license compliance checks** for **Rust, C++, and Python**  
✅ Uses the **organization secret** `ECLIPSE_GITLAB_API_TOKEN`  
✅ Comments results directly on the **Pull Request**

> ℹ️ **Note:** You can override the Bazel command using the `bazel-target` input.  
> **Default:** `run //:license-check`

---

### **2. Static Code Analysis Workflow**
**Usage Example**
```yaml
name: Static Analysis CI

on:
  pull_request:
  push:
    branches:
      - main

jobs:
  static-analysis:
    uses: eclipse-score/cicd-workflows/.github/workflows/static-analysis.yml@main
    with:
      bazel-targets: "//..."            # optional, default
      bazel-config: "lint"             # optional, default
      bazel-args: "--@aspect_rules_lint//lint:fail_on_violation=true"  # optional
```

This workflow:  
✅ Runs **Clippy** via Bazel on the selected targets  
✅ Publishes **Clippy reports** as an artifact  
✅ Fails the job if Bazel fails or if any Clippy report is non-empty  
✅ Writes a summary to the GitHub job summary  

Inputs:
- `bazel-targets`: Bazel targets to build (default: `//...`)
- `bazel-config`: Bazel config to apply (default: `lint`, set empty to disable)
- `bazel-args`: Extra Bazel args (e.g., `--@aspect_rules_lint//lint:fail_on_violation=true`)

---

### **3. Tests Workflow**
**Usage Example**
```yaml
name: Test CI

on:
  pull_request:
  push:
    branches:
      - main

jobs:
  tests:
    uses: eclipse-score/cicd-workflows/.github/workflows/tests.yml@main
```

This workflow:  
✅ Runs **GoogleTest** for C++  
✅ Runs **Rust Unit Tests**  
✅ Runs **pytest** for Python  

---

### **4. Rust Coverage Workflow**
**Usage Example**
```yaml
name: Rust Coverage CI

on:
  pull_request:
    types: [opened, reopened, synchronize]
  workflow_dispatch:

jobs:
  rust-coverage:
    uses: eclipse-score/cicd-workflows/.github/workflows/rust-coverage.yml@main
    with:
      bazel-test-targets: "//src/rust/..."
      bazel-test-config-flags: "--config=per-x86_64-linux --config=ferrocene-coverage"
      bazel-test-args: "--nocache_test_results"
      coverage-target: "//:rust_coverage"
      min-coverage: 90
      coverage-artifact-name: "rust-coverage-html"
```

This workflow:  
✅ Runs **Rust tests** with coverage instrumentation  
✅ Generates **coverage reports** via Bazel  
✅ Uploads the **HTML coverage report** as an artifact  

---

### **5. C++ Coverage Workflow**
**Usage Example**
```yaml
name: C++ Coverage CI

on:
  pull_request:
    types: [opened, reopened, synchronize]
  push:
    branches:
      - main
  merge_group:
    types: [checks_requested]

jobs:
  coverage-report:
    uses: eclipse-score/cicd-workflows/.github/workflows/cpp-coverage.yml@main
    with:
      bazel-target: "//..."
```

---

### **6. Required Approvals Workflow**

This workflow enforces **stricter CODEOWNERS checks** than GitHub’s defaults.  
Normally, GitHub requires approval from *any one* codeowner when multiple are listed.  
With this workflow, you can enforce that **all required teams approve** (or set a minimum count).

**Usage Example**

```yaml
name: Enforce Approvals

on:
  pull_request:
    types: [opened, reopened, synchronize, ready_for_review]
  pull_request_review:
    types: [submitted, edited, dismissed]

jobs:
  enforce:
    uses: eclipse-score/cicd-workflows/.github/workflows/required-approvals.yml@main
    with:
      pat_secret: SCORE_BOT_PAT
      # Optional overrides:
      # min_approvals: 2
      # approval_mode: ALL
      # org_name: qorix-group
```

**Defaults**  
- `org_name`: `score`  
- `min_approvals`: `1`  
- `approval_mode`: `ALL`  
- `require_all_approvals_latest_commit`: always `true`  

**Key Features**  
✅ Enforces that *all relevant CODEOWNERS* approve (`ALL` mode)  
✅ Invalidates approvals on new commits (`require_all_approvals_latest_commit`)  
✅ Works with **org secrets** (e.g. `SCORE_BOT_PAT`) that must have `repo` + `read:org` scopes  
✅ Compatible with branch protection rules → can be marked as **required**  

---


### **7. QNX Build (Gated) Workflow**

Use this workflow when you need QNX secrets for forked PRs and want a manual approval gate via an environment.

**Usage Example**

```yaml
name: Scrample QNX (gated)

on:
  pull_request_target:
    types: [opened, reopened, synchronize]

jobs:
  qnx-build:
    uses: eclipse-score/cicd-workflows/.github/workflows/qnx-build.yml@main
    permissions:
      contents: read
      pull-requests: read
    with:
      bazel-target: "//..." # optional, default shown
      bazel-config: "x86_64-qnx"     # optional, default shown
      credential-helper: ".github/tools/qnx_credential_helper.py" # optional, default shown
      environment-name: "workflow-approval" # optional, default shown
      extra-bazel-flags: "--lockfile_mode=error" # optional, pass explicitly when needed
    secrets:
      score-qnx-license: ${{ secrets.SCORE_QNX_LICENSE }}
      score-qnx-user: ${{ secrets.SCORE_QNX_USER }}
      score-qnx-password: ${{ secrets.SCORE_QNX_PASSWORD }}
```

**Notes**
- Runs on `pull_request_target` so maintainers can approve the `workflow-approval` environment before secrets are used.
- Installs the QNX license, builds with the configured Bazel target/config, and cleans up the license directory.
- Additional Bazel flags are caller-controlled via `extra-bazel-flags`; the workflow does not enable `--lockfile_mode=error` by default.

---

### **8. CodeQL Security Scan Workflow**

This workflow performs security and quality analysis using GitHub's CodeQL with MISRA C++ coding standards.

**Usage Example**

```yaml
name: CodeQL Security Analysis

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]
  schedule:
    - cron: '0 0 * * 1' # Weekly on Monday

jobs:
  codeql-scan:
    uses: eclipse-score/cicd-workflows/.github/workflows/codeql.yml@main
    with:
      build-script: "bazel build //..." # optional, default shown
```

**Defaults**  
- `build-script`: `bazel build //...`  

**Key Features**  
✅ Scans C/C++ code for security vulnerabilities and bugs  
✅ Applies MISRA C++ coding standards  
✅ Uploads SARIF results as artifacts  
✅ Integrates with GitHub Security tab  
✅ Supports custom Bazel build commands  

---

##  How to Update Workflows
Since these workflows are centralized, updates in the `cicd-workflows` repository will **automatically apply to all repositories using them**. If you need a specific version, reference a **tagged release** instead of `main`:
```yaml
uses: eclipse-score/cicd-workflows/.github/workflows/tests.yml@v1.0.0
```

---

## 🏃‍♂️ Runner Selection Logic

All workflows in this repository use the following logic for selecting the runner:

```yaml
runs-on: ${{ inputs.runner-labels && fromJSON(inputs.runner-labels) || vars.runner_labels_ghub_standard_x64 && fromJSON(vars.runner_labels_ghub_standard_x64) || vars.REPO_RUNNER_LABELS && fromJSON(vars.REPO_RUNNER_LABELS) || 'ubuntu-latest' }}
```

This means:

- If the caller workflow passes a `runner-labels` input when invoking the reusable workflow (`with: runner-labels: ...`), that value is used as the runner label(s).
- Otherwise, if your repository defines a variable named `runner_labels_ghub_standard_x64` (or any of the other supported ones) or `REPO_RUNNER_LABELS` (e.g., in repository or organization settings), its value will be used as the runner label(s).  
  This allows you to use **self-hosted runners** or any custom runner configuration.
- If none of the above are set, the workflow will default to GitHub-hosted `ubuntu-latest`.

**Why?**  
This approach allows forked repositories or projects with special requirements to use their own runners, while everyone else gets a reliable default. The `runner-labels` input additionally lets an individual caller override the runner for a single workflow invocation without changing repository/organization-wide variables.

> ℹ️ **Tip:** To use a self-hosted runner, either pass the `runner-labels` input to the workflow call, or set the `runner_labels_ghub_standard_x64` or `REPO_RUNNER_LABELS` variable in your repository or organization settings to the label(s) of your runner.

### Runner labels variable naming convention

Since it is very likely the case that different workflows will need different runners of different sizes, OSes and architectures to be cost efficiently using the runner infrastructure the variable that specifies the runner labels shall follow this naming convention:

`runner_labels_<os>_<size>_<architecture>`

As of today the following runner label variables are currently used/supported:

- `runner_labels_ghub_standard_x64`
- `runner_labels_ghub22_standard_x64`
- `runner_labels_ghub24_standard_x64`

Where:

- os:
  - ghub - GitHub Ubuntu latest OS image
  - ghub22 - GitHub Ubuntu 22.04 OS image
  - ghub24 - GitHub Ubuntu 24.04 OS image
- size: standard - Maps to the specs of the "Ubuntu latest" GitHub-hosted runner
- architecture: x64 - Maps to the architecture of the standard "Ubuntu latest" GitHub-hosted runner. The value is taken from the [GitHub hosted runners reference page](https://docs.github.com/en/actions/reference/runners/github-hosted-runners)

Due to this new naming convention the variable **REPO_RUNNER_LABELS is deprecated** and will be removed eventually!

### Runner labels variable value syntax

The value of the runner labels variable must be either a JSON array of strings, where each string is a valid GitHub Actions runner label, or a single string.

An example of a valid value for the variable when using a single label is `"self-hosted"`, which would select any self-hosted runner regardless of its other labels if there are multiple available.

An example of a valid value for the variable when using multiple labels is the
following JSON array:

```json
["self-hosted", "linux", "x64", "custom-label"]
```

This allows you to specify multiple labels for your runner, which can be used to match it in the workflow. For example, if you have a self-hosted runner with the labels `self-hosted`, `linux`, `x64`, and `custom-label`, you can set the variable to the above JSON array, and the workflow will use that runner when it runs.

The `runner-labels` workflow input accepts the same syntax (a single string or a JSON array string) and is available on every reusable workflow in this repository that selects its runner via this logic.

> ⚠️ **Caller syntax:** `runner-labels` is a `string` input, and `with:` values for a reusable workflow call only accept scalars — a bare YAML sequence is rejected. Quote the JSON value as a string in the caller:
>
> ```yaml
> with:
>   runner-labels: '["self-hosted", "linux", "x64"]'   # correct: quoted JSON array
>   # runner-labels: ["self-hosted", "linux", "x64"]   # WRONG: real YAML sequence, fails schema validation
> ```
>
> For a single label, quote it as a JSON string too: `runner-labels: '"self-hosted"'`.
