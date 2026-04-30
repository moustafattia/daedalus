from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from . import (
    DEFAULT_ACTIVE_STATES,
    DEFAULT_TERMINAL_STATES,
    TrackerConfigError,
    chunk,
    http_post_json,
    issue_priority_sort_key,
    linear_api_key,
    linear_endpoint,
    linear_project_slug,
    normalize_linear_issue,
    register,
)


def _configured_states(tracker_cfg: dict[str, Any], *keys: str, default: tuple[str, ...]) -> list[str]:
    for key in keys:
        if key in tracker_cfg:
            value = tracker_cfg.get(key)
            return list(value) if isinstance(value, list) else []
    return list(default)


LINEAR_ISSUES_BY_STATES_QUERY = """
query IssueRunnerIssuesByStates($projectSlug: String!, $states: [String!], $after: String) {
  issues(
    first: 50,
    after: $after,
    filter: {
      project: { slugId: { eq: $projectSlug } },
      state: { name: { in: $states } }
    }
  ) {
    nodes {
      id
      identifier
      title
      description
      priority
      branchName
      url
      createdAt
      updatedAt
      state { name }
      labels { nodes { name } }
      relations {
        nodes {
          type
          relatedIssue {
            id
            identifier
            createdAt
            updatedAt
            state { name }
          }
        }
      }
    }
    pageInfo {
      hasNextPage
      endCursor
    }
  }
}
""".strip()

LINEAR_ISSUES_BY_IDS_QUERY = """
query IssueRunnerIssuesByIds($ids: [ID!], $after: String) {
  issues(
    first: 50,
    after: $after,
    filter: {
      id: { in: $ids }
    }
  ) {
    nodes {
      id
      identifier
      title
      description
      priority
      branchName
      url
      createdAt
      updatedAt
      state { name }
      labels { nodes { name } }
      relations {
        nodes {
          type
          relatedIssue {
            id
            identifier
            createdAt
            updatedAt
            state { name }
          }
        }
      }
    }
    pageInfo {
      hasNextPage
      endCursor
    }
  }
}
""".strip()


@register("linear")
class LinearTrackerClient:
    kind = "linear"

    def __init__(
        self,
        *,
        workflow_root: Path,
        tracker_cfg: dict[str, Any],
        post_json: Callable[..., dict[str, Any]] | None = None,
    ):
        del workflow_root
        self._tracker_cfg = tracker_cfg
        self._endpoint = linear_endpoint(tracker_cfg)
        self._api_key = linear_api_key(tracker_cfg)
        self._project_slug = linear_project_slug(tracker_cfg)
        self._post_json = post_json or http_post_json

    def list_all(self) -> list[dict[str, Any]]:
        issues = {}
        for issue in self.list_candidates():
            issues[issue["id"]] = issue
        for issue in self.list_terminal():
            issues[issue["id"]] = issue
        return sorted(issues.values(), key=issue_priority_sort_key)

    def list_candidates(self) -> list[dict[str, Any]]:
        from workflows.issue_runner.tracker import eligible_issues

        raw_issues = self._query_issues_by_states(
            _configured_states(
                self._tracker_cfg,
                "active_states",
                "active-states",
                default=DEFAULT_ACTIVE_STATES,
            )
        )
        return eligible_issues(
            tracker_cfg=self._tracker_cfg,
            issues=[normalize_linear_issue(issue) for issue in raw_issues],
        )

    def refresh(self, issue_ids: list[str]) -> dict[str, dict[str, Any]]:
        ids = [str(issue_id).strip() for issue_id in issue_ids if str(issue_id).strip()]
        if not ids:
            return {}
        issues = self._query_issues_by_ids(ids)
        return {
            issue["id"]: issue
            for issue in (normalize_linear_issue(raw_issue) for raw_issue in issues)
        }

    def list_terminal(self) -> list[dict[str, Any]]:
        raw_issues = self._query_issues_by_states(
            _configured_states(
                self._tracker_cfg,
                "terminal_states",
                "terminal-states",
                default=DEFAULT_TERMINAL_STATES,
            )
        )
        terminal_states = {
            str(value).strip().lower()
            for value in _configured_states(
                self._tracker_cfg,
                "terminal_states",
                "terminal-states",
                default=DEFAULT_TERMINAL_STATES,
            )
            if str(value).strip()
        }
        out = []
        for issue in (normalize_linear_issue(raw_issue) for raw_issue in raw_issues):
            if str(issue.get("state") or "").strip().lower() in terminal_states:
                out.append(issue)
        out.sort(key=issue_priority_sort_key)
        return out

    def _query_issues_by_states(self, states: list[str]) -> list[dict[str, Any]]:
        if not states:
            return []
        return self._paginate_query(
            LINEAR_ISSUES_BY_STATES_QUERY,
            lambda after: {
                "projectSlug": self._project_slug,
                "states": states,
                "after": after,
            },
        )

    def _query_issues_by_ids(self, issue_ids: list[str]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for batch in chunk(issue_ids, 50):
            out.extend(
                self._paginate_query(
                    LINEAR_ISSUES_BY_IDS_QUERY,
                    lambda after, batch=batch: {
                        "ids": batch,
                        "after": after,
                    },
                )
            )
        return out

    def _paginate_query(
        self,
        query: str,
        variables_for_cursor: Callable[[str | None], dict[str, Any]],
    ) -> list[dict[str, Any]]:
        after: str | None = None
        nodes: list[dict[str, Any]] = []
        while True:
            payload = self._post_json(
                self._endpoint,
                query=query,
                variables=variables_for_cursor(after),
                api_key=self._api_key,
            )
            try:
                issues = (((payload.get("data") or {}).get("issues")) or {})
                page_nodes = issues.get("nodes") or []
                page_info = issues.get("pageInfo") or {}
            except AttributeError as exc:
                raise TrackerConfigError("Linear GraphQL response did not contain the expected issues connection") from exc
            if not isinstance(page_nodes, list):
                raise TrackerConfigError("Linear GraphQL response issues.nodes must be a list")
            nodes.extend(item for item in page_nodes if isinstance(item, dict))
            if not page_info.get("hasNextPage"):
                break
            after = str(page_info.get("endCursor") or "").strip() or None
            if not after:
                raise TrackerConfigError("Linear GraphQL pagination reported hasNextPage without endCursor")
        return nodes
