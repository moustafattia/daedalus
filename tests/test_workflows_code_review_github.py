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


def test_issue_label_names_normalizes_dicts_and_strings():
    github_module = load_module("daedalus_workflows_change_delivery_github_test", "workflows/change_delivery/github.py")

    result = github_module.issue_label_names(
        {
            "labels": [
                {"name": "active-lane"},
                {"name": "EFFORT:LARGE"},
                "review:2026-04-18",
                "",
                {"name": None},
            ]
        }
    )

    assert result == {"active-lane", "effort:large", "review:2026-04-18"}


def test_parse_priority_from_title_defaults_when_not_p_label():
    github_module = load_module("daedalus_workflows_change_delivery_github_test", "workflows/change_delivery/github.py")

    assert github_module.parse_priority_from_title("[P3] Legit priority") == 3
    assert github_module.parse_priority_from_title("[A01] Architecture lane") == 999


def test_pick_next_lane_issue_skips_active_lane_and_sorts_by_priority_then_issue_number():
    github_module = load_module("daedalus_workflows_change_delivery_github_test", "workflows/change_delivery/github.py")

    result = github_module.pick_next_lane_issue(
        [
            {"number": 230, "title": "[P2] lower priority", "labels": []},
            {"number": 220, "title": "[P1] currently active", "labels": [{"name": "active-lane"}]},
            {"number": 225, "title": "[P1] first real candidate", "labels": []},
            {"number": 224, "title": "[A07] architecture lane", "labels": []},
        ]
    )

    assert result["number"] == 225


def test_get_issue_details_uses_runner_when_issue_number_present():
    github_module = load_module("daedalus_workflows_change_delivery_github_test", "workflows/change_delivery/github.py")

    seen = {}

    def fake_run_json(command, cwd=None):
        seen["command"] = command
        seen["cwd"] = cwd
        return {"number": 224, "title": "Issue 224"}

    result = github_module.get_issue_details(224, repo_path=Path("/tmp/repo"), run_json=fake_run_json)

    assert result == {"number": 224, "title": "Issue 224"}
    assert seen["command"][:4] == ["gh", "issue", "view", "224"]
    assert seen["cwd"] == Path("/tmp/repo")


def test_issue_add_label_returns_true_when_runner_succeeds():
    github_module = load_module("daedalus_workflows_change_delivery_github_test", "workflows/change_delivery/github.py")

    seen = {}

    def fake_run(command, cwd=None):
        seen["command"] = command
        seen["cwd"] = cwd

    result = github_module.issue_add_label(224, "active-lane", repo_path=Path("/tmp/repo"), run=fake_run)

    assert result is True
    assert seen["command"] == ["gh", "issue", "edit", "224", "--add-label", "active-lane"]
    assert seen["cwd"] == Path("/tmp/repo")



def test_pick_next_lane_issue_from_repo_fetches_open_issues_then_selects_best_candidate():
    github_module = load_module("daedalus_workflows_change_delivery_github_test", "workflows/change_delivery/github.py")

    seen = {}

    def fake_run_json(command, cwd=None):
        seen["command"] = command
        seen["cwd"] = cwd
        return [
            {"number": 230, "title": "[P2] lower priority", "labels": []},
            {"number": 220, "title": "[P1] currently active", "labels": [{"name": "active-lane"}]},
            {"number": 225, "title": "[P1] first real candidate", "labels": []},
        ]

    result = github_module.pick_next_lane_issue_from_repo(Path("/tmp/repo"), run_json=fake_run_json)

    assert result["number"] == 225
    assert seen["command"] == ["gh", "issue", "list", "--state", "open", "--limit", "100", "--json", "number,title,url,labels,createdAt"]
    assert seen["cwd"] == Path("/tmp/repo")


def test_get_active_lane_from_repo_returns_single_matching_issue():
    github_module = load_module("daedalus_workflows_change_delivery_github_test", "workflows/change_delivery/github.py")

    seen = {}

    def fake_run_json(command, cwd=None):
        seen["command"] = command
        seen["cwd"] = cwd
        return [
            {"number": 223, "title": "[P2] not active", "url": "https://example/223", "labels": []},
            {
                "number": 224,
                "title": "[A07] active lane",
                "url": "https://example/224",
                "labels": [{"name": "active-lane"}],
                "assignees": [{"login": "moustafa"}],
                "updatedAt": "2026-04-23T00:00:00Z",
            },
        ]

    result = github_module.get_active_lane_from_repo(Path("/tmp/repo"), run_json=fake_run_json)

    assert result["number"] == 224
    assert result["assignees"] == [{"login": "moustafa"}]
    assert seen["command"] == [
        "gh",
        "issue",
        "list",
        "--state",
        "open",
        "--limit",
        "200",
        "--json",
        "number,title,url,labels,assignees,updatedAt",
    ]
    assert seen["cwd"] == Path("/tmp/repo")


def test_get_active_lane_from_repo_reports_multiple_matching_issues():
    github_module = load_module("daedalus_workflows_change_delivery_github_test", "workflows/change_delivery/github.py")

    def fake_run_json(_command, cwd=None):
        assert cwd == Path("/tmp/repo")
        return [
            {"number": 224, "title": "lane one", "url": "https://example/224", "labels": [{"name": "active-lane"}]},
            {"number": 225, "title": "lane two", "url": "https://example/225", "labels": [{"name": "active-lane"}]},
        ]

    result = github_module.get_active_lane_from_repo(Path("/tmp/repo"), run_json=fake_run_json)

    assert result == {
        "error": "multiple-active-lanes",
        "issues": [
            {"number": 224, "title": "lane one", "url": "https://example/224"},
            {"number": 225, "title": "lane two", "url": "https://example/225"},
        ],
    }


def test_get_open_pr_for_issue_matches_head_branch_to_issue_number():
    github_module = load_module("daedalus_workflows_change_delivery_github_test", "workflows/change_delivery/github.py")

    seen = {}

    def fake_run_json(command, cwd=None):
        seen["command"] = command
        seen["cwd"] = cwd
        return [
            {"number": 91, "headRefName": "codex/issue-223-first", "headRefOid": "aaa", "isDraft": False, "updatedAt": "2026-04-23T00:00:00Z"},
            {"number": 92, "headRefName": "codex/issue-224-second", "headRefOid": "bbb", "isDraft": True, "updatedAt": "2026-04-23T00:05:00Z"},
        ]

    result = github_module.get_open_pr_for_issue(
        224,
        repo_path=Path("/tmp/repo"),
        run_json=fake_run_json,
        issue_number_from_branch_fn=lambda branch: int(branch.split("issue-")[1].split("-")[0]),
    )

    assert result["number"] == 92
    assert seen["command"] == [
        "gh",
        "pr",
        "list",
        "--state",
        "open",
        "--limit",
        "50",
        "--json",
        "number,title,url,headRefName,headRefOid,isDraft,updatedAt",
    ]
    assert seen["cwd"] == Path("/tmp/repo")
