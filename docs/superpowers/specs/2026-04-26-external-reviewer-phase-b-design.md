# External Reviewer Pluggability — Phase B Design

**Status:** Approved
**Date:** 2026-04-26
**Branch:** `claude/external-reviewer-phase-b` (worktree at `.claude/worktrees/external-reviewer-phase-b`)
**Baseline:** main `47ae160`, 477 tests passing

## Problem

The code-review workflow's external reviewer is hard-wired to Codex Cloud. The fetcher (`fetch_codex_cloud_review` in `reviews.py`) reads PR review threads via GitHub GraphQL, filters comments by a configured bot-login set, and normalizes them into a provider-neutral output shape. The output shape is already abstract; the *selection* and *fetching* are not.

Operators cannot swap Codex Cloud for another comment-based reviewer (Greptile, CodeRabbit, custom GitHub App) without editing Python. The repair-handoff prompt is also Codex-Cloud–named (`render_codex_cloud_repair_handoff_prompt`) and built inline rather than from a template file.

Phase B delivers external-reviewer pluggability at the *workspace boundary*: a `Reviewer` Protocol with `@register` registry, a generalized `github-comments` provider, schema support for selecting and configuring providers, and a file-based repair-handoff prompt. The internal helper renames in `reviews.py` (`fetch_codex_cloud_review` → `fetch_external_review`, etc.) and the JSON ledger field rename (`codexCloud` → `externalReview`) stay deferred to Phase D.

## Scope

### In scope (this PR)
1. **`Reviewer` Protocol + registry** — new package `workflows/code_review/reviewers/` mirroring `runtimes/`. Protocol has one method: `fetch_review(pr_number, current_head_sha, cached_review) → dict` (provider-neutral output shape). `@register("<kind>")` decorator + `_REVIEWER_KINDS` registry + `build_reviewer(cfg, *, run_json, ws_context) → Reviewer` factory.
2. **`github-comments` provider** — generalized version of the current Codex fetcher. Configurable bot logins, clean/pending reactions, optional cache TTL. Codex Cloud becomes a configuration of this kind (no special-casing). Provider wraps `reviews.fetch_codex_cloud_review` for the actual logic — no helper renames.
3. **`disabled` provider** — explicit kind for `agents.external-reviewer.enabled: false`. Returns the existing `codex_cloud_placeholder` shape with `status: "skipped"`. Simplifies the workspace branch logic.
4. **Schema support:**
   - Add `kind:` field to `agents.external-reviewer:` (enum: `[github-comments, disabled]`, default `github-comments`).
   - Move `logins`, `clean-reactions`, `pending-reactions` INSIDE the reviewer block as nested fields (back-compat: top-level `codex-bot:` block still accepted as fallback for one release with a deprecation warning).
   - Optional `repo-slug:` override on the reviewer (current code hardcodes `moustafattia/YoyoPod_Core` at `workspace.py:1383, 1394` — this becomes config-driven).
5. **Repair-handoff prompt to a file** — `render_codex_cloud_repair_handoff_prompt` migrates from inline string-building to `workflows/code_review/prompts/external-reviewer-repair-handoff.md`. Function renamed `render_external_reviewer_repair_handoff_prompt`. Old name kept as a thin alias for back-compat.
6. **Workspace integration:** `workspace.py` builds the reviewer once during workspace setup (`ws.reviewer`) and routes `_fetch_codex_cloud_review` / `_fetch_codex_pr_body_signal` / `_codex_cloud_placeholder` through it. The existing call sites (`workspace.py:1386, 1404`) keep their names (Phase D rename) but delegate to `ws.reviewer.fetch_review(...)`.
7. **Tests** — reviewer registry, github-comments provider end-to-end with fixture GitHub data, disabled provider, schema validation, prompt template loading, hardcoded-repo-slug regression test.
8. **Operator docs** — `skills/operator/SKILL.md` documents the new reviewer config surface.

### Out of scope (deferred)
- **Phase C:** Webhooks (generic event-emitter for action transitions).
- **Phase D:** Rename pass — `fetch_codex_cloud_review` → `fetch_external_review`, `summarize_codex_cloud_review` → `summarize_external_review`, `build_codex_cloud_thread` → `build_external_review_thread`, `should_dispatch_codex_cloud_repair_handoff` → `should_dispatch_external_review_repair_handoff`, JSON ledger field `codexCloud` → `externalReview`, action-type literal `run_claude_review` → `run_internal_review`, helper injection names (`run_acpx_prompt_fn`).

