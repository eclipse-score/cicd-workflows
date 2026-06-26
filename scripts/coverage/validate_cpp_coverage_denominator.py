#!/usr/bin/env python3
"""Validate that denominator-complete report includes all expected functional sources."""

from __future__ import annotations

import argparse
import pathlib

from scripts.coverage.common import ensure_dir, read_lines, repo_root_from_env, write_lines
from scripts.coverage.lcov_utils import extract_covered_sources


def _load_exclusions(path: pathlib.Path | None) -> set[str]:
    if path is None or not path.exists():
        return set()
    return {
        line
        for line in read_lines(path)
        if not line.lstrip().startswith("#")
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate C++ coverage denominator integrity")
    parser.add_argument("--out-dir", default="bazel-out/coverage", help="Coverage output directory")
    parser.add_argument(
        "--expected-sources",
        default="bazel-out/coverage/expected_sources.txt",
        help="Expected functional source list",
    )
    parser.add_argument(
        "--coverage-info",
        default="bazel-out/coverage/coverage_full.filtered.info",
        help="Merged and filtered LCOV report",
    )
    parser.add_argument(
        "--exclusions-file",
        default="scripts/coverage/coverage_exclusions.txt",
        help="Optional relative source paths to exclude from denominator gate",
    )
    args = parser.parse_args()

    repo_root = repo_root_from_env()
    out_dir = repo_root / args.out_dir
    ensure_dir(out_dir)
    expected_path = repo_root / args.expected_sources
    coverage_path = repo_root / args.coverage_info
    exclusions_path = repo_root / args.exclusions_file

    if not expected_path.exists():
        raise RuntimeError(f"Expected source list not found: {expected_path}")
    if not coverage_path.exists():
        raise RuntimeError(f"Coverage info not found: {coverage_path}")

    expected = set(read_lines(expected_path))
    covered = extract_covered_sources(coverage_path, repo_root)
    exclusions = _load_exclusions(exclusions_path)

    missing = sorted((expected - exclusions) - covered)
    ensure_covered = sorted((expected - exclusions) & covered)

    write_lines(out_dir / "covered_sources.txt", ensure_covered)
    write_lines(out_dir / "missing_coverage_files.txt", missing)

    print(f"Expected functional files: {len(expected)}")
    print(f"Covered functional files: {len(covered)}")
    print(f"Excluded functional files: {len(exclusions)}")
    print(f"Missing functional files: {len(missing)}")

    if missing:
        preview = "\n".join(missing[:50])
        raise RuntimeError(
            "Coverage denominator validation failed. "
            "Some expected functional sources are missing from LCOV report.\n"
            f"Top missing entries:\n{preview}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
