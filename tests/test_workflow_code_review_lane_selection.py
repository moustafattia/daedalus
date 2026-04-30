"""Multi-axis lane selection: require / allow-any-of / exclude / priority / tiebreak."""
import importlib.util
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1] / "daedalus"


def load_module(module_name: str, relative_path: str):
    module_path = REPO_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _gh():
    return load_module("daedalus_workflow_github_test", "workflows/change_delivery/github.py")


def _ls():
    return load_module("daedalus_workflow_lane_selection_test_2", "workflows/change_delivery/lane_selection.py")


def _issue(number, labels=None, title=None, created_at=None):
    """Build a fake gh-issue dict matching `gh issue list --json`'s shape."""
    return {
        "number": number,
        "title": title or f"Issue {number}",
        "labels": [{"name": l} for l in (labels or [])],
        "createdAt": created_at,
    }


def _empty_cfg(active_lane_label="active-lane"):
    """Synthesized default config — same shape parse_config returns."""
    return _ls().parse_config(workflow_yaml={}, active_lane_label=active_lane_label)


# ─── Back-compat ─────────────────────────────────────────────────────

def test_back_compat_picks_lowest_p_priority_then_lowest_number():
    """No lane-selection block → pure title-priority + issue-number sort (current behavior)."""
    gh = _gh()
    items = [
        _issue(10, title="Issue 10"),               # title priority 999 (no [P])
        _issue(20, title="[P2] medium", labels=[]),
        _issue(30, title="[P1] high"),
    ]
    chosen = gh.pick_next_lane_issue(items, lane_selection_cfg=_empty_cfg())
    assert chosen["number"] == 30  # [P1] wins


def test_back_compat_excludes_active_lane_label():
    gh = _gh()
    items = [
        _issue(10, labels=["active-lane"], title="[P1] in progress"),
        _issue(20, title="[P2] candidate"),
    ]
    chosen = gh.pick_next_lane_issue(items, lane_selection_cfg=_empty_cfg())
    assert chosen["number"] == 20  # 10 excluded


# ─── require-labels (AND) ────────────────────────────────────────────

def test_require_labels_AND_combination():
    gh, ls = _gh(), _ls()
    cfg = ls.parse_config(
        workflow_yaml={"lane-selection": {"require-labels": ["needs-review", "ready"]}},
        active_lane_label="active-lane",
    )
    items = [
        _issue(10, labels=["needs-review"]),                    # missing ready
        _issue(20, labels=["needs-review", "ready"]),
        _issue(30, labels=["needs-review", "ready", "extra"]),
    ]
    # Both 20 and 30 qualify; oldest tiebreak → lowest number = 20
    chosen = gh.pick_next_lane_issue(items, lane_selection_cfg=cfg)
    assert chosen["number"] == 20


def test_require_labels_returns_none_when_no_match():
    gh, ls = _gh(), _ls()
    cfg = ls.parse_config(
        workflow_yaml={"lane-selection": {"require-labels": ["needs-review"]}},
        active_lane_label="active-lane",
    )
    items = [_issue(10, labels=["other"])]
    assert gh.pick_next_lane_issue(items, lane_selection_cfg=cfg) is None


# ─── allow-any-of (OR) ────────────────────────────────────────────

def test_allow_any_of_OR_combination():
    gh, ls = _gh(), _ls()
    cfg = ls.parse_config(
        workflow_yaml={"lane-selection": {"allow-any-of": ["urgent", "wip-codex"]}},
        active_lane_label="active-lane",
    )
    items = [
        _issue(10, labels=["other"]),       # neither
        _issue(20, labels=["wip-codex"]),
        _issue(30, labels=["urgent"]),
    ]
    chosen = gh.pick_next_lane_issue(items, lane_selection_cfg=cfg)
    assert chosen["number"] == 20  # oldest of 20/30 wins


# ─── exclude-labels ────────────────────────────────────────────

def test_exclude_labels_filters_out_blocked():
    gh, ls = _gh(), _ls()
    cfg = ls.parse_config(
        workflow_yaml={"lane-selection": {"exclude-labels": ["blocked"]}},
        active_lane_label="active-lane",
    )
    items = [
        _issue(10, labels=["blocked"]),
        _issue(20),
    ]
    chosen = gh.pick_next_lane_issue(items, lane_selection_cfg=cfg)
    assert chosen["number"] == 20


