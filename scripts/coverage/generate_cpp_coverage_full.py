#!/usr/bin/env python3
"""Generate denominator-complete C++ coverage from Bazel and LCOV."""

from __future__ import annotations

import argparse
import pathlib

from scripts.coverage.common import ensure_dir, read_lines, repo_root_from_env, run_cmd
from scripts.coverage.lcov_utils import coverage_stats_from_info
from scripts.coverage.target_sets import compute_target_sets


def _generate_synthetic_baseline(
    out_dir: pathlib.Path,
    expected_sources: list[str],
    repo_root: pathlib.Path,
) -> pathlib.Path:
    """Generate synthetic LCOV baseline with all expected sources marked as 0% hit.

    This approach avoids gcov version mismatch issues and is deterministic.
    Each source file is marked as existing but not executed.
    """

    baseline_info = out_dir / "coverage_baseline.info"
    lines: list[str] = []

    for source in expected_sources:
        source_path = repo_root / source
        lines.append("TN:baseline")
        lines.append(f"SF:{source_path.resolve()}")

        if source_path.exists():
            content = source_path.read_text(encoding="utf-8", errors="replace")
            for line_num, line in enumerate(content.splitlines(), start=1):
                if line.strip():
                    lines.append(f"DA:{line_num},0")
        lines.append("end_of_record")

    baseline_info.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return baseline_info


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate full-denominator C++ coverage report")
    parser.add_argument("--scope", default="//score/...", help="Bazel scope for C++ coverage")
    parser.add_argument("--config", default="x86_64-linux", help="Bazel config to use")
    parser.add_argument("--min-coverage", type=float, default=0.0, help="Minimum line coverage percent")
    parser.add_argument("--out-dir", default="bazel-out/coverage", help="Output directory for reports")
    parser.add_argument("--extra-bazel-flags", default="--test_output=errors --nocache_test_results --lockfile_mode=error")
    args = parser.parse_args()

    repo_root = repo_root_from_env()
    out_dir = repo_root / args.out_dir
    ensure_dir(out_dir)

    target_sets = compute_target_sets(args.scope)
    if not target_sets.functional_targets:
        raise RuntimeError("No functional C++ targets found for denominator.")
    if not target_sets.test_targets:
        raise RuntimeError("No C++ test targets found for numerator.")

    functional_targets_path = out_dir / "functional_targets.txt"
    test_targets_path = out_dir / "test_targets.txt"
    expected_sources_path = out_dir / "expected_sources.txt"

    functional_targets_path.write_text("\n".join(target_sets.functional_targets) + "\n", encoding="utf-8")
    test_targets_path.write_text("\n".join(target_sets.test_targets) + "\n", encoding="utf-8")
    expected_sources_path.write_text("\n".join(target_sets.functional_sources) + "\n", encoding="utf-8")

    output_path = pathlib.Path(run_cmd(["bazel", "info", "output_path"]).stdout.strip())
    execution_root = pathlib.Path(run_cmd(["bazel", "info", "execution_root"]).stdout.strip())

    base_bazel = ["bazel"]
    config_flags = ["--config", args.config]
    extra_flags = [flag for flag in args.extra_bazel_flags.split(" ") if flag]

    run_cmd(
        base_bazel
        + ["build"]
        + config_flags
        + ["--collect_code_coverage", f"--instrumentation_filter={args.scope}"]
        + read_lines(functional_targets_path)
    )

    baseline_info = _generate_synthetic_baseline(
        out_dir=out_dir,
        expected_sources=target_sets.functional_sources,
        repo_root=repo_root,
    )

    run_cmd(
        base_bazel
        + ["coverage"]
        + config_flags
        + ["--combined_report=lcov"]
        + extra_flags
        + read_lines(test_targets_path)
    )

    runtime_info = output_path / "_coverage" / "_coverage_report.dat"
    if not runtime_info.exists():
        raise RuntimeError(f"Coverage runtime report not found: {runtime_info}")

    merged_info = out_dir / "coverage_full.info"
    run_cmd(
        [
            "lcov",
            "-a",
            str(baseline_info),
            "-a",
            str(runtime_info),
            "-o",
            str(merged_info),
        ]
    )

    filtered_info = out_dir / "coverage_full.filtered.info"
    run_cmd(
        [
            "lcov",
            "--remove",
            str(merged_info),
            "*/external/*",
            "*/tests/*",
            "*/test/*",
            "--output-file",
            str(filtered_info),
        ]
    )

    html_dir = out_dir / "coverage_html"
    run_cmd(
        [
            "genhtml",
            "--branch-coverage",
            "--output-directory",
            str(html_dir),
            str(filtered_info),
        ]
    )

    stats = coverage_stats_from_info(filtered_info)
    coverage_percent = stats.line_rate * 100.0
    (out_dir / "coverage_summary.txt").write_text(
        (
            f"lines_found={stats.lines_found}\n"
            f"lines_hit={stats.lines_hit}\n"
            f"line_rate={stats.line_rate:.6f}\n"
            f"line_rate_percent={coverage_percent:.2f}\n"
        ),
        encoding="utf-8",
    )

    print(f"Line coverage: {coverage_percent:.2f}%")
    if coverage_percent < args.min_coverage:
        raise RuntimeError(
            f"Coverage {coverage_percent:.2f}% is below minimum threshold {args.min_coverage:.2f}%"
        )

    print(f"Generated full C++ coverage in: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
