# Reusable GitHub Actions Workflows

This repository contains **reusable GitHub Actions workflows** designed to standardize CI/CD processes across multiple repositories
in `SCORE`.  
These workflows integrate with **Bazel** and provide a consistent way to run **documentation builds, license checks, static analysis, tests, formatting checks and copyright verification**

## Available Workflows

| Workflow                       | Description                                                       |
| ------------------------------ | ----------------------------------------------------------------- |
| **Documentation Build**        | Builds project documentation and deploys it to GitHub Pages       |
| **Documentation Cleanup**      | Cleans up old documentation versions from the `gh-pages` branch   |
| **License Check**              | Verifies OSS licenses and compliance                              |
| **Static Code Analysis**       | Runs Clang-Tidy, Clippy, Pylint, and other linters                |
| **Tests**                      | Executes tests using GoogleTest, Rust test, or pytest             |
| **Rust Coverage**              | Computes Rust code coverage and uploads HTML reports              |
| **C++ Coverage**               | Computes C++ code coverage using LCOV and uploads HTML reports    |
| **Formatting Check**           | Verifies code formatting using Bazel-based tools                  |
| **Copyright Check**            | Ensures all source files have the required copyright headers      |
| **Required Approvals**         | Enforces stricter CODEOWNERS rules for multi-team approvals       |
| **QNX Build (Gated)**          | Builds QNX Bazel targets with environment-gated secrets for forks |
| **Documentation Verification** | Verifies documentation builds correctly and uploads results       |
| **CodeQL Scan**                | Performs security and quality analysis using GitHub CodeQL        |
| **SCORE PR Checks**            | Validates Bazel module naming conventions in pull requests        |
| **Bzlmod Lockfile Check**      | Enforces `MODULE.bazel.lock` consistency via `bazel mod tidy`     |
| **Template Sync**              | Synchronizes repository with eclipse-score/module_template        |

---

## Using the Workflows in Your Repository

To use a reusable workflow, create a workflow file inside **your repository** (e.g., `.github/workflows/ci.yml`) and reference the appropriate workflow from this repository.

### **1. Documentation Build Workflow**
**Usage Example**
```yaml 
name: Documentation CI

on:
  pull_request:
  push:
    branches:
      - main

jobs:
  docs:
    uses: eclipse-score/cicd-workflows/.github/workflows/docs.yml@main
    with:
      retention-days: 3
      # Optionally override:
      # workflow-version: main
      # bazel-target: "//docs:github-pages"
```
This workflow:

✅ Builds project documentation  
✅ Uploads it as an artifact  
✅ Deploys it to **GitHub Pages** on push to `main`  

---

### **2. Documentation Cleanup Workflow - DEPRECATED**
*Deprecated: This workflow is now integrated into the `Daily Maintenance` workflow. Use that workflow instead.*

**Usage Example**
```yaml
name: Documentation Cleanup

on:
  schedule:
    - cron: '0 2 * * *' # every day at 2am UTC

permissions:
  contents: write
  pages: write
  id-token: write

jobs:
  docs-cleanup:
    # Pin a version via vX.Y.Z tag or even better via a commit SHA for immutability.
    # Treat @main as experimental!
    uses: eclipse-score/cicd-workflows/.github/workflows/docs-cleanup.yml@vX.Y.Z
    with:
      workflow-version: main
    secrets:
      token: ${{ secrets.GITHUB_TOKEN }}
```

This workflow:

✅ Cleans up old documentation versions from the `gh-pages` branch  
✅ Runs daily at 2am UTC  
✅ Is intended for repositories with GitHub Pages enabled  

---

### **3. License Check Workflow**
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

### **4. Static Code Analysis Workflow**
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

### **5. Tests Workflow**
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

### **6. Rust Coverage Workflow**
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

### **7. C++ Coverage Workflow**
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

### **8. Copyright Check Workflow**
**Usage Example**
```yaml
name: Copyright Check CI

on:
  pull_request:
  push:
    branches:
      - main

jobs:
  copyright-check:
    uses: eclipse-score/cicd-workflows/.github/workflows/copyright.yml@main
    with:
      bazel-target: "run //:copyright-check" # optional, this is the default
```

This workflow:  
✅ Runs a **Bazel-based copyright**
✅ Ensures all source files have **Eclipse Foundation** headers

> ℹ️ **Note:** You can override the Bazel command using the `bazel-target` input.  
> **Default:** `run //:copyright-check`

---

### **9. Formatting Check Workflow**
**Usage Example**
```yaml
name: Formatting Check CI

on:
  pull_request:
  merge_group:
    types: [checks_requested]

jobs:
  formatting-check:
    uses: eclipse-score/cicd-workflows/.github/workflows/format-check.yml@main
    with:
      bazel-target: "test //:format.check" # optional, this is the default
```

