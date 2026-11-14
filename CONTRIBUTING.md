# Contributing

Changes to reusable workflows affect the repositories that call them. The
[consumer compatibility guide](tests/ci/README.md) explains how we test candidate
workflow changes against pinned SCORE repositories, add consumers and maintain
their compatibility contracts and commit pins.

Python tooling is managed by the root `pyproject.toml` and `uv.lock`. Run
`uv run --locked python -m pytest` for helper tests; the guide includes setup,
linting and dependency update instructions.
