#!/usr/bin/env python3
"""Run the complete x-gu.ru SEO diagnostics into one timestamped snapshot.

The pipeline writes report/review artifacts only. It never calls shrink, purge,
rerender, deploy, redirects, robots mutation or production policy promotion.
Some child tools use ``--apply`` solely to write review JSON/text files; those
specific tools are intentionally non-production-mutating.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


SERVER_OPT = Path(__file__).resolve().parent
REPO_ROOT = SERVER_OPT.parent
DEFAULT_ROOT = Path("/var/www/x-gu.ru/current")
DEFAULT_SNAPSHOTS = Path("/opt/p3-app/data/seo-snapshots")


REPORT_WRITE_ONLY_SCRIPTS = {
    "build_search_evidence.py",
    "pair_quality_audit.py",
    "index_coverage_review.py",
    "programmatic_seo_audit.py",
    "link_graph_cluster_audit.py",
    "metadata_intent_audit.py",
    "whitelist_lifecycle_report.py",
    "build_pair_policy.py",
    "gsc_cannibalization_report.py",
    "build_cannibalization_review.py",
    "seo_action_queue.py",
}


def _tooling_revision() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def build_steps(
    *,
    python: str,
    root: Path,
    out_dir: Path,
    days: int,
    max_input_age_days: int,
) -> list[tuple[str, list[str]]]:
    evidence = out_dir / "search_evidence.json"
    quality = out_dir / "pair_quality.json"
    coverage = out_dir / "index_coverage.json"
    graph = out_dir / "link_graph_cluster.json"
    metadata = out_dir / "metadata_intent.json"
    cannibalization = out_dir / "gsc_cannibalization.json"
    cann_review = out_dir / "cannibalization.review.json"

    def tool(name: str) -> str:
        if name not in REPORT_WRITE_ONLY_SCRIPTS:
            raise ValueError(f"script is not allowed in read-only SEO snapshot: {name}")
        return str(SERVER_OPT / name)

    return [
        (
            "search evidence",
            [
                python,
                tool("build_search_evidence.py"),
                "--days",
                str(days),
                "--candidate-out",
                str(out_dir / "whitelist.candidate.txt"),
                "--evidence-out",
                str(evidence),
                "--apply",
            ],
        ),
        (
            "pair quality",
            [
                python,
                tool("pair_quality_audit.py"),
                "--root",
                str(root),
                "--evidence",
                str(evidence),
                "--out",
                str(quality),
            ],
        ),
        (
            "programmatic corpus",
            [
                python,
                tool("programmatic_seo_audit.py"),
                "--root",
                str(root),
                "--json-out",
                str(out_dir / "programmatic_seo.json"),
            ],
        ),
        (
            "link graph and service clusters",
            [
                python,
                tool("link_graph_cluster_audit.py"),
                "--root",
                str(root),
                "--json-out",
                str(graph),
            ],
        ),
        (
            "metadata and intent similarity",
            [
                python,
                tool("metadata_intent_audit.py"),
                "--root",
                str(root),
                "--json-out",
                str(metadata),
            ],
        ),
        (
            "index coverage cohorts",
            [
                python,
                tool("index_coverage_review.py"),
                "--root",
                str(root),
                "--evidence",
                str(evidence),
                "--quality",
                str(quality),
                "--json-out",
                str(coverage),
            ],
        ),
        (
            "whitelist lifecycle",
            [
                python,
                tool("whitelist_lifecycle_report.py"),
                "--whitelist",
                str(root / ".xgu-whitelist.txt"),
                "--evidence",
                str(evidence),
                "--json-out",
                str(out_dir / "whitelist_lifecycle.json"),
            ],
        ),
        (
            "policy v2 candidate",
            [
                python,
                tool("build_pair_policy.py"),
                "--evidence",
                str(evidence),
                "--quality",
                str(quality),
                "--max-input-age-days",
                str(max_input_age_days),
                "--out",
                str(out_dir / "index_policy.v2.candidate.json"),
                "--review-out",
                str(out_dir / "index_policy.v2.review.json"),
                "--apply",
            ],
        ),
        (
            "GSC cannibalization",
            [
                python,
                tool("gsc_cannibalization_report.py"),
                "--days",
                str(days),
                "--out",
                str(cannibalization),
            ],
        ),
        (
            "cannibalization review",
            [
                python,
                tool("build_cannibalization_review.py"),
                "--cannibalization",
                str(cannibalization),
                "--quality",
                str(quality),
                "--out",
                str(cann_review),
                "--apply",
            ],
        ),
        (
            "unified action queue",
            [
                python,
                tool("seo_action_queue.py"),
                "--coverage",
                str(coverage),
                "--cannibalization",
                str(cann_review),
                "--graph",
                str(graph),
                "--metadata",
                str(metadata),
                "--out",
                str(out_dir / "seo_action_queue.json"),
                "--apply",
            ],
        ),
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--snapshots-root", type=Path, default=DEFAULT_SNAPSHOTS)
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--max-input-age-days", type=int, default=14)
    parser.add_argument("--name", default="", help="optional snapshot directory name")
    args = parser.parse_args()

    if args.days < 1 or args.max_input_age_days < 0:
        print("days must be positive and max input age non-negative", file=sys.stderr)
        return 2
    if not args.root.is_dir():
        print(f"release root not found: {args.root}", file=sys.stderr)
        return 2
    if not args.snapshots_root.is_dir():
        print(f"snapshots root not found: {args.snapshots_root}", file=sys.stderr)
        return 2

    name = args.name.strip() or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if "/" in name or name in {".", ".."}:
        print("invalid snapshot name", file=sys.stderr)
        return 2
    out_dir = args.snapshots_root / name
    if out_dir.exists():
        print(f"snapshot directory already exists: {out_dir}", file=sys.stderr)
        return 2
    out_dir.mkdir(mode=0o750)

    steps = build_steps(
        python=sys.executable,
        root=args.root.resolve(),
        out_dir=out_dir.resolve(),
        days=args.days,
        max_input_age_days=args.max_input_age_days,
    )
    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "root": str(args.root.resolve()),
        "days": args.days,
        "tooling_revision": _tooling_revision(),
        "report_writes_only": True,
        "steps": [name for name, _command in steps],
        "status": "running",
    }
    (out_dir / "snapshot_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    for step_name, command in steps:
        print(f"\n== {step_name} ==")
        completed = subprocess.run(command, cwd=REPO_ROOT, check=False)
        if completed.returncode:
            manifest["status"] = "failed"
            manifest["failed_step"] = step_name
            manifest["failed_exit"] = completed.returncode
            (out_dir / "snapshot_manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            print(f"SEO snapshot failed at: {step_name}", file=sys.stderr)
            return completed.returncode

    manifest["status"] = "complete"
    manifest["completed_at"] = datetime.now(timezone.utc).isoformat()
    (out_dir / "snapshot_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print("\nSEO snapshot: COMPLETE")
    print(f"  reports: {out_dir}")
    print(f"  action queue: {out_dir / 'seo_action_queue.json'}")
    print("  production SEO state was not changed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
