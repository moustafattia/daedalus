#!/usr/bin/env python3
"""Run Daedalus opt-in live smoke tests.

The script is intentionally environment-driven: each smoke declares the env
vars that make it runnable, and unavailable smokes are skipped rather than
pretending to validate live services.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Smoke:
    name: str
    description: str
    command: tuple[str, ...]
    required_env: tuple[str, ...]

    def enabled(self, env: dict[str, str]) -> bool:
        return all(str(env.get(key) or "").strip() for key in self.required_env)

    def missing(self, env: dict[str, str]) -> list[str]:
        return [key for key in self.required_env if not str(env.get(key) or "").strip()]


SMOKES: tuple[Smoke, ...] = (
    Smoke(
        name="github-issue-runner",
        description="Live GitHub issue-runner retry and cleanup smoke.",
        command=(sys.executable, "-m", "pytest", "tests/test_github_issue_runner_smoke.py", "-q", "-s"),
        required_env=("DAEDALUS_GITHUB_SMOKE_REPO",),
    ),
    Smoke(
        name="codex-app-server-runtime",
        description="Real Codex app-server start/resume runtime smoke.",
        command=(
            sys.executable,
            "-m",
            "pytest",
            "tests/test_runtimes_codex_app_server.py",
            "-k",
            "real_smoke_start_and_resume",
            "-q",
            "-s",
        ),
        required_env=("DAEDALUS_REAL_CODEX_APP_SERVER",),
    ),
    Smoke(
        name="runtime-matrix-codex",
        description="Runtime-matrix issue-runner smoke against the shared Codex service profile.",
        command=(
            sys.executable,
            "-m",
            "pytest",
            "tests/test_runtime_matrix.py",
            "-k",
            "real_codex_service_issue_runner_smoke",
            "-q",
            "-s",
        ),
        required_env=("DAEDALUS_REAL_CODEX_APP_SERVER",),
    ),
    Smoke(
        name="change-delivery-codex",
        description="Self-contained change-delivery GitHub issue plus Codex app-server lane dispatch smoke.",
        command=(
            sys.executable,
            "-m",
            "pytest",
            "tests/test_change_delivery_codex_app_server_smoke.py",
            "-q",
            "-s",
        ),
        required_env=("DAEDALUS_CHANGE_DELIVERY_CODEX_E2E", "DAEDALUS_CHANGE_DELIVERY_E2E_REPO"),
    ),
)


def _selected_smokes(names: list[str] | None) -> list[Smoke]:
    if not names:
        return list(SMOKES)
    known = {smoke.name: smoke for smoke in SMOKES}
    unknown = sorted(set(names) - set(known))
    if unknown:
        raise SystemExit(f"unknown smoke name(s): {', '.join(unknown)}")
    return [known[name] for name in names]


def _print_plan(smokes: list[Smoke], env: dict[str, str]) -> None:
    for smoke in smokes:
        if smoke.enabled(env):
            print(f"run  {smoke.name}: {' '.join(smoke.command)}")
        else:
            print(f"skip {smoke.name}: missing {', '.join(smoke.missing(env))}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run configured opt-in Daedalus live smokes.")
    parser.add_argument(
        "--only",
        action="append",
        choices=[smoke.name for smoke in SMOKES],
        help="Run only this smoke. Can be passed more than once.",
    )
    parser.add_argument("--list", action="store_true", help="List known smokes and whether they are configured.")
    parser.add_argument("--dry-run", action="store_true", help="Print the commands that would run.")
    parser.add_argument(
        "--fail-if-none",
        action="store_true",
        help="Return non-zero when no selected smoke has the required environment.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    env = dict(os.environ)
    smokes = _selected_smokes(args.only)

    if args.list or args.dry_run:
        _print_plan(smokes, env)
        return 0

    runnable = [smoke for smoke in smokes if smoke.enabled(env)]
    skipped = [smoke for smoke in smokes if not smoke.enabled(env)]
    for smoke in skipped:
        print(f"skip {smoke.name}: missing {', '.join(smoke.missing(env))}")

    if not runnable:
        print("no live smokes configured")
        return 2 if args.fail_if_none else 0

    for smoke in runnable:
        print(f"run {smoke.name}")
        completed = subprocess.run(smoke.command, cwd=REPO_ROOT, env=env, check=False)
        if completed.returncode != 0:
            return int(completed.returncode)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
