<!-- ----------------------------------------------------------------------------
  Copyright (c) 2026 Contributors to the Eclipse Foundation

  See the NOTICE file(s) distributed with this work for additional
  information regarding copyright ownership.

  This program and the accompanying materials are made available under the
  terms of the Apache License Version 2.0 which is available at
  https://www.apache.org/licenses/LICENSE-2.0

  SPDX-License-Identifier: Apache-2.0
----------------------------------------------------------------------------- -->

# Merge queue guidance for maintainers

Use this guide to configure automatic checks before merging changes.
This guidance is optional. Maintainers can choose another configuration for their repository.

* To choose a different setup, [compare alternatives](merge_queue_alternatives.md).
* To inspect existing setups, see the [repository status quo](merge_queue_status_quo.md).

## Recommended setup

Use GitHub's native merge queue, with labels to request early expensive checks:

| Checks | On a PR | In the merge queue |
| --- | --- | --- |
| Fast checks | Run automatically | Always run |
| Expensive checks, such as QNX builds | Run when enabled by a label | Always run |

Require stable collector checks that aggregate the results needed for merging, one per validation workflow.

The queue validates the combined state of the latest default branch, the PR, and PRs ahead of it.
Optional early checks let contributors investigate expensive-check failures before queueing again.
Labels only control PR checks; the corresponding checks always run in the queue.

## Setup checklist

1. [Configure workflow triggers and optional checks](#configure-workflow-triggers-and-optional-checks)
2. [Aggregate results in collector checks](#aggregate-results-in-collector-checks)
3. [Require the queue and collectors in branch protection](#require-the-queue-and-collectors-in-branch-protection)
4. [Verify merge-blocking behavior](#verify-merge-blocking-behavior)
5. [Document labels and failure recovery](#document-labels-and-failure-recovery)

## Configure workflow triggers and optional checks

1. Identify the checks that should block a merge, including build, test, and any necessary expensive checks.
   A workflow running on PRs or merge groups does not by itself make its result mandatory.
2. Trigger validation workflows on `merge_group` with `checks_requested` in addition to the appropriate PR event.
   Check out and validate the merge-group revision, rather than the PR head, in queue runs.
3. For optional early checks, include `labeled` and `unlabeled` PR events so label changes update the checks.
   Select these jobs by PR label, but always select them for merge-group events.
4. Handle access to secrets explicitly.
   Labels do not replace any necessary environment approval for fork PRs.

For QNX checks, follow the [shared Bazel workflow guide](../.github/workflows/bazel.md#run-qnx-checks-on-pull-requests),
which covers fork approval, label selection and queue triggers.

## Aggregate results in collector checks

Use a collector in each validation workflow. With separate PR and PR-target workflows, configure two collectors:

| Workflow | Example collector | Aggregates |
| --- | --- | --- |
| `pull_request` | `PR mergeable` | Ordinary PR checks, including fast checks. |
| `pull_request_target` | `QNX mergeable` | Secret-dependent checks and any required fork approval. |

Trigger both workflows on `merge_group` as well, so each collector validates its part of the queued revision.
Give the collectors distinct names and keep each name stable across that workflow's PR and queue runs.

Make each collector depend on all validation jobs in its workflow and evaluate their results.
Keep these dependency lists in the repository, so adding or renaming a job does not also require an organization-configuration change.

Configure each collector to:

* Use job-level `if: ${{ always() }}` to evaluate results even when dependencies fail, are skipped or are cancelled.
* Reject failures and cancellations, including cancellation of the workflow itself.
* Accept skipped jobs only when they are intentionally optional for that event.
* Require expensive checks to run successfully in the queue, even when they were optional on the PR.

Keep the collector limited to status evaluation, with a short timeout and no checkout or build steps.
`always()` allows it to run after cancellation; the result checks must still explicitly fail on cancellation.
For build jobs, use `if: ${{ !cancelled() }}` together with their selection or approval conditions instead:
this also allows failed or skipped dependencies, but respects workflow cancellation.
See [GitHub's explanation of `always()` and its warning for tasks such as checkout](https://docs.github.com/en/actions/reference/workflows-and-actions/expressions#always).

Two kinds of skipping need particular care:

* **Workflow filters:** event or path filters can skip an entire required workflow, leaving its check pending and blocking a merge.
* **Job conditions:** GitHub treats conditionally skipped jobs as successful. Listing a job as required does not ensure that it runs; account for intentional skips in the collector.

See [GitHub's guidance on skipped required checks](https://docs.github.com/en/pull-requests/how-tos/merge-and-close-pull-requests/troubleshooting-required-status-checks#handling-skipped-but-required-checks).

## Require the queue and collectors in branch protection

In [.eclipsefdn's Otterdog configuration](https://github.com/eclipse-score/.eclipsefdn/blob/main/otterdog/eclipse-score.jsonnet):

* Configure `required_merge_queue` for the repository's default branch.
* Add both collectors to `required_status_checks` using their reported check names.
* Review existing legacy branch protections as well as rulesets.
  Prefer the queue over an additional strict up-to-date requirement: the queue already validates against the latest base branch.

A queue without required validation checks does not enforce build or test results.
Likewise, a collector that exists in a workflow but is absent from the required-check list does not block a merge.

## Verify merge-blocking behavior

Before relying on the setup, verify that:

* A successful PR can enter the queue and merge after a successful queue run.
* Required failures and cancellations block merging.
* Optional PR checks are skipped as intended.
* Expensive checks run in the queue for fork PRs without labels.

GitHub's [merge queue documentation](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/configuring-pull-request-merges/managing-a-merge-queue)
describes the event and required-check configuration.

## Document labels and failure recovery

Document the two actions PR authors need:

* **Request early checks:** list the available labels and explain how to ask a maintainer to apply them when the author lacks permission.
* **Recover from a queue failure:** investigate the failure, test the fix on the PR, and queue the PR again to validate the latest combined state.

Early PR checks do not replace the final queue run.

Maintainers remain responsible for ongoing CI maintenance and for repairing a broken default branch.
Merge queues reduce the risk of integration failures, but do not fix flaky SDK downloads or tests;
caching and reliability improvements remain separate work.

## Further reading

* [Compare alternatives](merge_queue_alternatives.md) to choose a configuration for your repository.
* [Explore current repository setups](merge_queue_status_quo.md) to see how the features are combined.
