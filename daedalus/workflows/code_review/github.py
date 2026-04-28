from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable


"""YoYoPod Core GitHub integration helpers.

This slice extracts project-specific GitHub helpers so wrapper compatibility code
can delegate deterministic issue/label selection and simple GH command assembly
into the adapter layer.
"""


PRIORITY_RE = re.compile(r"\[P(\d+)\]")


def issue_label_names(issue: dict[str, Any] | None) -> set[str]:
    labels = (issue or {}).get("labels") or []
    names: set[str] = set()
    for label in labels:
        if isinstance(label, dict):
            name = str(label.get("name") or "").strip().lower()
            if name:
                names.add(name)
        elif isinstance(label, str):
            name = label.strip().lower()
            if name:
                names.add(name)
    return names



def parse_priority_from_title(title: str | None) -> int:
    match = PRIORITY_RE.search(title or "")
    return int(match.group(1)) if match else 999


def _iso_to_unix(iso_str: str) -> int:
    """Return Unix epoch seconds, or 0 if unparseable. Used for tiebreak sort keys."""
    if not iso_str:
        return 0
    try:
        from datetime import datetime
        return int(datetime.fromisoformat(iso_str.replace("Z", "+00:00")).timestamp())
    except (ValueError, TypeError):
        return 0



def pick_next_lane_issue(
    items: list[dict[str, Any]] | None,
    *,
    active_lane_label: str = "active-lane",
    lane_selection_cfg: dict[str, Any] | None = None,
    rng=None,
) -> dict[str, Any] | None:
    """Pick the next open issue eligible for promotion to active lane.

    ``lane_selection_cfg`` is the parsed config from
    :mod:`workflows.code_review.lane_selection.parse_config`. When ``None``,
    we synthesize a back-compat config (no required labels, the active-lane
    label auto-excluded, oldest tiebreak) preserving pre-issue-#2 behavior.

    ``rng`` is injectable for the ``tiebreak: random`` path so tests are
    deterministic. Defaults to a fresh ``random.Random()``.
    """
    import random

    back_compat = lane_selection_cfg is None

    if lane_selection_cfg is None:
        try:
            from .lane_selection import parse_config as _parse
        except ImportError:
            import importlib.util as _ilu
            _spec = _ilu.spec_from_file_location(
                "daedalus_lane_selection_for_picker",
                Path(__file__).resolve().parent / "lane_selection.py",
            )
            _mod = _ilu.module_from_spec(_spec)
            _spec.loader.exec_module(_mod)
            _parse = _mod.parse_config
        lane_selection_cfg = _parse(workflow_yaml={}, active_lane_label=active_lane_label)

    require = set(lane_selection_cfg.get("require-labels") or [])
    any_of = set(lane_selection_cfg.get("allow-any-of") or [])
    exclude = set(lane_selection_cfg.get("exclude-labels") or [])
    priority = lane_selection_cfg.get("priority") or []
    tiebreak = lane_selection_cfg.get("tiebreak") or "oldest"
    has_label_priority = bool(priority)

    candidates = []
    for item in items or []:
        labels = issue_label_names(item)
        if labels & exclude:
            continue
        if require and not require.issubset(labels):
            continue
        if any_of and not (labels & any_of):
            continue

        label_bucket = len(priority)
        for idx, plabel in enumerate(priority):
            if plabel in labels:
                label_bucket = idx
                break

        candidates.append({
            "label_bucket": label_bucket,
            "title_pri": parse_priority_from_title(item.get("title")),
            "issue": item,
        })

    if not candidates:
        return None

    def _sort_key(c):
        issue = c["issue"]
        if back_compat:
            # Pre-issue-#2 behavior: title_pri then issue_number, no time
            # component. Adding `createdAt` to the gh JSON output must not
            # shift no-config ordering for repos where createdAt and issue
            # numbering diverge (e.g. transferred/imported issues).
            return (c["title_pri"], int(issue.get("number") or 0))
        created = issue.get("createdAt") or ""
        if tiebreak == "newest":
            time_key = -_iso_to_unix(created)
        elif tiebreak == "oldest":
            time_key = _iso_to_unix(created)
        else:  # random — placeholder, picker handles random separately
            time_key = 0
        # Build the full sort tuple. Primary = label or title priority depending
        # on whether label priority is configured.
        if has_label_priority:
            return (c["label_bucket"], time_key, c["title_pri"], int(issue.get("number") or 0))
        return (c["title_pri"], time_key, int(issue.get("number") or 0))

    if tiebreak == "random":
        # Identify the top primary-bucket, then pick uniformly from it.
        # When label priority is configured, also match the tertiary
        # title_pri so random doesn't override title-priority ordering
        # within a label bucket.
        candidates.sort(key=_sort_key)
        primary_top = _sort_key(candidates[0])[0]
        if has_label_priority:
            title_top = _sort_key(candidates[0])[2]  # title_pri at index 2
            top_bucket = [
                c for c in candidates
                if _sort_key(c)[0] == primary_top
                and _sort_key(c)[2] == title_top
            ]
        else:
            top_bucket = [c for c in candidates if _sort_key(c)[0] == primary_top]
        rng = rng or random.Random()
        return rng.choice(top_bucket)["issue"]

    candidates.sort(key=_sort_key)
    return candidates[0]["issue"]