This workflow:  
✅ Runs a **Bazel-based formatting check** (e.g., `buildifier`, `clang-format`, etc.)  
✅ Can be integrated into Pull Requests and Merge Queues  
✅ Ensures code adheres to formatting rules before merge

> ℹ️ **Note:** You can override the Bazel command using the `bazel-target` input.  
> **Default:** `test //:format.check`

---
### **10. Required Approvals Workflow**

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


### **11. QNX Build (Gated) Workflow**

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

### **12. Documentation Verification Workflow**

This workflow verifies that documentation builds correctly and can be used to validate documentation changes in pull requests.

**Usage Example**

```yaml
name: Documentation Verification

on:
  pull_request:
    types: [opened, reopened, synchronize]

jobs:
  docs-verify:
    uses: eclipse-score/cicd-workflows/.github/workflows/docs-verify.yml@main
    with:
      bazel-docs-verify-target: "//:docs_check" # optional, default shown
```

**Defaults**  
- `bazel-docs-verify-target`: `//:docs_check`  

**Key Features**  
✅ Verifies documentation builds successfully  
✅ Uses Bazel-based documentation checks  
✅ Provides verification result as output  
✅ Integrates with Bazel shared caching for performance  

---

### **13. CodeQL Security Scan Workflow**

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

### **14. SCORE PR Checks Workflow**

This workflow enforces SCORE-specific standards, particularly Bazel module naming conventions.

**Usage Example**

```yaml
name: PR Checks

on:
  pull_request:
    branches: [main]

jobs:
  score-checks:
    uses: eclipse-score/cicd-workflows/.github/workflows/score-pr-checks.yml@main
```

**No inputs required**

**Key Features**  
✅ Validates Bazel module names follow the pattern `^score_[[:lower:]_]+$`  
✅ Ensures module names start with `score_`  
✅ Allows only lowercase letters and underscores  
✅ Skips validation if no `MODULE.bazel` file exists  

**Examples of valid module names:**  
- `score_cli`  
- `score_compose`  
- `score_web_api`  

---

### **15. Template Sync Workflow**

This workflow automatically synchronizes your repository with the latest changes from `eclipse-score/module_template`.

**Usage Example**

```yaml
name: Template Sync

on:
  schedule:
    - cron: '0 0 * * 0' # Weekly on Sunday
  workflow_dispatch:

jobs:
  template-sync:
    uses: eclipse-score/cicd-workflows/.github/workflows/template-sync.yml@main
    with:
      pr_title: "[Template Sync] Upstream template update" # optional, default shown
      pr_commit_msg: "chore(template): upstream template update" # optional, default shown
      template_sync_ignore_file_path: ".github/.templatesyncignore" # optional, default shown
    secrets:
      SCORE_APPROVALS_PAT: ${{ secrets.SCORE_APPROVALS_PAT }}
```

**Defaults**  
- `pr_title`: `[Template Sync] Upstream template update`  
- `pr_commit_msg`: `chore(template): upstream template update`  
- `template_sync_ignore_file_path`: `.github/.templatesyncignore`  

**Key Features**  
✅ Automatically creates PRs with template updates  
✅ Respects `.templatesyncignore` file to exclude specific files  
✅ Uses `SCORE_APPROVALS_PAT` secret for authentication  
✅ Configurable PR titles and commit messages  
✅ Can be triggered on schedule or manually  

> ℹ️ **Note:** This workflow requires the `SCORE_APPROVALS_PAT` secret with appropriate permissions to create pull requests.

---

### **16. Bzlmod Lockfile Check Workflow**

This workflow keeps Bazel's lockfile in sync with your module declarations. It records the exact set of resolved module versions and extension outputs so builds are reproducible across machines. The job fails if the lockfile is missing or out of date after running `bazel mod tidy`, which means someone changed `MODULE.bazel` without updating the lockfile.

**Usage Example**

```yaml
name: Bzlmod Lockfile Check

on:
  pull_request:
  push:
    branches:
      - main

jobs:
  bzlmod-lock:
    uses: eclipse-score/cicd-workflows/.github/workflows/bzlmod-lock-check.yml@main
    with:
      working-directory: .
```

**Defaults**  
- `working-directory`: `.`  

This workflow:  
✅ Fails if `MODULE.bazel.lock` is missing  
✅ Runs `bazel mod tidy`  
✅ Fails if `MODULE.bazel` or `MODULE.bazel.lock` changes after tidy  

---

### **17. Daily Maintenance Workflow**

This workflow groups the repository's daily maintenance tasks. It always handles stale pull requests and also runs documentation cleanup when GitHub Pages is enabled for the repository.

**Usage Example**

