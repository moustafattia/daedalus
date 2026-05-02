#!/usr/bin/env python3
"""Mechanical release-readiness scorecard checks.

This does not decide whether Daedalus is ready to publish; it verifies that the
public scorecard still names the current evidence paths and launch gates.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
READINESS = REPO_ROOT / "docs" / "release-readiness.md"
HARNESS = REPO_ROOT / "docs" / "harness-engineering.md"


@dataclass(frozen=True)
class Check:
    name: str
    path: Path
    phrase: str

    def result(self) -> dict[str, object]:
        exists = self.path.exists()
        text = self.path.read_text(encoding="utf-8") if exists and self.path.is_file() else ""
        ok = exists and self.phrase in text
        return {
            "name": self.name,
            "ok": ok,
            "path": self.path.relative_to(REPO_ROOT).as_posix(),
            "phrase": self.phrase,
        }


CHECKS: tuple[Check, ...] = (
    Check("public beta posture", READINESS, "public beta candidate"),
    Check("issue-runner reference", READINESS, "Reference workflow: `issue-runner`"),
    Check("change-delivery flagship", READINESS, "Flagship workflow: `change-delivery`"),
    Check("GitHub first class", READINESS, "First-class tracker: GitHub"),
    Check("Codex app-server opt-in", READINESS, "real Codex app-server smokes remain opt-in"),
    Check("change-delivery E2E gap", READINESS, "full issue-to-PR-to-review-to-merge E2E"),
    Check("live smoke harness", HARNESS, "scripts/smoke-live.sh"),
    Check("scorecard automation", HARNESS, "release-scorecard.yml"),
    Check("GitHub smoke test", REPO_ROOT / "tests" / "test_github_issue_runner_smoke.py", "test_live_github_issue_runner_feedback_retry_recovery_and_cleanup"),
    Check("change-delivery Codex smoke", REPO_ROOT / "tests" / "test_change_delivery_codex_app_server_smoke.py", "test_live_change_delivery_codex_app_server_creates_issue_and_dispatches_lane"),
    Check("smoke runner script", REPO_ROOT / "scripts" / "smoke_live.py", "change-delivery-codex"),
)


def collect() -> dict[str, object]:
    results = [check.result() for check in CHECKS]
    return {
        "ok": all(bool(item["ok"]) for item in results),
        "checks": results,
    }


def _markdown(report: dict[str, object]) -> str:
    lines = [
        "# Release Scorecard",
        "",
        "| Check | Status | Evidence |",
        "|---|---|---|",
    ]
    for item in report["checks"]:
        status = "ok" if item["ok"] else "missing"
        lines.append(f"| {item['name']} | {status} | `{item['path']}` |")
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check or print the release-readiness scorecard.")
    parser.add_argument("--check", action="store_true", help="Exit non-zero if required evidence is missing.")
    parser.add_argument("--markdown", action="store_true", help="Print a Markdown summary instead of JSON.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = collect()
    if args.markdown:
        print(_markdown(report), end="")
    else:
        print(json.dumps(report, indent=2))
    return 1 if args.check and not report["ok"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
