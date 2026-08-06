# Bazel cache maintenance

`cache-maintenance.yml` maintains the shared Bazel caches of a repository that
uses `MODULE.bazel.lock`. Follow the steps below to integrate it into an
existing repository.

## Quick start

Before starting, commit an up-to-date `MODULE.bazel.lock`.

### 1. Cache each Bazel job

Replace direct use of `bazel-contrib/setup-bazel` (or another cache setup
action) in every Bazel job with the S-CORE cache action:

```yaml
- name: Setup Bazel with shared caching
  uses: eclipse-score/cicd-actions/setup-bazel-cache@<actions-sha>
  with:
    unique-cache-name: ${{ github.job }}-<workflow_name>
```

Use a stable suffix such as `-build-qnx-x86_64` to keep cache names unique per
workflow and target configuration.

### 2. Make main-branch build workflows callable

Every workflow that should warm a Bazel cache on `main` must meet both of these
conditions:

- It must not run directly on pushes to `main`.
- It must declare `workflow_call` so the maintenance workflow can invoke it
  after repository-cache maintenance has finished.

Remove the existing `push` trigger, add `workflow_call`, and retain any
pull-request or manual triggers that are useful for the workflow.

Workflow triggered only by pushes to `main` — before:

```yaml
on:
  push:
    branches: [main]
```

Workflow triggered only by pushes to `main` — after:

```yaml
on:
  workflow_call:
```

Workflow with pull-request checks — before:

```yaml
on:
  pull_request:
    types: [opened, synchronize, reopened]
```

Workflow with pull-request checks — after:

```yaml
on:
  pull_request:
    types: [opened, synchronize, reopened]
  workflow_call:
```

### 3. Add the orchestration workflow

Create `.github/workflows/cache-maintenance.yml` in the consuming repository.
It validates repository dependencies first, warms the selected build caches
afterward, and prunes obsolete caches last. Pin reusable workflows and actions
to reviewed commit SHAs.

```yaml
name: Cache maintenance

on:
  # Read-only self-test of every `variants` entry before merge.
  pull_request:
    types: [opened, reopened, synchronize, labeled, unlabeled]
  # The same self-test for the merge-queue commit.
  merge_group:
    types: [checks_requested]
  # Useful for troubleshooting; also read-only.
  workflow_dispatch:
  # Only this event can warm or prune shared caches.
  push:
    branches: [main]

jobs:
  repository_cache_maintenance:
    permissions:
      # Required when a lockfile change replaces and prunes caches.
      actions: write
      contents: read
    secrets:
      # Optional unless a configured variant uses QNX.
      qnx-license: ${{ secrets.SCORE_QNX_LICENSE }}
      qnx-user: ${{ secrets.SCORE_QNX_USER }}
      qnx-password: ${{ secrets.SCORE_QNX_PASSWORD }}
      # Optional unless variants fetch private GitHub repositories.
      github-app-private-key: ${{ secrets.ETAS_ENG_SCORE_BOT_PRIVATE_KEY }}
    uses: eclipse-score/cicd-workflows/.github/workflows/cache-maintenance.yml@<workflows-sha>
    with:
      github-app-client-id: ${{ vars.ETAS_ENG_SCORE_BOT_APP_ID }}
      # Each line becomes one `bazel fetch` invocation.
      variants: |
        //...
        --config=target_config_1 //...

  warmup-qnx-x86_64:
    needs: repository_cache_maintenance
    # PR, merge-queue, and manual runs validate variants above but never warm
    # or write shared caches.
    if: ${{ github.event_name == 'push' }}
    secrets: inherit
    uses: ./.github/workflows/build_qnx_x86_64.yml

  delete_old_caches:
    needs: warmup-qnx-x86_64
    if: ${{ !cancelled() && github.event_name == 'push' }}
    runs-on: ubuntu-24.04
    permissions:
      # Prune after every warmup job has completed its cache-save post-step.
      actions: write
      contents: read
    steps:
      - name: Prune obsolete Bazel caches
        uses: eclipse-score/cicd-actions/prune-cache@<actions-sha>
```

## Corner cases and operations

### Credentials and private dependencies

The QNX credentials are optional, but all three must be supplied when a
configured variant requires QNX. Likewise, provide GitHub App credentials only
when a variant fetches dependencies from private GitHub repositories. To use a
token instead, pass it as the `token` secret and omit
`github-app-client-id` and `github-app-private-key`.

The older `score-qnx-license`, `score-qnx-user`, and `score-qnx-password`
secret names remain supported for compatibility. New callers should use the
`qnx-*` names.

### Variants and multiple warmup jobs

`variants` is a newline-separated list of argument groups passed to `bazel
fetch`. Include every platform or configuration whose external repositories must
be available. A line containing only `//...` is valid. Do not place a shell
command in this input.

Add one `warmup-*` job for every build configuration that should populate a disk
cache. Each warmup job must depend on `repository_cache_maintenance`, and the
final prune job must list all warmup jobs in `needs`.

### Pull requests, merge queues, and manual runs

The reusable workflow treats these events as dry runs: it validates and fetches
the configured variants, but does not write shared caches. The example's warmup
and final-prune jobs intentionally run only for a `push`. Do not invoke
credentialed build workflows for untrusted PR code unless their own security
model permits it.

### Operational checklist

- Run `bazel mod tidy` when changing module dependencies, and commit the
  resulting `MODULE.bazel.lock`.
- Include every required Bazel configuration in `variants`.
- Use stable, distinct `unique-cache-name` values for each cache-producing job.
- Do not run an independent cache-pruning job in parallel with maintenance.
- Keep the final prune job after all warmup jobs; it needs `actions: write`.
- Update the pinned action and workflow SHAs together when adopting a newer
  caching implementation.

## Background: cache lifecycle

The workflow manages two cache types:

| Cache            | Purpose                                       | Refresh rule                                                                                    |
| ---------------- | --------------------------------------------- | ----------------------------------------------------------------------------------------------- |
| Repository cache | Downloaded external repositories and archives | Rebuild when `MODULE.bazel.lock` changes                                                        |
| Disk cache       | Local Bazel action outputs                    | Add outputs on normal `main` builds; delete it after a lockfile-driven repository-cache refresh |

All Bazel jobs restore these caches through
`eclipse-score/cicd-actions/setup-bazel-cache`. Each job or target configuration
needs a stable, unique cache name so disk caches do not collide.

On a push to `main`, the reusable workflow compares `MODULE.bazel.lock` with the
previous commit. If it changed, it constructs and uploads a fresh repository
cache, then deletes stale disk caches only after that upload is complete. The
subsequent warmup jobs repopulate their disk caches, and the final prune job
removes obsolete cache entries.

If the lockfile did not change, the repository cache is left intact and warmup
jobs can add new action outputs to their own disk caches. This avoids a cache
gap and unnecessary dependency downloads.
