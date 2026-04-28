from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


try:
    from .tools import configure_subcommands
except ImportError:
    module_path = Path(__file__).resolve().parent / "tools.py"
    spec = spec_from_file_location("daedalus_tools_for_schemas", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load tools from {module_path}")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    configure_subcommands = module.configure_subcommands


def setup_cli(subparser):
    configure_subcommands(subparser)
