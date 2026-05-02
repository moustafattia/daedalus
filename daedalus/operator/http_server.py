"""Operator HTTP status server placeholder for the flat agentic workflow."""
from __future__ import annotations


def start_server(*args, **kwargs):
    raise RuntimeError("legacy workflow HTTP server was removed; use workflow: agentic CLI status instead")


__all__ = ["start_server"]