## Architecture

### Reviewer layering
```
        ┌────────────────────────────────────────┐
        │     workspace.py (action handlers)     │
        │  _fetch_codex_cloud_review,            │
        │  _fetch_codex_pr_body_signal,          │
        │  _codex_cloud_placeholder              │
        └─────────────────┬──────────────────────┘
                          │
                ┌─────────▼──────────┐
                │  ws.reviewer       │  ← NEW (built from agents.external-reviewer)
                │  - fetch_review    │
                └─────────┬──────────┘
                          │
              ┌───────────┼─────────────┐
              ▼           ▼             ▼
        ┌──────────┐ ┌──────────┐ ┌──────────┐
        │ github-  │ │ disabled │ │ (future  │
        │ comments │ │  Reviewer│ │  kinds)  │
        │ Reviewer │ └──────────┘ └──────────┘
        └──────────┘
              │
              ▼
        wraps reviews.fetch_codex_cloud_review
        (renamed in Phase D)
```

### Reviewer Protocol contract
```python
# workflows/code_review/reviewers/__init__.py

@dataclass(frozen=True)
class ReviewerContext:
    """Workspace-scoped primitives a reviewer needs at fetch time."""
    run_json: Callable[..., Any]
    repo_path: Path
    repo_slug: str
    iso_to_epoch: Callable[[str | None], int | None]
    now_epoch: Callable[[], float]
    extract_severity: Callable[[str], str]
    extract_summary: Callable[[str], str]
    agent_name: str
    agent_role: str = "external_reviewer_agent"

@runtime_checkable
class Reviewer(Protocol):
    def fetch_review(
        self,
        *,
        pr_number: int | None,
        current_head_sha: str | None,
        cached_review: dict | None,
    ) -> dict[str, Any]: ...

    def fetch_pr_body_signal(self, pr_number: int | None) -> dict | None: ...

    def placeholder(self, *, required: bool, status: str, summary: str) -> dict: ...
```

### Schema changes
```yaml
# Updated agents.external-reviewer block
external-reviewer:
  type: object
  required: [enabled, name]
  additionalProperties: false
  properties:
    enabled: {type: boolean}
    name: {type: string}
    kind:
      type: string
      enum: [github-comments, disabled]
      # default: github-comments (handled in workspace builder)
    provider: {type: string}        # legacy field, kept for back-compat
    cache-seconds: {type: integer}
    repo-slug: {type: string}        # NEW: override for the GraphQL query
    logins:
      type: array
      items: {type: string}
    clean-reactions:
      type: array
      items: {type: string}
    pending-reactions:
      type: array
      items: {type: string}

# Top-level codex-bot block: kept as deprecated fallback
codex-bot:
  type: object
  description: "Deprecated — move logins/reactions inside agents.external-reviewer"
  properties:
    logins: {type: array, items: {type: string}}
    clean-reactions: {type: array, items: {type: string}}
    pending-reactions: {type: array, items: {type: string}}
```

### Reviewer config resolution order
For each reviewer field (`logins`, `clean-reactions`, `pending-reactions`, `cache-seconds`):
1. `agents.external-reviewer.<field>` if present
2. Top-level `codex-bot.<field>` if present (deprecated path; emit a one-time warning at workspace build)
3. Built-in defaults (`logins: ["chatgpt-codex-connector[bot]"]`, `clean-reactions: ["+1", "rocket", "heart", "hooray"]`, `pending-reactions: ["eyes"]`, `cache-seconds: 300`)

### `repo-slug` resolution
Same resolution: `agents.external-reviewer.repo-slug` > current hardcoded `"moustafattia/YoyoPod_Core"` (preserved for back-compat one release) > error if neither.

