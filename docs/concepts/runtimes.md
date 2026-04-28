# Runtimes

A **runtime** is the thing Daedalus shells out to when a turn happens. Daedalus owns leases, state, and dispatch; the runtime owns "how do I actually run an LLM turn against a worktree." Three are shipped today.

## The Protocol

```python
class Runtime(Protocol):
    def ensure_session(*, worktree, session_name, model, resume_session_id) -> SessionHandle
    def run_prompt(*, worktree, session_name, prompt, model) -> str
    def run_command(*, worktree, command_argv, env) -> str   # for `command:` overrides
    def assess_health(session_meta, *, worktree, now_epoch) -> SessionHealth
    def close_session(*, worktree, session_name) -> None

    # Optional — runtime opts out by simply not defining it.
    def last_activity_ts() -> float | None
```

`last_activity_ts()` is the Symphony §8.5 hook that lets [stall detection](stalls.md) work. Runtimes without it are skipped by the reconciler — they opt out silently.

## Adapter shape comparison

| | `claude-cli` | `acpx-codex` | `hermes-agent` |
|---|---|---|---|
| Persistent session | ❌ one-shot | ✅ resumable | ❌ one-shot |
| `ensure_session` | no-op | `acpx codex sessions ensure` | no-op |
| `run_prompt` | `claude --print …` | `acpx codex prompt -s <name>` | requires `command:` override |
| `assess_health` | always healthy | freshness + grace window | always healthy |
| `close_session` | no-op | `acpx codex sessions close` | no-op |
| Records `last_activity_ts` | yes (before + after `_run`) | yes | yes |

## Selection in `workflow.yaml`

```yaml
runtimes:
  coder-runtime:
    kind: claude-cli
    max-turns-per-invocation: 24
    timeout-seconds: 1200
  reviewer-runtime:
    kind: acpx-codex
    session-idle-freshness-seconds: 900
    session-idle-grace-seconds: 1800
    session-nudge-cooldown-seconds: 600

agents:
  coder:
    t1: { name: claude-coder, model: opus, runtime: coder-runtime }
  internal-reviewer:
    name: codex-reviewer
    model: gpt-5
    runtime: reviewer-runtime
```

The preflight pass walks `runtimes.<name>.kind` and `agents.external-reviewer.kind` to confirm every referenced runtime resolves to a registered adapter before a tick dispatches.

## Adding a new runtime

1. Subclass nothing — just implement the Protocol shape.
2. Decorate with `@register("<your-kind>")` from `workflows.code_review.runtimes`.
3. Add the kind to `schema.yaml` so config validation accepts it.
4. Optionally implement `last_activity_ts()` for stall participation.

## Where this lives in code

- Protocol: `workflows/code_review/runtimes/__init__.py`
- Adapters: `workflows/code_review/runtimes/{claude_cli,acpx_codex,hermes_agent}.py`
- Preflight: `workflows/code_review/preflight.py`
