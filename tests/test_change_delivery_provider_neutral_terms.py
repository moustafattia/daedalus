from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]

BANNED_TERMS = (
    "awaiting_claude_prepublish",
    "claude_prepublish_findings",
    "dispatch-claude-review",
    "preflight-claude-review",
    "claudeReview",
    "claude-review-",
    "prepublish-claude-required",
    "workflow-not-awaiting-local-claude",
    "single-pass-claude",
    "claude_findings_open",
    "claude_preflight_blocked",
    "codex_cloud_findings_open",
    "currentClaude",
    "lastCodexCloud",
    "prePublishReviewModel",
    "INTER_REVIEW_AGENT_",
    "CLAUDE_REVIEW_",
    "CLAUDE_PASS_",
    "CODEX_CLOUD_",
    "dispatch_codex_turn",
    "sessionRuntime",
    "coder_agent",
    "Codex implementation session",
    "persistent Codex implementation",
    "active coder session",
    "active Codex session",
)

SCAN_ROOTS = (
    REPO_ROOT / "daedalus" / "workflows" / "change_delivery",
    REPO_ROOT / "daedalus" / "runtime.py",
    REPO_ROOT / "docs",
    REPO_ROOT / "tests",
)


def _iter_files(root: Path):
    if root.is_file():
        yield root
        return
    for path in root.rglob("*"):
        if path.is_file() and path.suffix in {".py", ".md", ".yaml", ".yml", ".svg"}:
            yield path


def test_change_delivery_workflow_semantics_do_not_use_provider_terms():
    current_file = Path(__file__).resolve()
    violations: list[str] = []
    for root in SCAN_ROOTS:
        for path in _iter_files(root):
            if path.resolve() == current_file:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for term in BANNED_TERMS:
                if term in text:
                    violations.append(f"{path.relative_to(REPO_ROOT)}: {term}")

    assert violations == []
