import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent

# Put the plugin dir on sys.path so absolute imports of sibling top-level
# packages (workflows/, etc.) resolve when Hermes loads us as a package.
# Hermes' plugin loader puts ~/.hermes/plugins/ on sys.path so this package
# (`daedalus`) is importable, but doesn't add this directory itself — so
# tools.py's `from workflows.code_review.paths import ...` fails without
# this bootstrap. Same self-bootstrap pattern as workflows/__main__.py.
_PLUGIN_DIR_STR = str(PLUGIN_DIR)
if _PLUGIN_DIR_STR not in sys.path:
    sys.path.insert(0, _PLUGIN_DIR_STR)


try:
    from .schemas import setup_cli
    from .tools import execute_raw_args, execute_workflow_command
except ImportError:
    def _load_local_module(module_name: str):
        module_path = PLUGIN_DIR / f"{module_name}.py"
        spec = spec_from_file_location(f"daedalus_{module_name}", module_path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"unable to load {module_name} from {module_path}")
        module = module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    setup_cli = _load_local_module("schemas").setup_cli
    _tools_module = _load_local_module("tools")
    execute_raw_args = _tools_module.execute_raw_args
    execute_workflow_command = _tools_module.execute_workflow_command


def register(ctx):
    ctx.register_command(
        "daedalus",
        execute_raw_args,
        description="Operate the Daedalus workflow engine from the current Hermes session.",
    )
    ctx.register_command(
        "workflow",
        execute_workflow_command,
        description="Run a workflow's CLI (e.g. /workflow code-review status).",
    )
    ctx.register_cli_command(
        name="daedalus",
        help="Operate the Daedalus workflow engine.",
        setup_fn=setup_cli,
        description="Daedalus workflow engine control surface.",
    )

    skill_md = PLUGIN_DIR / "skills" / "operator" / "SKILL.md"
    if skill_md.exists():
        ctx.register_skill("operator", skill_md, description="Operate the Daedalus engine.")
