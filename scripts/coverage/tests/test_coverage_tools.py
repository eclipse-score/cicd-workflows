#!/usr/bin/env python3
"""Unit tests for coverage tooling."""

from __future__ import annotations

import pathlib
import tempfile
import unittest
from unittest.mock import Mock, patch

from scripts.coverage.generate_cpp_coverage_full import _generate_synthetic_baseline
from scripts.coverage.lcov_utils import coverage_stats_from_info, extract_covered_sources, normalize_sf_path
from scripts.coverage.target_sets import _normalize_source_label, compute_target_sets
from scripts.coverage.validate_cpp_coverage_denominator import _load_exclusions


class NormalizeSourceLabelTests(unittest.TestCase):
    def test_normalize_valid_score_cpp_label(self) -> None:
        self.assertEqual(
            _normalize_source_label("//score/launch_manager/daemon/src:main.cpp"),
            "score/launch_manager/daemon/src/main.cpp",
        )

    def test_reject_non_cpp_label(self) -> None:
        self.assertIsNone(_normalize_source_label("//score/launch_manager/daemon/src:main.hpp"))

    def test_reject_non_score_label(self) -> None:
        self.assertIsNone(_normalize_source_label("//examples/demo:main.cpp"))


class LcovParsingTests(unittest.TestCase):
    def test_normalize_absolute_sf_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            repo_root = pathlib.Path(tmp_dir)
            source_path = repo_root / "score/launch_manager/daemon/src/main.cpp"
            source_path.parent.mkdir(parents=True, exist_ok=True)
            source_path.write_text("int main() { return 0; }\n", encoding="utf-8")

            normalized = normalize_sf_path(str(source_path), repo_root)
            self.assertEqual(normalized, "score/launch_manager/daemon/src/main.cpp")

    def test_extract_covered_sources(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            repo_root = pathlib.Path(tmp_dir)
            info_path = repo_root / "coverage.info"
            info_path.write_text(
                "\n".join(
                    [
                        "TN:",
                        f"SF:{repo_root}/score/a.cpp",
                        "DA:1,1",
                        "DA:2,0",
                        "end_of_record",
                        "SF:score/b.cpp",
                        "DA:1,0",
                        "end_of_record",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            covered = extract_covered_sources(info_path, repo_root)
            self.assertEqual(covered, {"score/a.cpp", "score/b.cpp"})

    def test_coverage_stats_from_info(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            info_path = pathlib.Path(tmp_dir) / "coverage.info"
            info_path.write_text(
                "\n".join(
                    [
                        "SF:score/a.cpp",
                        "DA:1,1",
                        "DA:2,0",
                        "end_of_record",
                        "SF:score/b.cpp",
                        "DA:1,0",
                        "DA:2,1",
                        "end_of_record",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            stats = coverage_stats_from_info(info_path)
            self.assertEqual(stats.lines_found, 4)
            self.assertEqual(stats.lines_hit, 2)
            self.assertAlmostEqual(stats.line_rate, 0.5)


class ComputeTargetSetsTests(unittest.TestCase):
    @patch("scripts.coverage.target_sets._query_labels")
    def test_compute_target_sets(self, query_mock: Mock) -> None:
        query_mock.side_effect = [
            ["//score/lib:a", "//score/bin:tool"],
            ["//score/lib:a_test"],
            [
                "//score/lib:a.cpp",
                "//score/lib:b.cc",
                "//score/lib:header.hpp",
                "//external/dep:foo.cpp",
            ],
        ]

        sets = compute_target_sets("//score/...")

        self.assertEqual(sets.functional_targets, ["//score/bin:tool", "//score/lib:a"])
        self.assertEqual(sets.test_targets, ["//score/lib:a_test"])
        self.assertEqual(sets.functional_sources, ["score/lib/a.cpp", "score/lib/b.cc"])


class SyntheticBaselineTests(unittest.TestCase):
    def test_generate_synthetic_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = pathlib.Path(tmp_dir)
            repo_root = tmp_path

            score_dir = repo_root / "score/lib"
            score_dir.mkdir(parents=True, exist_ok=True)

            source_a = score_dir / "a.cpp"
            source_a.write_text("int foo() {\n\n    return 42;\n}\n", encoding="utf-8")

            source_b = score_dir / "b.cc"
            source_b.write_text("void bar() {}\n", encoding="utf-8")

            out_dir = repo_root / "coverage_out"
            out_dir.mkdir()

            baseline_path = _generate_synthetic_baseline(
                out_dir=out_dir,
                expected_sources=["score/lib/a.cpp", "score/lib/b.cc"],
                repo_root=repo_root,
            )

            content = baseline_path.read_text(encoding="utf-8")
            self.assertIn("SF:", content)
            self.assertIn("DA:", content)
            self.assertIn("end_of_record", content)
            self.assertIn("score/lib/a.cpp", content)
            self.assertIn("score/lib/b.cc", content)
            self.assertIn("DA:3,0", content)
            self.assertNotIn("DA:2,0", content)

            stats = coverage_stats_from_info(baseline_path)
            self.assertEqual(stats.lines_found, 4)
            self.assertEqual(stats.lines_hit, 0)
            self.assertEqual(stats.line_rate, 0.0)


class CoverageExclusionsTests(unittest.TestCase):
    def test_load_exclusions_ignores_comments(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            exclusions_path = pathlib.Path(tmp_dir) / "coverage_exclusions.txt"
            exclusions_path.write_text(
                "\n".join(
                    [
                        "# comment",
                        "score/lib/a.cpp",
                        "  # indented comment",
                        "",
                        "score/lib/b.cc",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            exclusions = _load_exclusions(exclusions_path)
            self.assertEqual(exclusions, {"score/lib/a.cpp", "score/lib/b.cc"})


if __name__ == "__main__":
    unittest.main()
