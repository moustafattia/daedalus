"""Lane-selection config parser.

Synthesizes a fully-populated config dict from the (optional) ``lane-selection:``
block in workflow.yaml. Defaults preserve current behavior exactly so workspaces
without the block see no change in promotion semantics.
"""
from __future__ import annotations

from typing import Any, Mapping


_VALID_TIEBREAKS = {"oldest", "newest", "random"}


def _norm_list(values) -> list[str]:
    """Lowercase + strip + drop empties. Preserves caller order."""
    out: list[str] = []
    seen: set[str] = set()
    for v in values or []:
        if not isinstance(v, str):
            continue
        s = v.strip().lower()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def parse_config(
    *,
    workflow_yaml: Mapping[str, Any],
    active_lane_label: str,
) -> dict[str, Any]:
    """Return a fully-populated lane-selection config.

    Defaults (when the block or any field is absent):

      - require-labels: []
      - allow-any-of:   []
      - exclude-labels: [<active-lane-label>]   (auto-injected — never picked)
      - priority:       []
      - tiebreak:       "oldest"

    All label strings are normalized to lowercase to keep set-comparisons in
    the picker uniform with our existing ``issue_label_names`` helper, which
    lowercases on read.
    """
    block = (workflow_yaml or {}).get("lane-selection") or {}

    require = _norm_list(block.get("require-labels"))
    any_of = _norm_list(block.get("allow-any-of"))
    user_exclude = _norm_list(block.get("exclude-labels"))
    priority = _norm_list(block.get("priority"))

    # Auto-inject the active-lane label so the picker can never pick a
    # currently-active lane, even when the operator forgets to list it.
    auto_exclude = (active_lane_label or "").strip().lower()
    exclude = list(user_exclude)
    if auto_exclude and auto_exclude not in exclude:
        exclude.append(auto_exclude)

    raw_tiebreak = block.get("tiebreak") or "oldest"
    tiebreak = raw_tiebreak if raw_tiebreak in _VALID_TIEBREAKS else "oldest"

    return {
        "require-labels": require,
        "allow-any-of": any_of,
        "exclude-labels": exclude,
        "priority": priority,
        "tiebreak": tiebreak,
    }
