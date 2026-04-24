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



def pick_next_lane_issue(items: list[dict[str, Any]] | None, *, active_lane_label: str = "active-lane") -> dict[str, Any] | None:
    candidates = []
    for item in items or []:
        if active_lane_label in issue_label_names(item):
            continue
        priority = parse_priority_from_title(item.get("title"))
        candidates.append((priority, int(item.get("number")), item))
    if not candidates:
        return None
    candidates.sort(key=lambda entry: (entry[0], entry[1]))
    return candidates[0][2]



def pick_next_lane_issue_from_repo(
    repo_path: Path,
    *,
    run_json: Callable[..., list[dict[str, Any]]],
    active_lane_label: str = "active-lane",
) -> dict[str, Any] | None:
    items = run_json(
        ["gh", "issue", "list", "--state", "open", "--limit", "100", "--json", "number,title,url,labels"],
        cwd=repo_path,
    )
    return pick_next_lane_issue(items, active_lane_label=active_lane_label)



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
