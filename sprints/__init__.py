import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent

# Put the plugin dir on sys.path so absolute imports of sibling top-level
# packages (workflows/, runtimes/, trackers/) resolve when Hermes loads us as a
# package. Append instead of prepending: Hermes has its own top-level packages,
# and plugin modules must never shadow them during agent startup.
_PLUGIN_DIR_STR = str(PLUGIN_DIR)
if _PLUGIN_DIR_STR not in sys.path:
    sys.path.append(_PLUGIN_DIR_STR)


try:
    from .schemas import setup_cli
    from . import sprints_cli as _cli

    execute_raw_args = _cli.execute_raw_args
    execute_workflow_command = _cli.execute_workflow_command
    sys.modules.setdefault(f"{__name__}.tools", _cli)
except ImportError:

    def _load_local_module(module_name: str):
        module_path = PLUGIN_DIR / f"{module_name}.py"
        spec = spec_from_file_location(f"sprints_{module_name}", module_path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"unable to load {module_name} from {module_path}")
        module = module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    setup_cli = _load_local_module("schemas").setup_cli
    _cli_module = _load_local_module("sprints_cli")
    execute_raw_args = _cli_module.execute_raw_args
    execute_workflow_command = _cli_module.execute_workflow_command


def register(ctx):
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

    skill_md = PLUGIN_DIR / "skills" / "operator" / "SKILL.md"
    if skill_md.exists():
        ctx.register_skill(
            "operator", skill_md, description="Operate the Sprints engine."
        )
