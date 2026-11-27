# Renovate component updates

Dependabot continues to update ordinary GitHub Actions such as `actions/checkout`.
The hourly Renovate job additionally updates **SHA pins with a component release
comment**, for example:

```yaml
# Before
uses: eclipse-score/cicd-actions/setup-bazel-cache@af1be61755b13c1f33b6c250080dc6ac025012f1 # setup-bazel-cache/v0.0.1
# After: both the commit and component version change together.
uses: eclipse-score/cicd-actions/setup-bazel-cache@e2a2a587a759b681632040ec1e1294015a0405f5 # setup-bazel-cache/v0.1.0
```

The example illustrates the reference format. Renovate resolves the actual
commit from the published tag. It looks up tags in the referenced repository and
only considers versions with the same component prefix, such as
`setup-bazel-cache/`. It also supports SHA-pinned reusable workflow calls with
component version comments. Workflow files and `action.yml`/`action.yaml`
definitions are scanned, including quoted `uses:` values.

| Reference | Renovate behavior |
| --- | --- |
| `owner/repo/action@SHA # component/v1.2.3` | Updates SHA and comment to a newer release of that component |
| `owner/repo/.github/workflows/build.yml@SHA # build/v1.2.3` | Same behavior for reusable workflows |
| `actions/checkout@v4` or `@SHA # v4.1.0` | Left to Dependabot |
| `owner/repo/action@component/v1.2.3` | Unchanged: this rollout only handles existing SHA pins |
| `owner/repo/action@main`, `@SHA` or `@SHA # main` | Unchanged: no component version comment |

`SHA` denotes a full 40-character commit hash. Component versions must have
major, minor and patch numbers. Minor/patch updates are grouped; major updates
get separate PRs. All require manual review. Moving a tag without changing its
version does not produce a standalone SHA refresh.

## Opt in your repository

Opt-in requires changes in two repositories: your consumer repository and this
central workflow repository. No consumer-local Renovate configuration is needed.

1. **In your repository**, use full SHA pins with component version comments,
   such as `@<40-character SHA> # setup-bazel-cache/v0.1.0`. The comment must name
   a published component tag; references such as `@cache` are not migrated
   automatically.
2. **In your repository**, keep Dependabot's `github-actions` entry and add the
   ignore rules below under that entry. Merge these exclusions before activating
   Renovate, so both bots do not update the same dependency.
3. **In this repository**, submit a PR adding your consumer repository, for
   example `eclipse-score/my-module`, to the component rule's `matchRepositories`
   list in [the central configuration](.github/renovate.json5). Also add it to the
   top-level `repositories` catalog if it is not already there. This registers
   the repository that **uses** the component, not the repository publishing it.
4. After the central PR is merged, the next hourly Renovate run checks your
   component pins. Eligible newer releases produce reviewable update PRs.

For components from `eclipse-score/cicd-actions` and reusable workflows from
`eclipse-score/cicd-workflows`, the consumer's Dependabot configuration should
include:

```yaml
version: 2
updates:
  - package-ecosystem: github-actions
    directory: /
    schedule:
      interval: weekly
    ignore:
      - dependency-name: "eclipse-score/cicd-actions*"
      - dependency-name: "eclipse-score/cicd-workflows*"
```

Add `ignore` to your existing entry; retain its schedule, groups and other options.
The trailing `*` matches both the bare repository name and names with an action
or workflow path, such as
`eclipse-score/cicd-workflows/.github/workflows/on-pr.yml`. One entry per source
repository therefore covers both Dependabot name forms. These prefix patterns
also match repositories whose names extend the same prefix.

Only exclude source repositories you want Renovate to manage. These exclusions
apply to all their references; Dependabot cannot select by version-comment
format. Other Actions remain with Dependabot. When adding components from another
source repository, add corresponding ignore entries in each consumer.

The initial consumers are `eclipse-score/cicd-workflows` and
`eclipse-score/docs-as-code`. Existing Bazel module updates continue independently.
Consumer-local Renovate configuration still applies unless the catalog entry
sets `requireConfig: "ignored"`; `cicd-workflows` uses this because it hosts the
central configuration itself. If you already have local Renovate configuration,
ensure it permits the central custom manager and component rules.
