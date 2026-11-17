# Monorepo releases

For maintainers of repositories with independently versioned components.
Check which components have changed before preparing their next release:

```sh
uv run --locked monorepo-releases
```

The check lists commits on your current branch since each component's highest
`component/vMAJOR.MINOR.PATCH` tag. Refresh local tags first if needed. No tag
means a first release is needed; relevant commits mean a release needs review;
otherwise no new release is needed. It does not create releases.
Uncommitted changes and untracked files produce repository-wide warnings.
Tags are expected to have equally named GitHub Releases; only tags are checked.

`chore`, `test` and `ci` Conventional Commits, including scoped forms such as
`chore(deps):`, count as maintenance. Explicit breaking changes (`chore!:`) remain
relevant. `uv run --locked monorepo-releases --no-filter` also displays maintenance
commits, without changing the release assessment.

[The workflow](.github/workflows/monorepo-releases.yml) runs the same command on
`main` pushes and all PRs against `main`.
Results appear in the run's **Summary** tab, with collapsible commit lists.
Trailing PR references such as `(#123)` link to the PR in that repository.

## Set up another repository

Install a pinned tool revision with uv, replacing `<SHA>` with its commit:

```sh
uv add 'monorepo-releases @ git+https://github.com/eclipse-score/cicd-workflows@<SHA>#subdirectory=tools/monorepo-releases'
```

Commit `pyproject.toml` and `uv.lock`. Copy
[`.github/components.yml`](.github/components.yml) and adapt it to your components.

Run the same command from your checkout. For CI, copy
[the workflow](.github/workflows/monorepo-releases.yml), keeping checkout's
`fetch-depth: 0` so every component tag and its history are available.
