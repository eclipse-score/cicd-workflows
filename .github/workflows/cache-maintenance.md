# Bazel cache maintenance

`cache-maintenance.yml` maintains the shared Bazel caches of a repository that
uses `MODULE.bazel.lock`. Before integrating it, commit an up-to-date lockfile.

## How caching works

Every Bazel job must use the S-CORE cache action instead of using
`bazel-contrib/setup-bazel` directly:

```yaml
# Add this step after checkout in every Bazel job.
- name: Setup Bazel with shared caching
  # Replace `<actions-sha>` with a reviewed cicd-actions commit SHA.
  uses: eclipse-score/cicd-actions/setup-bazel-cache@<actions-sha>
  with:
    # This name must be stable and unique for the produced Bazel outputs.
    unique-cache-name: ${{ github.job }}
    # For a matrix job, append its stable identifying values, for example:
    # unique-cache-name: ${{ github.job }}-${{ matrix.name }}
    # Uncomment this when the cache-writing branch is not named `main`:
    # main-branch: <default-branch>
```

Use a stable suffix for matrix values or target configurations when a job name
alone is not unique. The action restores caches in every job, but saves them
only when the current ref is `main` by default. Set `main-branch` if the
repository uses a different default branch.

There are two ways to combine the action with cache maintenance:

| Mode             | Use when                                                                      | Tradeoff                                                                                            |
| ---------------- | ----------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------- |
| Automatic warmup | Existing Bazel workflows already run on every push to the default branch      | Simplest integration, but every job may download the repository-cache delta after a lockfile change |
| Ordered warmup   | Avoiding redundant dependency downloads is worth the additional orchestration | Maintenance downloads the delta once, but build workflows must be reusable and wait for maintenance |

Both modes rebuild the caches after a lockfile change. Automatic warmup is the
simpler default. Ordered warmup remains useful when dependency downloads are
expensive or unreliable and should be performed only once.

## Option 1: Automatic warmup

Keep the `push` triggers of existing Bazel workflows. On a push to `main`,
`setup-bazel-cache` automatically saves the repository cache and each job's
disk cache. Add a separate workflow that calls only the maintenance workflow:

```yaml
name: Cache maintenance

on:
  # Validate every `variants` entry without writing caches before merge.
  pull_request:
    types: [opened, reopened, synchronize]
  # Perform the same read-only validation for merge-queue commits.
  merge_group:
    types: [checks_requested]
  # Allow a repository-cache rebuild after a transient failure or for testing.
  workflow_dispatch:
  # Refresh and prune shared caches after every push to the writing branch.
  push:
    # Replace `main` when the repository uses a different writing branch.
    branches: [main]

jobs:
  repository_cache_maintenance:
    permissions:
      # Required to delete invalidated and superseded cache generations.
      actions: write
      # Required to check out the repository and inspect its lockfile.
      contents: read
    secrets:
      # Remove these optional secrets when no configured variant uses QNX.
      qnx-license: ${{ secrets.SCORE_QNX_LICENSE }}
      qnx-user: ${{ secrets.SCORE_QNX_USER }}
      qnx-password: ${{ secrets.SCORE_QNX_PASSWORD }}
    # Replace `<workflows-sha>` with a reviewed cicd-workflows commit SHA.
    uses: eclipse-score/cicd-workflows/.github/workflows/cache-maintenance.yml@<workflows-sha>
    with:
      # Each non-empty line is appended to one `bazel fetch` invocation.
      # Replace the example configuration with those used by this repository.
      variants: |
        //...
        --config=target_config_1 //...
```

The reusable workflow already prunes obsolete cache generations, so this mode
does not need a separate prune job.

Automatic warmup is not yet optimized for the smallest possible cache size.
Optimizations will follow.

When the lockfile changes, its new hash selects a new repository-cache
generation. Each independently starting Bazel job restores the previous
generation, downloads the delta it needs, and automatically saves caches at the
end of the job. The caches are therefore rebuilt without explicit warmup
orchestration. However, jobs running in parallel cannot yet restore the new
generation, so they may download the same repository-cache delta redundantly.

