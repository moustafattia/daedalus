#!/usr/bin/env python3
"""One-shot migrator: legacy workflow JSON -> workflow.yaml.

This script exists only to carry older JSON configs into the public
``workflow.yaml`` contract. It is not the primary onboarding path for new
installs; new users should use ``scaffold-workflow`` instead.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import yaml


def _normalize_segment(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    if not normalized:
        raise ValueError(f"unable to derive instance name segment from {value!r}")
    return normalized


def _derive_instance_name(*, github_slug: str, new_path: Path, old: dict) -> str:
    if new_path.name == "workflow.yaml" and new_path.parent.name == "config":
        root_name = new_path.parent.parent.name.strip()
        if root_name:
            return root_name
    legacy_name = Path(old.get("ledgerPath", "")).parent.parent.name.strip()
    if legacy_name:
        return legacy_name
    owner, repo = _parse_github_slug(github_slug)
    return f"{_normalize_segment(owner)}-{_normalize_segment(repo)}-change-delivery"


def _parse_github_slug(value: str) -> tuple[str, str]:
    parts = [part.strip() for part in value.split("/", 1)]
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValueError("expected owner/repo")
    return parts[0], parts[1]


def _resolve_github_slug(*, old: dict, override: str | None) -> str:
    github_slug = (
        (override or "").strip()
        or str(old.get("githubSlug") or "").strip()
        or str(old.get("repositorySlug") or "").strip()
    )
    if not github_slug:
        raise ValueError(
            "github slug missing in legacy config; pass --github-slug owner/repo"
        )
    _parse_github_slug(github_slug)
    return github_slug


def _resolve_milestone_chat_id(*, old: dict, override: str | None) -> str | None:
    if override:
        return override
    schedules = old.get("schedules") or {}
    milestone = schedules.get("milestone-notifier") or schedules.get("milestoneNotifier") or {}
    delivery = milestone.get("delivery") or {}
    for candidate in (
        delivery.get("chat-id"),
        delivery.get("chatId"),
        old.get("milestoneNotifierChatId"),
    ):
        value = str(candidate or "").strip()
        if value:
            return value
    return None


def convert(
    old: dict,
    *,
    new_path: Path,
    github_slug_override: str | None = None,
    milestone_chat_id: str | None = None,
) -> dict:
    session = old.get("sessionPolicy", {}) or {}
    review = old.get("reviewPolicy", {}) or {}
    labels = old.get("agentLabels", {}) or {}

    engine_owner = old.get("engineOwner", "openclaw")
    repo_path = old.get("repoPath", "")
    github_slug = _resolve_github_slug(old=old, override=github_slug_override)
    instance_name = _derive_instance_name(
        github_slug=github_slug,
        new_path=new_path,
        old=old,
    )
    resolved_milestone_chat_id = _resolve_milestone_chat_id(
        old=old,
        override=milestone_chat_id,
    )

    schedules = {
        "watchdog-tick": {"interval-minutes": 5},
    }
    if resolved_milestone_chat_id:
        schedules["milestone-notifier"] = {
            "interval-hours": 1,
            "delivery": {
                "channel": "telegram",
                "chat-id": resolved_milestone_chat_id,
            },
        }

    return {
        "workflow": "change-delivery",
        "schema-version": 1,

        "instance": {
            "name": instance_name,
            "engine-owner": engine_owner,
        },

        "repository": {
            "local-path": repo_path,
            "github-slug": github_slug,
            "active-lane-label": old.get("activeLaneLabel", "active-lane"),
        },

        "runtimes": {
            "acpx-codex": {
                "kind": "acpx-codex",
                "session-idle-freshness-seconds": int(session.get("codexSessionFreshnessSeconds", 900)),
                "session-idle-grace-seconds": int(session.get("codexSessionPokeGraceSeconds", 1800)),
                "session-nudge-cooldown-seconds": int(session.get("codexSessionNudgeCooldownSeconds", 600)),
            },
            "claude-cli": {
                "kind": "claude-cli",
                "max-turns-per-invocation": int(
                    review.get("interReviewAgentMaxTurns")
                    or review.get("internalReviewerAgentMaxTurns")
                    or review.get("claudeReviewMaxTurns", 24)
                ),
                "timeout-seconds": int(
                    review.get("interReviewAgentTimeoutSeconds")
                    or review.get("internalReviewerAgentTimeoutSeconds")
                    or review.get("claudeReviewTimeoutSeconds", 1200)
                ),
            },
        },

        "agents": {
            "coder": {
                "default": {
                    "name": labels.get("internalCoderAgent", "Internal_Coder_Agent"),
                    "model": session.get("codexModel", "gpt-5.3-codex-spark/high"),
                    "runtime": "acpx-codex",
                },
                "high-effort": {
                    "name": labels.get("internalCoderAgent", "Internal_Coder_Agent"),
                    "model": session.get("codexModelLargeEffort") or session.get("codexModelHighEffort") or "gpt-5.3-codex",
                    "runtime": "acpx-codex",
                },
                "escalated": {
                    "name": labels.get("escalationCoderAgent", "Escalation_Coder_Agent"),
                    "model": session.get("codexModelEscalated", "gpt-5.4"),
                    "runtime": "acpx-codex",
                },
            },
            "internal-reviewer": {
                "name": labels.get("internalReviewerAgent", "Internal_Reviewer_Agent"),
                "model": review.get("interReviewAgentModel") or review.get("internalReviewerAgentModel") or review.get("claudeModel", "claude-sonnet-4-6"),
                "runtime": "claude-cli",
                "freeze-coder-while-running": bool(
                    review.get("freezeCoderWhileInterReviewAgentRunning",
                               review.get("freezeCoderWhileInternalReviewAgentRunning",
                                          review.get("freezeCoderWhileClaudeReviewRunning", True)))
                ),
            },
            "external-reviewer": {
                "enabled": True,
                "name": labels.get("externalReviewerAgent", "External_Reviewer_Agent"),
                "provider": "codex-cloud",
                "cache-seconds": int(old.get("reviewCache", {}).get("codexCloudSeconds", 1800)),
            },
            "advisory-reviewer": {
                "enabled": False,
                "name": labels.get("advisoryReviewerAgent", "Advisory_Reviewer_Agent"),
            },
        },

        "gates": {
            "internal-review": {
                "pass-with-findings-tolerance": int(
                    review.get("interReviewAgentPassWithFindingsReviews")
                    or review.get("internalReviewerAgentPassWithFindingsReviews")
                    or review.get("claudePassWithFindingsReviews", 1)
                ),
                "require-pass-clean-before-publish": True,
                "request-cooldown-seconds": int(old.get("reviewCache", {}).get("claudeReviewRequestCooldownSeconds", 1200)),
            },
            "external-review": {
                "required-for-merge": True,
            },
            "merge": {
                "require-ci-acceptable": True,
            },
        },

        "triggers": {
            "lane-selector": {
                "type": "github-label",
                "label": old.get("activeLaneLabel", "active-lane"),
            },
        },

        "escalation": {
            "restart-count-threshold": int(session.get("codexEscalateRestartCount", 2)),
            "local-review-count-threshold": int(session.get("codexEscalateLocalReviewCount", 2)),
            "postpublish-finding-threshold": int(session.get("codexEscalatePostpublishFindingCount", 3)),
            "lane-failure-retry-budget": int(session.get("laneFailureRetryBudget", 3)),
            "no-progress-tick-budget": int(session.get("laneNoProgressTickBudget", 3)),
            "operator-attention-retry-threshold": int(session.get("laneOperatorAttentionRetryThreshold", 5)),
            "operator-attention-no-progress-threshold": int(session.get("laneOperatorAttentionNoProgressThreshold", 5)),
            "lane-counter-increment-min-seconds": int(session.get("laneCounterIncrementMinSeconds", 240)),
        },

        "schedules": schedules,

        "prompts": {
            "internal-review": "internal-review-strict",
            "coder-dispatch": "coder-dispatch",
            "repair-handoff": "repair-handoff",
        },

        "storage": {
            "ledger": "memory/workflow-status.json",
            "health": "memory/workflow-health.json",
            "audit-log": "memory/workflow-audit.jsonl",
            "cron-jobs-path": old.get("cronJobsPath", ""),
            "hermes-cron-jobs-path": old.get("hermesCronJobsPath", str(Path.home() / ".hermes/cron/jobs.json")),
            "sessions-state": "state/sessions",
        },

        "codex-bot": {
            "logins": ["chatgpt-codex-connector", "chatgpt-codex-connector[bot]"],
            "clean-reactions": ["+1"],
            "pending-reactions": ["eyes"],
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Migrate a legacy workflow JSON config into workflow.yaml.",
    )
    parser.add_argument("old_json_path", help="Path to the legacy JSON config.")
    parser.add_argument("new_yaml_path", help="Destination workflow.yaml path.")
    parser.add_argument(
        "--github-slug",
        help="Repository slug in owner/repo form. Required when the legacy JSON does not carry one.",
    )
    parser.add_argument(
        "--milestone-chat-id",
        help="Optional Telegram chat id for the milestone-notifier schedule. When omitted, that schedule is left out.",
    )
    return parser


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    old_path = Path(args.old_json_path).expanduser().resolve()
    new_path = Path(args.new_yaml_path).expanduser().resolve()
    if not old_path.exists():
        print(f"input JSON not found: {old_path}", file=sys.stderr)
        return 1
    if new_path.exists():
        print(f"refusing to overwrite existing file: {new_path}", file=sys.stderr)
        return 1
    old = json.loads(old_path.read_text(encoding="utf-8"))
    try:
        new = convert(
            old,
            new_path=new_path,
            github_slug_override=args.github_slug,
            milestone_chat_id=args.milestone_chat_id,
        )
    except ValueError as exc:
        print(f"migration error: {exc}", file=sys.stderr)
        return 2
    new_path.parent.mkdir(parents=True, exist_ok=True)
    new_path.write_text(yaml.safe_dump(new, sort_keys=False, default_flow_style=False), encoding="utf-8")
    print(f"wrote {new_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
