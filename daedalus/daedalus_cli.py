"""Public Daedalus CLI facade.

The command implementation lives in :mod:`cli.commands`; this root module
stays as the stable plugin/script entrypoint.
"""

try:
    from .cli.commands import *  # noqa: F401,F403
    from .cli.commands import execute_raw_args as _execute_raw_args
except ImportError:
    from cli.commands import *  # noqa: F401,F403
    from cli.commands import execute_raw_args as _execute_raw_args


if __name__ == "__main__":
    import sys

    result = _execute_raw_args(" ".join(sys.argv[1:]))
    print(result)
    sys.exit(0 if not result.startswith("daedalus error:") else 1)