Use this mode when the simpler workflow structure is more valuable than
avoiding those redundant downloads. Every cacheable Bazel workflow must run on
every push to `main`; otherwise, its job-specific disk cache is never updated.
Pull-request-only workflows can restore caches, but cannot update them.

## Option 2: Ordered warmup

Use a single orchestration workflow to run repository-cache maintenance first,
selected builds second, and final pruning last. Maintenance downloads the
repository-cache delta once and publishes the new generation. The subsequent
build jobs restore that generation instead of downloading the delta in every
job, while also warming their individual disk caches.

### 1. Make cache-warming workflows callable

For every workflow that should warm a disk cache, replace its `push` trigger
with `workflow_call`. Retain pull-request or manual triggers that are useful.
The orchestration workflow becomes the only entry point for its default-branch
pushes.

Before:

```yaml
# The cacheable build workflow currently runs directly after each main push.
on:
  push:
    branches: [main]
```

After:

```yaml
# The orchestration workflow now calls this build workflow after maintenance.
on:
  workflow_call:
```

A workflow that also runs for pull requests can retain that trigger:

```yaml
on:
  # Keep the workflow's existing pull-request checks.
  pull_request:
    types: [opened, synchronize, reopened]
  # Let the cache-maintenance orchestrator call the same workflow on `main`.
  workflow_call:
```

### 2. Add the orchestration workflow

Create `.github/workflows/cache-maintenance.yml` in the consuming repository.
Pin reusable workflows and actions to reviewed commit SHAs.

```yaml
name: Cache maintenance

on:
  # Validate every `variants` entry without writing caches before merge.
  pull_request:
    types: [opened, reopened, synchronize]
  # Perform the same read-only validation for merge-queue commits.
  merge_group:
    types: [checks_requested]
  # Dispatch from `main` to rebuild both repository and disk caches manually.
  workflow_dispatch:
  # Run the ordered refresh and warmup after every cache-writing push.
  push:
    # Replace `main` when the repository uses a different writing branch.
    branches: [main]

jobs:
  repository_cache_maintenance:
    permissions:
      # Required to delete invalidated and superseded cache generations.
      actions: write
      # Required to check out the repository and inspect its lockfile.
      contents: read
    secrets:
      # Remove these optional secrets when no configured variant uses QNX.
      qnx-license: ${{ secrets.SCORE_QNX_LICENSE }}
      qnx-user: ${{ secrets.SCORE_QNX_USER }}
      qnx-password: ${{ secrets.SCORE_QNX_PASSWORD }}
    # Replace `<workflows-sha>` with a reviewed cicd-workflows commit SHA.
    uses: eclipse-score/cicd-workflows/.github/workflows/cache-maintenance.yml@<workflows-sha>
    with:
      # Fetch all configurations required by the subsequent warmup jobs.
      # Each non-empty line is appended to one `bazel fetch` invocation.
      variants: |
        //...
        --config=target_config_1 //...

  warmup-qnx-x86_64:
    # Do not start until the new repository-cache generation is available.
    needs: repository_cache_maintenance
    # PR and merge-queue runs above are read-only validation runs.
    if: ${{ github.event_name == 'push' || github.event_name == 'workflow_dispatch' }}
    # Forward repository secrets needed by this local reusable build workflow.
    secrets: inherit
    # Replace this path with a local reusable workflow that uses
    # eclipse-score/cicd-actions/setup-bazel-cache in its Bazel jobs.
    uses: ./.github/workflows/build_qnx_x86_64.yml

  delete_old_caches:
    # List every warmup job here when more than one is configured.
    needs:
      - warmup-qnx-x86_64
      # - warmup-other-configuration
    # Run after successful or failed warmups, but never after cancellation and
    # never for the read-only pull-request or merge-queue events.
    if: ${{ !cancelled() && (github.event_name == 'push' || github.event_name == 'workflow_dispatch') }}
    runs-on: ubuntu-24.04
    permissions:
      # Required to delete superseded cache generations.
      actions: write
      # Keep all permissions not needed by this job read-only.
      contents: read
    steps:
      # Run only after every warmup job has completed its cache-save post-step.
      - name: Prune obsolete Bazel caches
        # Replace `<actions-sha>` with a reviewed cicd-actions commit SHA.
        uses: eclipse-score/cicd-actions/prune-cache@<actions-sha>
```

