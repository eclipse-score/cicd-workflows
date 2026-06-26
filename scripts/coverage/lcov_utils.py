#!/usr/bin/env python3
"""LCOV parsing and normalization helpers."""

from __future__ import annotations

import pathlib
import re
from dataclasses import dataclass

CPP_EXTENSIONS = (".c", ".cc", ".cpp", ".cxx")


@dataclass(frozen=True)
class CoverageStats:
    """Summary stats extracted from LCOV DA records."""

    lines_found: int
    lines_hit: int

    @property
    def line_rate(self) -> float:
        if self.lines_found == 0:
            return 0.0
        return self.lines_hit / self.lines_found


def normalize_sf_path(sf_path: str, repo_root: pathlib.Path) -> str | None:
    """Normalize LCOV SF path to workspace-relative score path if possible."""

    candidate = sf_path.strip()
    if not candidate:
        return None

    path_obj = pathlib.Path(candidate)
    if path_obj.is_absolute():
        try:
            relative = path_obj.resolve().relative_to(repo_root.resolve())
            return relative.as_posix()
        except ValueError:
            pass

    if candidate.startswith("score/") and candidate.endswith(CPP_EXTENSIONS):
        return candidate

    match = re.search(r"(?:^|/)score/.+", candidate)
    if match:
        extracted = match.group(0)
        if extracted.endswith(CPP_EXTENSIONS):
            return extracted

    return None


def extract_covered_sources(lcov_info_path: pathlib.Path, repo_root: pathlib.Path) -> set[str]:
    """Extract normalized set of C/C++ sources represented in LCOV report."""

    covered: set[str] = set()
    for line in lcov_info_path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("SF:"):
            continue
        normalized = normalize_sf_path(line[3:], repo_root)
        if normalized is not None:
            covered.add(normalized)
    return covered


def coverage_stats_from_info(lcov_info_path: pathlib.Path) -> CoverageStats:
    """Compute line coverage stats from DA records."""

    lines_found = 0
    lines_hit = 0
    for line in lcov_info_path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("DA:"):
            continue
        lines_found += 1
        _, payload = line.split(":", maxsplit=1)
        _, hit_text = payload.split(",", maxsplit=1)
        if int(hit_text) > 0:
            lines_hit += 1

    return CoverageStats(lines_found=lines_found, lines_hit=lines_hit)
