from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


try:
    from .daedalus_cli import configure_subcommands
except ImportError:
    module_path = Path(__file__).resolve().parent / "daedalus_cli.py"
    spec = spec_from_file_location("daedalus_cli_for_schemas", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load cli from {module_path}")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    configure_subcommands = module.configure_subcommands


def setup_cli(subparser):
    configure_subcommands(subparser)