Add one `warmup-*` job for every build configuration that should populate a
disk cache. Each must depend on `repository_cache_maintenance`. The final prune
job must list all warmup jobs in `needs`; it removes older disk-cache
generations created after maintenance performed its own prune.

## Configuration and operations

### Variants

`variants` is a newline-separated list of argument groups passed to `bazel
fetch`. Include every platform or configuration whose external repositories
should be available from the maintained repository cache. A line containing
only `//...` is valid. Do not place a shell command in this input.

Omitting rarely used configurations keeps the repository cache smaller and
faster to restore. In automatic mode, independently running Bazel jobs may each
download the delta needed for their targets. In ordered mode, maintenance
fetches all variants once before the build warmups start.

### Credentials and private dependencies

The QNX credentials are optional, but all three must be supplied when a
configured variant requires QNX. Likewise, provide GitHub App credentials only
when a variant fetches dependencies from private GitHub repositories. To use a
token instead, pass it as the `token` secret and omit
`github-app-client-id` and `github-app-private-key`.

For a private S-CORE derivative that needs private GitHub dependencies, use the
following maintenance job instead of the one in either complete example:

```yaml
jobs:
  repository_cache_maintenance:
    permissions:
      # Required to delete invalidated and superseded cache generations.
      actions: write
      # Required to check out the repository and inspect its lockfile.
      contents: read
    secrets:
      # Store the GitHub App private key as a repository or organization secret.
      github-app-private-key: ${{ secrets.PRIVATE_DEPENDENCY_APP_PRIVATE_KEY }}
    # Replace `<workflows-sha>` with a reviewed cicd-workflows commit SHA.
    uses: eclipse-score/cicd-workflows/.github/workflows/cache-maintenance.yml@<workflows-sha>
    with:
      # A GitHub App client ID is not secret and can be stored as a variable.
      github-app-client-id: ${{ vars.PRIVATE_DEPENDENCY_APP_ID }}
      # Include every target configuration whose dependencies must be cached.
      variants: |
        //...
```

The reusable workflow configures private-dependency credentials whenever the
caller supplies them. Do not pass credentials to workflows that execute
untrusted pull-request code. Public repositories do not receive repository
secrets for pull requests from forks; private repositories and same-repository
branches require an explicit caller-side credential policy.

The older `score-qnx-license`, `score-qnx-user`, and `score-qnx-password`
secret names remain supported for compatibility. New callers should use the
`qnx-*` names.

### Pull requests, merge queues, and manual runs

The reusable workflow treats pull requests and merge queues as dry runs: it
validates and fetches the configured variants, but does not write or delete
shared caches. Pushes and manual dispatches rebuild the repository cache when
`MODULE.bazel.lock` has changed and always prune obsolete cache generations.

The caller's push trigger selects the cache-writing branch. A manually
dispatched maintenance run writes for the selected ref; ordinary Bazel jobs
using the default `setup-bazel-cache` configuration save only on `main`.

## Cache lifecycle

The workflow manages two cache types:

| Cache            | Purpose                                       | Refresh rule                                                                          |
| ---------------- | --------------------------------------------- | ------------------------------------------------------------------------------------- |
| Repository cache | Downloaded external repositories and archives | Rebuild when `MODULE.bazel.lock` changes                                              |
| Disk cache       | Local Bazel action outputs                    | Add outputs on cache-writing pushes; delete all disk caches when the lockfile changes |

All Bazel jobs restore both through
`eclipse-score/cicd-actions/setup-bazel-cache`. Repository caches share a key
derived from the lockfile. Disk caches use the configured
`unique-cache-name`, and cache-writing builds save a new generation after every
run.

When the lockfile changes, both modes create a repository-cache generation for
the new lockfile and repopulate disk caches. Automatic warmup lets each
independently triggered Bazel job download the required repository delta and
save its caches. Ordered warmup has maintenance download the delta once before
the selected build jobs restore the new repository cache and warm their disk
caches. When the lockfile does not change, the repository cache remains intact
and maintenance only prunes obsolete generations.
