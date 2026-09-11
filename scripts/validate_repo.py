#!/usr/bin/env python3
"""Run all repository checks that do not require the private backend.

This is the local equivalent of GitHub Actions validation. It intentionally
uses only repository files plus the Ruff CLI, so it can run on a workstation
or the server even when GitHub-hosted runners are unavailable.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def run_step(name: str, command: list[str]) -> bool:
    print(f"\n== {name} ==")
    print("$ " + " ".join(command))
    completed = subprocess.run(command, cwd=ROOT, check=False)
    if completed.returncode:
        print(f"FAILED: {name} (exit={completed.returncode})")
        return False
    print(f"OK: {name}")
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--skip-ruff",
        action="store_true",
        help="skip Ruff only when validating in an environment where Ruff is unavailable",
    )
    args = parser.parse_args()

    steps: list[tuple[str, list[str]]] = [
        ("Python syntax", [sys.executable, "-m", "compileall", "-q", "."]),
    ]

    if not args.skip_ruff:
        ruff = shutil.which("ruff")
        if ruff is None:
            print(
                "Ruff CLI is not available in PATH. Install it with "
                "`python -m pip install ruff` or use --skip-ruff only for a "
                "limited local check.",
                file=sys.stderr,
            )
            return 2
        steps.append(
            (
                "Fatal Python errors",
                [
                    ruff,
                    "check",
                    ".",
                    "--select",
                    "E9,F63,F7,F82",
                    "--output-format=github",
                ],
            )
        )

    steps.extend(
        [
            ("Unit tests", [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"]),
            ("Repository invariants", [sys.executable, "scripts/repo_healthcheck.py"]),
            ("SEO pipeline invariants", [sys.executable, "scripts/seo_pipeline_healthcheck.py"]),
        ]
    )

    failures = 0
    for name, command in steps:
        if not run_step(name, command):
            failures += 1

    if failures:
        print(f"\nRepository validation: FAIL ({failures} failed step(s))")
        return 1
    print("\nRepository validation: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