def pick_next_lane_issue_from_repo(
    repo_path: Path,
    *,
    run_json: Callable[..., list[dict[str, Any]]],
    active_lane_label: str = "active-lane",
    lane_selection_cfg: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    items = run_json(
        ["gh", "issue", "list", "--state", "open", "--limit", "100",
         "--json", "number,title,url,labels,createdAt"],
        cwd=repo_path,
    )
    return pick_next_lane_issue(
        items,
        active_lane_label=active_lane_label,
        lane_selection_cfg=lane_selection_cfg,
    )



def get_active_lane_from_repo(
    repo_path: Path,
    *,
    run_json: Callable[..., list[dict[str, Any]]],
    active_lane_label: str = "active-lane",
) -> dict[str, Any] | None:
    items = run_json(
        [
            "gh",
            "issue",
            "list",
            "--state",
            "open",
            "--limit",
            "200",
            "--json",
            "number,title,url,labels,assignees,updatedAt",
        ],
        cwd=repo_path,
    )
    items = [
        item
        for item in items
        if active_lane_label in issue_label_names(item)
    ]
    if not items:
        return None
    if len(items) > 1:
        return {
            "error": "multiple-active-lanes",
            "issues": [
                {"number": item.get("number"), "title": item.get("title"), "url": item.get("url")}
                for item in items
            ],
        }
    return items[0]



def get_open_pr_for_issue(
    issue_number: int | None,
    *,
    repo_path: Path,
    run_json: Callable[..., list[dict[str, Any]]],
    issue_number_from_branch_fn: Callable[[str | None], int | None],
) -> dict[str, Any] | None:
    prs = run_json(
        [
            "gh",
            "pr",
            "list",
            "--state",
            "open",
            "--limit",
            "50",
            "--json",
            "number,title,url,headRefName,headRefOid,isDraft,updatedAt",
        ],
        cwd=repo_path,
    )
    if issue_number is None:
        return None
    for pr in prs:
        if issue_number_from_branch_fn(pr.get("headRefName")) == issue_number:
            return pr
    return None



def get_issue_details(
    issue_number: int | None,
    *,
    repo_path: Path,
    run_json: Callable[..., dict[str, Any]],
) -> dict[str, Any] | None:
    if issue_number is None:
        return None
    try:
        return run_json(
            ["gh", "issue", "view", str(issue_number), "--json", "number,title,url,body"],
            cwd=repo_path,
        )
    except Exception:
        return None



def issue_add_label(issue_number: int | None, label: str, *, repo_path: Path, run: Callable[..., Any]) -> bool:
    if issue_number is None:
        return False
    try:
        run(["gh", "issue", "edit", str(issue_number), "--add-label", label], cwd=repo_path)
        return True
    except Exception:
        return False



def issue_remove_label(issue_number: int | None, label: str, *, repo_path: Path, run: Callable[..., Any]) -> bool:
    if issue_number is None:
        return False
    try:
        run(["gh", "issue", "edit", str(issue_number), "--remove-label", label], cwd=repo_path)
        return True
    except Exception:
        return False



def issue_comment(issue_number: int | None, body: str, *, repo_path: Path, run: Callable[..., Any]) -> bool:
    if issue_number is None:
        return False
    try:
        run(["gh", "issue", "comment", str(issue_number), "--body", body], cwd=repo_path)
        return True
    except Exception:
        return False



def issue_close(issue_number: int | None, comment: str | None = None, *, repo_path: Path, run: Callable[..., Any]) -> bool:
    if issue_number is None:
        return False
    command = ["gh", "issue", "close", str(issue_number)]
    if comment:
        command.extend(["--comment", comment])
    try:
        run(command, cwd=repo_path)
        return True
    except Exception:
        return False
