# Reusable GitHub Actions Workflows

This repository contains **reusable GitHub Actions workflows** designed to standardize CI/CD processes across multiple repositories
in `S-CORE`.
These workflows integrate with **Bazel** and provide a consistent way to run **documentation builds, license checks, static analysis, tests, formatting checks and copyright verification**

## Available Workflows

| Workflow                | Description                                                        |
|-------------------------|--------------------------------------------------------------------|
| **Documentation Build** | Builds project documentation and deploys it to GitHub Pages         |
| **Documentation Cleanup** | Cleans up old documentation versions from the `gh-pages` branch   |
| **License Check**       | Verifies OSS licenses and compliance                               |
| **Static Code Analysis**| Runs Clang-Tidy, Clippy, Pylint, and other linters                 |
| **Tests**               | Executes tests using GoogleTest, Rust test, or pytest              |
| **Formatting Check**    | Verifies code formatting using Bazel-based tools                   |
| **Copyright Check**     | Ensures all source files have the required copyright headers        |

---

## Using the Workflows in Your Repository

To use a reusable workflow, create a workflow file inside **your repository** (e.g., `.github/workflows/ci.yml`) and reference the appropriate workflow from this repository.

### **1️ Documentation Build Workflow**
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

### **2️ Documentation Cleanup Workflow**
**Usage Example**
```yaml
name: Documentation Cleanup

on:
  schedule:
    - cron: '0 2 * * *' # every day at 2am UTC

jobs:
  docs-cleanup:
    uses: eclipse-score/cicd-workflows/.github/workflows/docs-cleanup.yml@main
    with:
      workflow-version: main
    secrets:
      token: ${{ secrets.GITHUB_TOKEN }}
```

This workflow:

✅ Cleans up old documentation versions from the `gh-pages` branch  
✅ Runs daily at 2am UTC  

---

### **3️ License Check Workflow**
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

### **4️ Static Code Analysis Workflow**
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
      bazel-target: "run //:static-analysis" # optional, this is the default
```

This workflow:  
✅ Runs **Clang-Tidy** for C++  
✅ Runs **Rust Clippy, Cargo Audit, and Cargo Geiger** for Rust  
✅ Runs **Pylint** for Python  

> ℹ️ **Note:** You can override the Bazel command using the `bazel-target` input.  
> **Default:** `run //:static-analysis`

---

### **5️ Tests Workflow**
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

### **6️ Copyright Check Workflow**
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

### **7️ Formatting Check Workflow**
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
  uses: bazel-contrib/setup-bazel@0.14.0
  with:
    disk-cache: true
    repository-cache: true
    bazelisk-cache: true
```

### Benefits

- **`disk-cache`**: Stores compiled Bazel outputs across jobs.
- **`repository-cache`**: Caches external dependencies (e.g., modules, WORKSPACE fetches).
- **`bazelisk-cache`**: Avoids re-downloading Bazel binaries.

This setup significantly reduces CI build time and improves reuse across different workflows.


### **Summary**
✅ **Standardized** CI/CD workflows across all projects  
✅ **Reusable & Maintainable** with centralized updates  
✅ **Bazel-powered** for consistent testing & analysis