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
    unique-cache-name: ${{ github.job }}[-matrix-job-name]
```

Add an optional stable suffix for matrix values or target configurations when a
job name alone is not unique.

### 2. Make cache-warming workflows callable

For every workflow that should warm a Bazel cache after a push, remove its
`push` trigger and add `workflow_call`. Retain any pull-request or manual
triggers that are useful for the workflow. The cache-maintenance workflow then
invokes it after repository-cache maintenance has finished.

Workflow triggered only by the cache-writing push — before:

```yaml
on:
  push:
    branches: [main]
```

Workflow triggered only by the cache-writing push — after:

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
  # Rebuild caches manually, for example after a transient download failure or for workflow testing.
  workflow_dispatch:
  # Select the branch whose pushes may warm and prune shared caches.
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
    uses: eclipse-score/cicd-workflows/.github/workflows/cache-maintenance.yml@<workflows-sha>
    with:
      # Each line becomes one `bazel fetch` invocation.
      variants: |
        //...
        --config=target_config_1 //...

  warmup-qnx-x86_64:
    needs: repository_cache_maintenance
    # PR and merge-queue runs validate variants above but never warm or write
    # shared caches.
    if: ${{ github.event_name == 'push' || github.event_name == 'workflow_dispatch' }}
    secrets: inherit
    uses: ./.github/workflows/build_qnx_x86_64.yml

  delete_old_caches:
    needs: warmup-qnx-x86_64
    if: ${{ !cancelled() && (github.event_name == 'push' || github.event_name == 'workflow_dispatch') }}
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

For a private S-CORE derivative that needs private GitHub dependencies, add the
GitHub App credentials to its calling job:

```yaml
    secrets:
      github-app-private-key: ${{ secrets.PRIVATE_DEPENDENCY_APP_PRIVATE_KEY }}
    with:
      github-app-client-id: ${{ vars.PRIVATE_DEPENDENCY_APP_ID }}
```

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

### Pull requests and merge queues

The reusable workflow treats pull requests and merge queues as dry runs: it
validates and fetches the configured variants, but does not write shared caches.
Pushes and manual dispatches rebuild and prune caches when `MODULE.bazel.lock`
has changed. The caller's push trigger and the ref selected for a manual
dispatch determine the cache-writing branch.

GitHub's secret handling depends on the PR model: public repositories do not
receive repository secrets for PRs from forks, whereas private repositories and
same-repository branch PRs can make secrets available. This workflow
nevertheless deliberately does not configure GitHub App credentials or tokens
for `pull_request` or `pull_request_target` runs, because they execute code
from the proposed change.

Repositories that use a trusted, branch-based PR model may choose a different
credential policy. With the default policy, validate private dependencies in a
trusted event such as a merge-queue run or a manually dispatched run.

### Operational checklist

- Run `bazel mod tidy` when changing module dependencies, and commit the
  resulting `MODULE.bazel.lock`.
- Include the Bazel configurations needed for the desired cache coverage in
  `variants`; omitting rarely used configurations keeps the repository cache
  smaller and faster to restore.
- Use stable, distinct `unique-cache-name` values for each cache-producing job.
- Do not run an independent cache-pruning job in parallel with maintenance.
- Keep the final prune job after all warmup jobs; it needs `actions: write`.
- Update the pinned action and workflow SHAs together when adopting a newer
  caching implementation.

## Background: cache lifecycle

The workflow manages two cache types:

| Cache            | Purpose                                       | Refresh rule                                                                                                   |
| ---------------- | --------------------------------------------- | -------------------------------------------------------------------------------------------------------------- |
| Repository cache | Downloaded external repositories and archives | Rebuild when `MODULE.bazel.lock` changes                                                                       |
| Disk cache       | Local Bazel action outputs                    | Add outputs on cache-writing pushes or manual runs; delete it after a lockfile-driven repository-cache refresh |

All Bazel jobs restore these caches through
`eclipse-score/cicd-actions/setup-bazel-cache`. Each job or target configuration
needs a stable, unique cache name so disk caches do not collide.

On a cache-writing push or manual dispatch, the reusable workflow compares
`MODULE.bazel.lock` with the previous commit. If it changed, it constructs and
uploads a fresh repository cache, then deletes stale disk caches only after that
upload is complete. The subsequent warmup jobs repopulate their disk caches,
and the final prune job removes obsolete cache entries.

If the lockfile did not change, the repository cache is left intact and warmup
jobs can add new action outputs to their own disk caches. This avoids a cache
gap and unnecessary dependency downloads.