### Repair-handoff prompt migration
- Move the line-by-line content of `render_codex_cloud_repair_handoff_prompt` into `workflows/code_review/prompts/external-reviewer-repair-handoff.md` as a `.format()` template.
- Add `render_external_reviewer_repair_handoff_prompt` that calls `_load_template("external-reviewer-repair-handoff").format(**kwargs)`.
- Keep `render_codex_cloud_repair_handoff_prompt = render_external_reviewer_repair_handoff_prompt` as a back-compat alias (one-line module-level binding).
- Workspace prompt overrides at `<workspace>/config/prompts/external-reviewer-repair-handoff.md` work via the Phase A resolution chain (no new code — the bundled file is just another candidate Phase A's `resolve_prompt_template_path` already checks).

## Data flow (one tick: fetch external review)

1. Workspace builder reads `agents.external-reviewer.kind` (default `github-comments`).
2. Builder constructs a `Reviewer` instance via `build_reviewer(cfg, *, ws_context)`, stored on `ns.reviewer`.
3. The existing `_fetch_codex_cloud_review(pr_number, current_head_sha, cached_review)` workspace shim now calls `ns.reviewer.fetch_review(pr_number=..., current_head_sha=..., cached_review=...)` instead of the inline `reviews.fetch_codex_cloud_review(...)` call.
4. `GithubCommentsReviewer.fetch_review` delegates to `reviews.fetch_codex_cloud_review` with provider-config values from its own state. Behavior identical to today.
5. `DisabledReviewer.fetch_review` returns `placeholder(required=False, status="skipped", summary="External review disabled.")` — independent of pr_number.

## Migration path for live `yoyopod` workspace

Live `~/.hermes/workflows/yoyopod/config/workflow.yaml` currently has:
```yaml
agents:
  external-reviewer:
    enabled: true
    name: ChatGPT_Codex_Cloud
    provider: chatgpt-codex
    cache-seconds: 300

codex-bot:
  logins: [chatgpt-codex-connector[bot]]
  clean-reactions: [+1, rocket, heart, hooray]
  pending-reactions: [eyes]
```

After this PR (no edits required by operator):
- `kind:` defaults to `github-comments` since `enabled: true` and no explicit kind.
- `logins`/reactions read via deprecation fallback from top-level `codex-bot:` (warning logged once on workspace build).
- `repo-slug` falls back to current hardcoded value.

Operator can opt into the new clean form by moving the bot config inside the reviewer block — both forms continue to work for one release.

## Tests

New file `tests/test_external_reviewer_phase_b.py`:
- `test_reviewer_protocol_kinds_registered` — `github-comments` and `disabled` both in `_REVIEWER_KINDS`.
- `test_github_comments_reviewer_fetch_uses_configured_logins` — provider passes its `logins` field to the underlying fetcher (mocked).
- `test_github_comments_reviewer_fetch_uses_configured_repo_slug` — provider's repo-slug is used in the GraphQL call (regression test for the hardcoded `moustafattia/YoyoPod_Core`).
- `test_github_comments_reviewer_falls_back_to_codex_bot_block` — when only top-level `codex-bot:` is present, provider reads logins from there.
- `test_disabled_reviewer_fetch_returns_skipped_placeholder` — placeholder with `status: "skipped"`, `required: False`.
- `test_disabled_reviewer_fetch_does_not_call_run_json` — no GitHub API calls when disabled.
- `test_build_reviewer_unknown_kind_raises` — `ValueError` with registered-kinds list.
- `test_build_reviewer_defaults_to_github_comments_when_enabled` — no explicit kind ⇒ github-comments.
- `test_build_reviewer_defaults_to_disabled_when_enabled_false` — `enabled: false` ⇒ DisabledReviewer regardless of kind.

New file `tests/test_external_reviewer_schema.py`:
- `test_schema_accepts_kind_github_comments`
- `test_schema_accepts_kind_disabled`
- `test_schema_accepts_repo_slug_override`
- `test_schema_accepts_logins_inside_reviewer_block`
- `test_schema_rejects_unknown_kind`
- `test_existing_yoyopod_workflow_yaml_still_validates`

New file `tests/test_external_reviewer_repair_handoff_prompt.py`:
- `test_repair_handoff_template_loads_from_file` — bundled `prompts/external-reviewer-repair-handoff.md` exists and parses.
- `test_render_external_reviewer_repair_handoff_prompt_matches_legacy_output` — output of the new function matches the (frozen) output of `render_codex_cloud_repair_handoff_prompt` for a fixed input.
- `test_codex_cloud_alias_still_callable` — back-compat alias works.

Existing 477 tests stay green. Target: ~477 + 18 new = ~495 passing. Yoyopod regression test remains green.

## Open questions

None — all locked in based on user's "go ahead":
- `disabled` is a first-class kind (not just `enabled: false` flag).
- Top-level `codex-bot:` stays one release as deprecated fallback.
- Helper renames stay deferred to Phase D.
- `repo-slug` becomes config-driven (current hardcode is a preserved fallback).