def test_exclude_wins_over_require():
    """If an issue has BOTH a required label AND an excluded label, exclude wins."""
    gh, ls = _gh(), _ls()
    cfg = ls.parse_config(
        workflow_yaml={"lane-selection": {"require-labels": ["needs-review"], "exclude-labels": ["blocked"]}},
        active_lane_label="active-lane",
    )
    items = [
        _issue(10, labels=["needs-review", "blocked"]),
        _issue(20, labels=["needs-review"]),
    ]
    chosen = gh.pick_next_lane_issue(items, lane_selection_cfg=cfg)
    assert chosen["number"] == 20


# ─── priority ────────────────────────────────────────────

def test_priority_critical_beats_high():
    gh, ls = _gh(), _ls()
    cfg = ls.parse_config(
        workflow_yaml={"lane-selection": {"priority": ["severity:critical", "severity:high"]}},
        active_lane_label="active-lane",
    )
    items = [
        _issue(10, labels=["severity:high"]),
        _issue(20, labels=["severity:critical"]),
    ]
    chosen = gh.pick_next_lane_issue(items, lane_selection_cfg=cfg)
    assert chosen["number"] == 20


def test_priority_unmatched_issues_fall_to_bottom_bucket():
    gh, ls = _gh(), _ls()
    cfg = ls.parse_config(
        workflow_yaml={"lane-selection": {"priority": ["severity:critical"]}},
        active_lane_label="active-lane",
    )
    items = [
        _issue(10),                                # no priority label
        _issue(20, labels=["severity:critical"]),
    ]
    chosen = gh.pick_next_lane_issue(items, lane_selection_cfg=cfg)
    assert chosen["number"] == 20


def test_priority_multiple_labels_picks_highest():
    """When an issue has multiple priority labels, the highest-ranked wins."""
    gh, ls = _gh(), _ls()
    cfg = ls.parse_config(
        workflow_yaml={"lane-selection": {"priority": ["severity:critical", "severity:high"]}},
        active_lane_label="active-lane",
    )
    items = [
        _issue(10, labels=["severity:high"]),
        _issue(20, labels=["severity:high", "severity:critical"]),  # has both
    ]
    chosen = gh.pick_next_lane_issue(items, lane_selection_cfg=cfg)
    assert chosen["number"] == 20  # picks via critical


# ─── tiebreak ────────────────────────────────────────────

def test_tiebreak_oldest_uses_created_at_asc():
    gh, ls = _gh(), _ls()
    cfg = ls.parse_config(
        workflow_yaml={"lane-selection": {"tiebreak": "oldest"}},
        active_lane_label="active-lane",
    )
    items = [
        _issue(20, created_at="2026-04-26T12:00:00Z"),
        _issue(10, created_at="2026-04-25T12:00:00Z"),  # older
    ]
    chosen = gh.pick_next_lane_issue(items, lane_selection_cfg=cfg)
    assert chosen["number"] == 10


def test_tiebreak_newest_uses_created_at_desc():
    gh, ls = _gh(), _ls()
    cfg = ls.parse_config(
        workflow_yaml={"lane-selection": {"tiebreak": "newest"}},
        active_lane_label="active-lane",
    )
    items = [
        _issue(20, created_at="2026-04-26T12:00:00Z"),  # newer
        _issue(10, created_at="2026-04-25T12:00:00Z"),
    ]
    chosen = gh.pick_next_lane_issue(items, lane_selection_cfg=cfg)
    assert chosen["number"] == 20


def test_tiebreak_random_with_seeded_rng_is_deterministic():
    gh, ls = _gh(), _ls()
    import random
    cfg = ls.parse_config(
        workflow_yaml={"lane-selection": {"tiebreak": "random"}},
        active_lane_label="active-lane",
    )
    items = [_issue(10), _issue(20), _issue(30)]
    rng = random.Random(42)
    chosen = gh.pick_next_lane_issue(items, lane_selection_cfg=cfg, rng=rng)
    # Don't assert a specific number — just that the function returns something
    # from the candidate set, and that the same seed is reproducible.
    assert chosen["number"] in {10, 20, 30}
    rng2 = random.Random(42)
    chosen2 = gh.pick_next_lane_issue(items, lane_selection_cfg=cfg, rng=rng2)
    assert chosen["number"] == chosen2["number"]


