"""Phase B tests: external reviewer pluggability."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest


def test_reviewer_module_exposes_protocol_and_registry():
    from workflows.code_review.reviewers import Reviewer, ReviewerContext, register, build_reviewer, _REVIEWER_KINDS
    assert callable(register)
    assert callable(build_reviewer)
    assert isinstance(_REVIEWER_KINDS, dict)


def test_build_reviewer_unknown_kind_raises():
    from workflows.code_review.reviewers import build_reviewer

    with pytest.raises(ValueError, match="unknown"):
        build_reviewer({"kind": "made-up"}, ws_context=MagicMock())
