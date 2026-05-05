"""Hermes registration for the Sprints plugin."""

from __future__ import annotations

import os
from pathlib import Path

from .install_checks import check_install_readiness, register_install_help


def setup_cli(subparser):
    from sprints_cli.commands import configure_subcommands

    return configure_subcommands(subparser)


def register(ctx):
    report = check_install_readiness(
        ctx,
        auto_install=os.getenv("SPRINTS_SKIP_REGISTER_PREFLIGHT", "") != "1",
    )
    if not report.ok:
        register_install_help(ctx, report)
        return

    import sprints
    from sprints_cli.commands import execute_raw_args, execute_workflow_command

    ctx.register_command(
        "sprints",
        execute_raw_args,
        description="Operate the Sprints workflow engine from the current Hermes session.",
    )
    ctx.register_command(
        "workflow",
        execute_workflow_command,
        description="Run a workflow's CLI (e.g. /workflow change-delivery status).",
    )
    ctx.register_cli_command(
        name="sprints",
        help="Operate the Sprints workflow engine.",
        setup_fn=setup_cli,
        description="Sprints workflow engine control surface.",
    )

    skills_dir = Path(sprints.__file__).resolve().parent / "skills"
    for skill_md in sorted(skills_dir.glob("*/SKILL.md")):
        ctx.register_skill(
            skill_md.parent.name,
            skill_md,
            description=_skill_description(skill_md),
        )


def _skill_description(skill_md: Path) -> str:
    try:
        text = skill_md.read_text(encoding="utf-8")
    except OSError:
        return f"Sprints {skill_md.parent.name} skill."
    in_front_matter = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == "---":
            in_front_matter = not in_front_matter
            continue
        if in_front_matter and stripped.startswith("description:"):
            value = stripped.split(":", 1)[1].strip()
            return value or f"Sprints {skill_md.parent.name} skill."
    return f"Sprints {skill_md.parent.name} skill."
