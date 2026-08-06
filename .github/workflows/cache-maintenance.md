# Bazel cache maintenance

`cache-maintenance.yml` keeps the GitHub Actions Bazel caches useful without
allowing normal CI jobs to race while saving them. It is intended for Bazel
repositories that use `MODULE.bazel.lock`.

The workflow manages two cache types:

| Cache | Purpose | Refresh rule |
| --- | --- | --- |
| Repository cache | Downloaded external repositories and archives | Rebuild when `MODULE.bazel.lock` changes |
| Disk cache | Local Bazel action outputs | Add outputs on normal `main` builds; delete it after a lockfile-driven repository-cache refresh |

All other Bazel jobs restore these caches through
`eclipse-score/cicd-actions/setup-bazel-cache`. Give every distinct job or
target configuration a stable, unique cache name so their disk caches do not
collide.

The caller orchestrates the lifecycle: maintenance runs first, cache-warming
builds run only after it, and a final prune runs after all warming jobs have
finished. Pull requests, merge queues, and manual runs execute only the
maintenance job in dry-run mode; shared caches remain unchanged.

## Prerequisites

- The repository has `MODULE.bazel.lock` committed and up to date.
- The repository can fetch every dependency needed by the configured variants.
- Jobs that modify or delete caches have `actions: write` and `contents: read`.
- For QNX variants, make the QNX license, user, and password available as
  repository secrets. They are optional when no configured variant needs QNX.
- If any configured variant fetches dependencies from private GitHub
  repositories, provide either a GitHub App client ID and private key, or a
  token with read access to those repositories.

## 1. Cache each Bazel job

Replace direct use of `bazel-contrib/setup-bazel` (or another cache setup
action) in every Bazel job with the S-CORE cache action:

```yaml
- name: Setup Bazel with shared caching
  uses: eclipse-score/cicd-actions/setup-bazel-cache@<actions-sha>
  with:
    unique-cache-name: ${{ github.job }}-<variant>
```

Use a suffix such as `-qnx-x86_64` when one workflow can build more than one
configuration. Keep the name stable between runs. The action restores the
repository cache and the job-specific disk cache, then saves eligible cache
updates in its post-step.

## 2. Make main-branch build workflows callable

The cache-maintenance workflow must run before jobs that populate caches on
`main`. Move the `push` trigger from those workflows to `workflow_call`; retain
their PR and manual triggers if they are still wanted.

```yaml
on:
  pull_request:
    types: [opened, synchronize, reopened]
  workflow_dispatch:
  workflow_call:
```

For a workflow called from another workflow, pass inherited secrets:

```yaml
jobs:
  build:
    secrets: inherit
    uses: ./.github/workflows/build.yml
```

## 3. Add the orchestration workflow

Create `.github/workflows/cache-maintenance.yml` in the consuming repository.
The example below checks the repository cache first, then runs the cache-warming
build, and only then prunes obsolete caches. Pin both reusable workflows and
actions to reviewed commit SHAs.

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
      gh_app_private_key: ${{ secrets.ETAS_ENG_SCORE_BOT_PRIVATE_KEY }}
    uses: eclipse-score/cicd-workflows/.github/workflows/cache-maintenance.yml@<workflows-sha>
    with:
      gh_app_client_id: ${{ vars.ETAS_ENG_SCORE_BOT_APP_ID }}
      # Each line becomes one `bazel fetch` invocation.
      variants: |
        //...
        --config=target_config_1 //...

  precache-qnx-x86_64:
    needs: repository_cache_maintenance
    # PR, merge-queue, and manual runs validate variants above but never warm
    # or write shared caches.
    if: ${{ github.event_name == 'push' }}
    secrets: inherit
    uses: ./.github/workflows/build_qnx_x86_64.yml

  delete_old_caches:
    needs: precache-qnx-x86_64
    if: ${{ !cancelled() && github.event_name == 'push' }}
    runs-on: ubuntu-24.04
    permissions:
      # Prune only after every warmup job has completed its cache-save post-step.
      actions: write
      contents: read
    steps:
      - name: Prune obsolete Bazel caches
        uses: eclipse-score/cicd-actions/prune-cache@<actions-sha>
```

The QNX and GitHub App values above are optional, but must be supplied when a
configured variant needs them. To use a token instead of a GitHub App, pass it
as the `token` secret and omit `gh_app_client_id` and `gh_app_private_key`.
The older `score-qnx-license`, `score-qnx-user`, and `score-qnx-password`
secret names remain supported for compatibility, but new callers should use the
`qnx-*` names.

`variants` is a newline-separated list of argument groups passed to `bazel
fetch`. Include every platform or configuration whose external repositories must
be available. A line containing only `//...` is valid. Do not place a shell
command in this input.

Add one `precache-*` job for each build configuration that should warm its disk
cache. Each must depend on `repository_cache_maintenance`; add each one to the
prune job's `needs` list.

## How it behaves

On a push to `main`, the reusable workflow compares `MODULE.bazel.lock` with the
previous commit. If it changed, it constructs and uploads a fresh repository
cache, then deletes stale disk caches only after that upload is complete. The
subsequent precache jobs repopulate their disk caches, and the final prune job
removes obsolete cache entries.

If the lockfile did not change, the repository cache is left intact and the
precache jobs can add new action outputs to their own disk caches. This avoids a
cache gap and unnecessary dependency downloads.

Manual dispatch is useful to exercise cache-maintenance checks. The example's
precache and final-prune jobs intentionally run only for a `push`, so manual
runs do not alter the shared caches beyond the reusable workflow's own event
rules.

## Pull requests and merge queues

To validate the `variants` input before it reaches `main`, add `pull_request`
and `merge_group` triggers to the orchestration workflow. The reusable workflow
treats these as dry runs: it validates/fetches using the configured variants but
does not write shared caches. Do not call credentialed build workflows from
untrusted PR code unless their own security model explicitly permits it.

```yaml
on:
  pull_request:
    types: [opened, reopened, synchronize, labeled, unlabeled]
  merge_group:
    types: [checks_requested]
  workflow_dispatch:
  push:
    branches: [main]
```

## Operational checklist

- Run `bazel mod tidy` when changing module dependencies, and commit the
  resulting `MODULE.bazel.lock`.
- Include every required Bazel configuration in `variants`.
- Use stable, distinct `unique-cache-name` values for each cache-producing job.
- Do not run an independent cache-pruning job in parallel with maintenance.
- Keep the final prune job after all precache jobs; it needs `actions: write`.
- Update the pinned action and workflow SHAs together when adopting a newer
  caching implementation.
