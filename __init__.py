from pathlib import Path

from .schemas import setup_cli
from .tools import execute_raw_args


PLUGIN_DIR = Path(__file__).resolve().parent


def register(ctx):
    ctx.register_command(
        "relay",
        execute_raw_args,
        description="Operate the YoYoPod Relay shadow runtime from the current Hermes session.",
    )
    ctx.register_cli_command(
        name="relay",
        help="Operate the YoYoPod Relay shadow runtime.",
        setup_fn=setup_cli,
        description="YoYoPod Relay project control surface.",
    )

    skill_md = PLUGIN_DIR / "skills" / "operator" / "SKILL.md"
    if skill_md.exists():
        ctx.register_skill("operator", skill_md, description="Operate the YoYoPod Relay project plugin.")
