from .tools import configure_subcommands


def setup_cli(subparser):
    configure_subcommands(subparser)
