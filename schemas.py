"""Repo-root wrapper for the official Hermes plugin layout."""

try:
    from .daedalus.schemas import *  # noqa: F401,F403
except ImportError:
    from daedalus.schemas import *  # noqa: F401,F403
