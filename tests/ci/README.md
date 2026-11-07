# Consumer compatibility tests

Changes to [`on-pr.yml`](../../.github/workflows/on-pr.yml) are tested against pinned real SCORE repositories in
[the consumer catalog](../../.github/ci/consumers.yml). The suite calls the workflow from
the candidate PR, checks out each catalog commit and uses the existing default
checks. The complete workflow must succeed, and every entry in `required-checks`
must report successful execution on the pinned checkout. The matrix reads check
input defaults from the candidate `on-pr.yml` and then applies documented catalog
overrides, so changes to public defaults are exercised by the consumers. Additional checks may
run or be skipped without changing this minimum contract.

The catalog covers application modules, C++ foundations, documentation and
coverage tooling, Python tools, registry maintenance and GitHub Actions. These
profiles exercise both standard root targets and documented exceptions, as well
as repositories with and without Bazel or standalone Python tests.

Add consumers as entries in the shared catalog:

```yaml
- name: module-template
  repository: eclipse-score/module_template
  ref: <full-commit-sha>
  required-checks: [pre-commit, format, copyright, module-tidy, lockfile]
```

`required-checks` is a tested minimum compatibility profile, not an exhaustive
assessment of a repository. An omitted check does not imply a missing capability.
`require-lock` is an error guard that fails only when a required lockfile is
missing. It remains in reported outcomes but cannot be a required successful
check in the catalog.
Review changes to minimum requirements separately from routine pin updates so
that a failing consumer cannot silently weaken its compatibility contract.

Repository-specific exceptions belong in the same catalog. Optional `overrides`
accepts `format-target`, `copyright-target` and the existing `skip-*` inputs;
every override requires a `value` and a nonempty `reason`. For example:

```yaml
overrides:
  format-target:
    value: //tools:format.check
    reason: The repository exposes its formatter only from //tools.
```

Explicit skip switches cannot disable a required check. Leave `overrides` absent
for a standard call. These exceptions make differences visible without requiring
separate consumer files or scripts.

The suite needs no consumer-specific files, write permissions or secrets. The
workflow repository's `CI success` job covers its own common checks and all
consumer scenarios.

## Daily snapshot refresh

The `Update consumer pins` workflow runs daily at 04:43 UTC and can also be
started manually on `main`. It resolves each consumer's default branch once,
changes only commit pins and runs the same consumer scenarios against that exact
candidate catalog. Required checks and documented exceptions stay unchanged.

When every consumer succeeds, the workflow commits the catalog and attempts a
normal push to `main`. A concurrent change on `main` or branch protection rejects
the push; it is never forced. Failed consumer checks or a rejected push instead
create or update one PR on `bot/consumer-pin-update` for review. An API failure
leaves the catalog unchanged, and a run without new commits publishes nothing.
The fallback branch retains the tested base and changes only pins. Concurrent
catalog edits can therefore cause a visible merge conflict that needs manual
resolution; they are never resolved automatically in favor of the older catalog.
Partial workflow reruns download the exact artifact produced by preparation,
even when that job succeeded in an earlier attempt.

Publishing reuses `SCORE_BOT_CLASSIC_PAT`, already used by Renovate. Consumer
checks run separately with read-only permissions and receive no bot credential.
The fallback PR triggers the normal PR suite and is not merged automatically.

## Local verification

The repository's [Python project](../../pyproject.toml) declares PyYAML and the
pytest/Ruff development tools. `uv.lock` pins their resolved versions, and
`.python-version` selects Python 3.12 for local runs and GitHub Actions. This is
repository tooling: no Python package is built or published.

Run the helper tests and lint checks from the repository root:

```sh
uv sync --locked
uv run --locked python -m pytest -v
uv run --locked ruff check tests/ci
```

All workflow helper calls use the same locked project environment. `--locked`
rejects dependency changes without a matching lockfile update. After deliberately
changing dependencies, run `uv lock` and commit `pyproject.toml` and `uv.lock`
together. The shared `on-pr` workflow also discovers and runs these tests through
the root `pyproject.toml` and `uv.lock`.

These tests cover catalog validation, outcome assertions, pin edits and the
publishing handoff. They do not execute the consumers' checks. The
[consumer suite](../../.github/workflows/_test_consumers.yml) runs those
checks in GitHub Actions using the candidate workflow implementation.

The internal report collection action also has real-artifact self-tests in
[the report self-test workflow](../../.github/workflows/_test_merge_test_reports.yml).
They upload fixtures and invoke the candidate action for single and multiple
artifacts, identical files, conflicts and missing artifacts. The PR workflow's
`CI success` gate requires these self-tests as well as consumer compatibility.