```yaml
name: Daily Maintenance

on:
  schedule:
    - cron: '0 3 * * *' # Daily at 3am UTC
  workflow_dispatch:

permissions:
  contents: write
  issues: write
  pull-requests: write
  pages: write
  id-token: write

jobs:
  daily:
    uses: eclipse-score/cicd-workflows/.github/workflows/daily.yml@main
    permissions:
      contents: write
      issues: write
      pull-requests: write
      pages: write
      id-token: write
```

This reusable workflow does not expose any parameters. It applies a fixed maintenance policy:

- Marks pull requests as stale after `30` days of inactivity using the `stale` label  
- Closes stale pull requests after `10` more days, adding the `autoclosed` label when closing  
- Exempts pull requests labeled `keep-open`, `do-not-close`, `pinned`, or `feature_request` from being marked stale or auto-closed  
- Runs documentation cleanup on the `gh-pages` branch when GitHub Pages is enabled  

**Key Features**  
✅ Marks pull requests stale after the configured idle period  
✅ Posts a friendly comment asking whether the PR is still relevant, up to date, and ready to merge  
✅ Closes stale pull requests after the configured follow-up window with a separate explanation  
✅ Removes the stale label automatically when new activity appears  
✅ Supports exempt labels for long-running or intentionally parked pull requests  
✅ Skips issues entirely and targets pull requests only  
✅ Runs documentation cleanup only when the repository has GitHub Pages enabled  

> ℹ️ **Note:** If you only need stale pull request handling or need different timing or label behavior, use `actions/stale` directly in your repository instead of this reusable workflow.
 
> ℹ️ **Note:** This repository also runs its own combined daily maintenance workflow via [`.github/workflows/_local_daily.yml`](./.github/workflows/_local_daily.yml), which includes stale pull request handling and documentation cleanup when GitHub Pages is enabled.

---


##  How to Update Workflows
Since these workflows are centralized, updates in the `cicd-workflows` repository will **automatically apply to all repositories using them**. If you need a specific version, reference a **tagged release** instead of `main`:
```yaml
uses: eclipse-score/cicd-workflows/.github/workflows/tests.yml@v1.0.0
```

---

## ⚡️ Bazel Setup with Shared Caching

To improve performance and reduce redundant downloads across workflow runs, the reusable workflows configure Bazel with shared caching:

```yaml
- name: Setup Bazel with shared caching
  uses: bazel-contrib/setup-bazel@0.18.0
  with:
    disk-cache: true
    repository-cache: true
    bazelisk-cache: true
    cache-save: ${{ github.event_name == 'push' }}
```

### Benefits

- **`disk-cache`**: Stores compiled Bazel outputs across jobs.
- **`repository-cache`**: Caches external dependencies (e.g., modules, WORKSPACE fetches).
- **`bazelisk-cache`**: Avoids re-downloading Bazel binaries.
- **`cache-save`**: Saves the cache only on push events to avoid unnecessary cache updates (e.g., from pull requests).

This setup significantly reduces CI build time and improves reuse across different workflows.


### **Summary**
✅ **Standardized** CI/CD workflows across all projects  
✅ **Reusable & Maintainable** with centralized updates  
✅ **Bazel-powered** for consistent testing & analysis

## 🏃‍♂️ Runner Selection Logic

All workflows in this repository use the following logic for selecting the runner:

```yaml
runs-on: ${{ vars.runner_labels_ghub_standard_x64 && fromJSON(vars.runner_labels_ghub_standard_x64) || vars.REPO_RUNNER_LABELS && fromJSON(vars.REPO_RUNNER_LABELS) || 'ubuntu-latest' }}
```

This means:

- If your repository defines a variable named `runner_labels_ghub_standard_x64` (or any of the other supported ones) or `REPO_RUNNER_LABELS` (e.g., in repository or organization settings), its value will be used as the runner label(s).  
  This allows you to use **self-hosted runners** or any custom runner configuration.
- If `runner_labels_ghub_standard_x64` or `REPO_RUNNER_LABELS` is **not set**, the workflow will default to GitHub-hosted `ubuntu-latest`.

**Why?**  
This approach allows forked repositories or projects with special requirements to use their own runners, while everyone else gets a reliable default.

> ℹ️ **Tip:** To use a self-hosted runner, set the `runner_labels_ghub_standard_x64` or `REPO_RUNNER_LABELS` variable in your repository or organization settings to the label(s) of your runner.

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

### Runner labels variable syntax

The value of the runner labels variable must be a JSON array of strings, where each string is a valid GitHub Actions runner label. For example:

```json
["self-hosted", "linux", "x64", "custom-label"]
```

This allows you to specify multiple labels for your runner, which can be used to match it in the workflow. For example, if you have a self-hosted runner with the labels `self-hosted`, `linux`, `x64`, and `custom-label`, you can set the variable to the above JSON array, and the workflow will use that runner when it runs.