# ─── title-priority interaction ────────────────────────────────────────────

def test_title_priority_remains_primary_when_label_priority_empty():
    gh, ls = _gh(), _ls()
    cfg = ls.parse_config(workflow_yaml={"lane-selection": {}}, active_lane_label="active-lane")
    items = [
        _issue(10, title="[P3] low"),
        _issue(20, title="[P1] urgent"),
    ]
    chosen = gh.pick_next_lane_issue(items, lane_selection_cfg=cfg)
    assert chosen["number"] == 20  # [P1] wins


def test_title_priority_demoted_to_tertiary_when_label_priority_active():
    """When label priority is configured, it's primary; title priority becomes a tertiary tiebreak."""
    gh, ls = _gh(), _ls()
    cfg = ls.parse_config(
        workflow_yaml={"lane-selection": {"priority": ["severity:critical"]}},
        active_lane_label="active-lane",
    )
    items = [
        _issue(10, labels=["severity:critical"], title="[P3] low priority title"),
        _issue(20, title="[P1] high title but no severity label"),
    ]
    # 10 has the critical label → wins despite worse title priority
    chosen = gh.pick_next_lane_issue(items, lane_selection_cfg=cfg)
    assert chosen["number"] == 10


# ─── back-compat function signature ────────────────────────────────────────────

def test_pick_next_lane_issue_back_compat_positional_call():
    """Existing callers using `pick_next_lane_issue(items, active_lane_label='X')` keep working."""
    gh = _gh()
    items = [
        _issue(10, labels=["active-lane"]),
        _issue(20),
    ]
    # Old-style call — no lane_selection_cfg passed
    chosen = gh.pick_next_lane_issue(items, active_lane_label="active-lane")
    assert chosen["number"] == 20


# ─── regression: P5.1 back-compat ignores createdAt ────────────────────────

def test_back_compat_no_cfg_ignores_createdAt_uses_issue_number():
    """When `lane_selection_cfg` is None (no lane-selection block at all),
    the picker must rank by (title_pri, issue_number) — NOT by createdAt.

    Adding `createdAt` to the gh JSON output must not shift no-config
    ordering for repos where createdAt and issue numbering diverge
    (transferred / imported issues).
    """
    gh = _gh()
    # Issue 10 was created LATER than issue 20 (transferred / imported).
    # Both have the same title priority.
    items = [
        _issue(10, title="[P1] later created", created_at="2026-04-26T12:00:00Z"),
        _issue(20, title="[P1] earlier created", created_at="2026-04-25T12:00:00Z"),
    ]
    # Pre-issue-#2 behavior: lower issue_number wins (10), regardless of createdAt.
    chosen = gh.pick_next_lane_issue(items)  # lane_selection_cfg defaults to None
    assert chosen["number"] == 10


# ─── regression: P5.2 random tiebreak respects tertiary title_pri ──────────

def test_random_tiebreak_respects_title_priority_within_label_bucket():
    """When label-priority is configured AND tiebreak=random, the random pool
    is narrowed to (label_bucket, title_pri) tied set. So `[P1]` strictly beats
    `[P3]` within the same label bucket, even under random tiebreak.
    """
    gh, ls = _gh(), _ls()
    import random as _random

    cfg = ls.parse_config(
        workflow_yaml={
            "lane-selection": {
                "priority": ["severity:critical"],
                "tiebreak": "random",
            }
        },
        active_lane_label="active-lane",
    )
    items = [
        _issue(10, labels=["severity:critical"], title="[P3] lower title pri"),
        _issue(20, labels=["severity:critical"], title="[P1] higher title pri"),
    ]
    # Run many times with different seeds — only #20 should ever come out.
    seen = set()
    for seed in range(200):
        rng = _random.Random(seed)
        chosen = gh.pick_next_lane_issue(items, lane_selection_cfg=cfg, rng=rng)
        seen.add(chosen["number"])
    assert seen == {20}, f"random tiebreak should never select [P3] over [P1]; saw: {seen}"
