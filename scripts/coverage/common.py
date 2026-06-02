#!/usr/bin/env python3
"""Shared helpers for coverage tooling."""

from __future__ import annotations

import os
import pathlib
import subprocess
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class CmdResult:
    """Simple command result for testability and traceability."""

    stdout: str
    stderr: str


def run_cmd(command: list[str], cwd: pathlib.Path | None = None) -> CmdResult:
    """Run command and raise with detailed diagnostics on failure."""

    completed = subprocess.run(  # noqa: S603
        command,
        cwd=str(cwd) if cwd else None,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        cmd_as_text = " ".join(command)
        raise RuntimeError(
            f"Command failed ({completed.returncode}): {cmd_as_text}\n"
            f"stdout:\n{completed.stdout}\n"
            f"stderr:\n{completed.stderr}"
        )
    return CmdResult(stdout=completed.stdout, stderr=completed.stderr)


def ensure_dir(path: pathlib.Path) -> None:
    """Create directory tree if needed."""

    path.mkdir(parents=True, exist_ok=True)


def write_lines(path: pathlib.Path, lines: Iterable[str]) -> None:
    """Write a deterministic newline-terminated file."""

    text = "\n".join(lines)
    if text:
        text += "\n"
    path.write_text(text, encoding="utf-8")


def read_lines(path: pathlib.Path) -> list[str]:
    """Read non-empty stripped lines from file."""

    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def repo_root_from_env() -> pathlib.Path:
    """Resolve repository root from environment override or working directory."""

    override = os.getenv("COVERAGE_REPO_ROOT", "").strip()
    if override:
        return pathlib.Path(override).resolve()
    return pathlib.Path(os.getcwd()).resolve()
