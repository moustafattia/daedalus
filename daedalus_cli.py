"""Repo-root wrapper for the official Hermes plugin layout."""

try:
    from .daedalus.daedalus_cli import *  # noqa: F401,F403
    from .daedalus.daedalus_cli import execute_raw_args as _execute_raw_args
except ImportError:
    from daedalus.daedalus_cli import *  # noqa: F401,F403
    from daedalus.daedalus_cli import execute_raw_args as _execute_raw_args


if __name__ == "__main__":
    import sys

    result = _execute_raw_args(" ".join(sys.argv[1:]))
    print(result)
    sys.exit(0 if not result.startswith("daedalus error:") else 1)
