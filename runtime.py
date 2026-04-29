"""Repo-root wrapper for the official Hermes plugin layout."""

try:
    from .daedalus.runtime import *  # noqa: F401,F403
    from .daedalus.runtime import main as _main
except ImportError:
    from daedalus.runtime import *  # noqa: F401,F403
    from daedalus.runtime import main as _main


if __name__ == "__main__":
    raise SystemExit(_main())
