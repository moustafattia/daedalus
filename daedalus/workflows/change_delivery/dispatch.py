"""Generic agent dispatcher.

Resolves runtime + command + prompt-template path from workspace config,
loads and renders the resolved prompt template, materializes the rendered
text to a file inside the worktree, fills placeholders in the command argv,
and invokes the runtime.

Phase A only — no model-tied call sites. The dispatcher itself owns
template loading + rendering so that the workspace prompt-override surface
(``agents.<role>.prompt`` or ``<workspace>/config/prompts/<role>.md``)
actually drives what the agent sees.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

_BUNDLED_PROMPTS = Path(__file__).parent / "prompts"


class DispatchConfigError(Exception):
    """Raised on misconfigured agent role / runtime / command override."""


def _agent_cfg(workspace, role: str, tier: str | None) -> dict:
    agents = (workspace.config or {}).get("agents") or {}
    if role not in agents:
        raise DispatchConfigError(f"unknown agent role: {role!r}")
    role_cfg = agents[role]

    # coder is tiered (map of tier -> cfg); other roles are flat dicts.
    if role == "coder":
        if not tier:
            raise DispatchConfigError(f"role {role!r} requires a tier")
        if tier not in role_cfg:
            raise DispatchConfigError(f"unknown tier {tier!r} for role {role!r}")
        return role_cfg[tier]
    return role_cfg


def resolve_prompt_template_path(
    *,
    workspace,
    role: str,
    agent_cfg: dict,
) -> Path:
    """Resolution order:

    1. agent_cfg['prompt']        — explicit override (absolute or relative
                                    to workspace.path/config)
    2. workspace.path/config/prompts/<role>.md
    3. bundled prompts/<role>.md
    """
    explicit = agent_cfg.get("prompt")
    if explicit:
        p = Path(explicit)
        if not p.is_absolute() and getattr(workspace, "path", None):
            p = Path(workspace.path) / "config" / explicit
        if not p.exists():
            raise DispatchConfigError(f"prompt path does not exist: {p}")
        return p

    if getattr(workspace, "path", None):
        ws_override = Path(workspace.path) / "config" / "prompts" / f"{role}.md"
        if ws_override.exists():
            return ws_override

    bundled = _BUNDLED_PROMPTS / f"{role}.md"
    if not bundled.exists():
        raise DispatchConfigError(
            f"no prompt template found for role {role!r} "
            f"(checked workspace override and {bundled})"
        )
    return bundled


def resolve_inline_prompt_template(
    *,
    workspace,
    role: str,
    agent_cfg: dict,
) -> str | None:
    """Return an inline prompt override from ``workspace.config.prompts``.

    Explicit file paths still win. Inline prompts remain available for role-
    specific overrides, but the shared ``WORKFLOW.md`` body now flows through
    ``workflow-policy`` instead.
    """
    if agent_cfg.get("prompt"):
        return None
    prompts = (workspace.config or {}).get("prompts") or {}
    if not prompts:
        return None
    template = prompts.get(role)
    if template is None:
        return None
    if not isinstance(template, str):
        raise DispatchConfigError(
            f"workspace.config.prompts[{role!r}] must be a string"
        )
    return template


def resolve_workflow_policy(*, workspace) -> str | None:
    policy = (workspace.config or {}).get("workflow-policy")
    if policy is None:
        return None
    if not isinstance(policy, str):
        raise DispatchConfigError("workspace.config['workflow-policy'] must be a string")
    return policy


def _compose_with_workflow_policy(prompt_text: str, workflow_policy: str | None) -> str:
    policy = str(workflow_policy or "").strip()
    if not policy:
        return prompt_text
    return "\n".join(
        [
            "# Shared Workflow Policy",
            "",
            policy,
            "",
            "# Role-Specific Instructions",
            "",
            prompt_text.lstrip(),
        ]
    )


def _materialize_prompt(*, worktree: Path, role: str, tier: str | None, rendered_text: str) -> Path:
    """Write the already-rendered prompt to a deterministic file under
    <worktree>/.daedalus/dispatch/, return the path."""
    out_dir = Path(worktree) / ".daedalus" / "dispatch"
    out_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(rendered_text.encode("utf-8")).hexdigest()[:12]
    label = f"{role}-{tier}" if tier else role
    out = out_dir / f"{label}-{digest}.txt"
    out.write_text(rendered_text, encoding="utf-8")
    return out


def _resolve_command(*, agent_cfg: dict, runtime_cfg: dict) -> list[str] | None:
    """Agent command wins; falls back to runtime profile command; None if neither."""
    cmd = agent_cfg.get("command")
    if cmd:
        return list(cmd)
    cmd = runtime_cfg.get("command")
    if cmd:
        return list(cmd)
    return None


def _substitute(argv: list[str], values: dict[str, str]) -> list[str]:
    """Replace {key} placeholders in each argv element. Unknown placeholders
    pass through unchanged so adapters can interpret them."""
    out = []
    for a in argv:
        s = a
        for k, v in values.items():
            s = s.replace("{" + k + "}", v)
        out.append(s)
    return out


def dispatch_agent(
    *,
    workspace,
    role: str,
    prompt_kwargs: dict[str, Any] | None = None,
    session_name: str,
    worktree: Path,
    tier: str | None = None,
    extra_placeholders: dict[str, str] | None = None,
) -> str:
    """Resolve config, load + render prompt template, run the agent, return stdout.

    Behavior:
      - Resolves agent role (tiered for 'coder', flat otherwise).
      - Resolves runtime via ``workspace.runtime(<name>)``.
      - Resolves command (agent override -> runtime default -> None).
      - Resolves prompt template text (agent.prompt file -> inline
        ``workspace.config.prompts`` -> workspace override file -> bundled),
        then renders via ``.format(**prompt_kwargs)``.
      - If a command is present: materializes the rendered text to a file,
        substitutes placeholders, invokes ``runtime.run_command(...)``.
      - If no command is present: invokes ``runtime.run_prompt(...)`` with the
        rendered prompt as a string (preserves pre-Phase-A behavior).
    """
    cfg = _agent_cfg(workspace, role, tier)
    runtime_name = cfg.get("runtime")
    if not runtime_name:
        raise DispatchConfigError(f"agent {role!r}/{tier!r} has no runtime")
    runtime = workspace.runtime(runtime_name)
    runtimes_cfg = (workspace.config or {}).get("runtimes") or {}
    runtime_cfg = runtimes_cfg.get(runtime_name) or {}
    model = cfg.get("model") or ""

    # Resolve + load + render the template (the dispatcher, not the caller,
    # owns this so workspace/agent overrides actually take effect).
    inline_template = resolve_inline_prompt_template(
        workspace=workspace, role=role, agent_cfg=cfg,
    )
    if inline_template is not None:
        template_text = inline_template
    else:
        template_path = resolve_prompt_template_path(
            workspace=workspace, role=role, agent_cfg=cfg,
        )
        template_text = template_path.read_text(encoding="utf-8")
    template_text = _compose_with_workflow_policy(
        template_text,
        resolve_workflow_policy(workspace=workspace),
    )
    rendered_text = template_text.format(**(prompt_kwargs or {}))

    command = _resolve_command(agent_cfg=cfg, runtime_cfg=runtime_cfg)

    if command is None:
        # Legacy path: runtime owns the invocation.
        return runtime.run_prompt(
            worktree=worktree,
            session_name=session_name,
            prompt=rendered_text,
            model=model,
        )

    prompt_path = _materialize_prompt(
        worktree=worktree, role=role, tier=tier, rendered_text=rendered_text,
    )
    placeholders = {
        "model": model,
        "prompt_path": str(prompt_path),
        "worktree": str(worktree),
        "session_name": session_name,
    }
    if extra_placeholders:
        placeholders.update(extra_placeholders)

    argv = _substitute(command, placeholders)
    return runtime.run_command(worktree=worktree, command_argv=argv)
