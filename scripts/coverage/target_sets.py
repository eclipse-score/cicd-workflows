#!/usr/bin/env python3
"""Derive Bazel target and source sets for C++ coverage."""

from __future__ import annotations

import argparse
import pathlib
import re
from dataclasses import dataclass

from scripts.coverage.common import ensure_dir, run_cmd, write_lines

CPP_EXTENSIONS = (".c", ".cc", ".cpp", ".cxx")


@dataclass(frozen=True)
class TargetSets:
    """Typed carrier for all computed sets."""

    functional_targets: list[str]
    test_targets: list[str]
    functional_sources: list[str]


def _query_labels(query: str) -> list[str]:
    result = run_cmd(["bazel", "query", query])
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _normalize_source_label(label: str) -> str | None:
    """Convert source labels like //score/foo:bar.cpp to score/foo/bar.cpp."""

    if not label.startswith("//score/"):
        return None
    if label.startswith("@"):
        return None

    match = re.fullmatch(r"//([^:]+):(.+)", label)
    if not match:
        return None

    package_path, target_name = match.group(1), match.group(2)
    if not target_name.endswith(CPP_EXTENSIONS):
        return None

    return f"{package_path}/{target_name}"


def compute_target_sets(scope: str) -> TargetSets:
    """Compute functional targets, test targets, and expected source files."""

    functional_query = (
        f'kind("cc_library", {scope}) union '
        f'kind("cc_binary", {scope})'
    )
    tests_query = f'kind("cc_test", {scope}) except attr("tags", "no-coverage", {scope})'
    srcs_query = f'labels(srcs, ({functional_query}))'

    functional_targets = sorted(set(_query_labels(functional_query)))
    test_targets = sorted(set(_query_labels(tests_query)))

    source_labels = _query_labels(srcs_query)
    functional_sources = sorted(
        set(
            normalized
            for normalized in (_normalize_source_label(label) for label in source_labels)
            if normalized is not None
        )
    )

    return TargetSets(
        functional_targets=functional_targets,
        test_targets=test_targets,
        functional_sources=functional_sources,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate Bazel target sets for full C++ coverage.")
    parser.add_argument("--scope", default="//score/...", help="Bazel scope for coverage (default: //score/...)")
    parser.add_argument(
        "--out-dir",
        default="bazel-out/coverage",
        help="Output directory for generated target sets",
    )
    args = parser.parse_args()

    out_dir = pathlib.Path(args.out_dir)
    ensure_dir(out_dir)

    target_sets = compute_target_sets(args.scope)
    write_lines(out_dir / "functional_targets.txt", target_sets.functional_targets)
    write_lines(out_dir / "test_targets.txt", target_sets.test_targets)
    write_lines(out_dir / "expected_sources.txt", target_sets.functional_sources)

    print(f"Wrote {len(target_sets.functional_targets)} functional targets")
    print(f"Wrote {len(target_sets.test_targets)} test targets")
    print(f"Wrote {len(target_sets.functional_sources)} expected functional source files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
