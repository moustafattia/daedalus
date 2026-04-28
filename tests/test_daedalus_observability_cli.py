"""set-observability + get-observability CLI handlers."""
import importlib.util
import json
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1] / "daedalus"


def load_module(module_name: str, relative_path: str):
    module_path = REPO_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _make_workflow_root(tmp_path):
    root = tmp_path / "yoyopod_core"
    (root / "runtime" / "state" / "daedalus").mkdir(parents=True)
    (root / "config").mkdir()
    (root / "workspace").mkdir()
    return root


def test_set_observability_writes_override(tmp_path):
    tools = load_module("daedalus_tools_set_obs_test", "tools.py")
    root = _make_workflow_root(tmp_path)

    args = mock.Mock()
    args.workflow_root = root
    args.workflow = "code-review"
    args.github_comments = "off"

    out = tools.cmd_set_observability(args, parser=None)
    assert "code-review" in out
    assert "off" in out.lower() or "False" in out

    override_file = root / "runtime" / "state" / "daedalus" / "observability-overrides.json"
    assert override_file.exists()
    data = json.loads(override_file.read_text())
    assert data["code-review"]["github-comments"]["enabled"] is False


def test_set_observability_unset_removes_block(tmp_path):
    tools = load_module("daedalus_tools_set_obs_test", "tools.py")
    root = _make_workflow_root(tmp_path)

    # First set
    args1 = mock.Mock()
    args1.workflow_root = root
    args1.workflow = "code-review"
    args1.github_comments = "on"
    tools.cmd_set_observability(args1, parser=None)

    # Then unset
    args2 = mock.Mock()
    args2.workflow_root = root
    args2.workflow = "code-review"
    args2.github_comments = "unset"
    out = tools.cmd_set_observability(args2, parser=None)
    assert "unset" in out.lower() or "removed" in out.lower()

    override_file = root / "runtime" / "state" / "daedalus" / "observability-overrides.json"
    data = json.loads(override_file.read_text())
    assert "code-review" not in data


def test_get_observability_shows_default_source_when_no_yaml_no_override(tmp_path):
    tools = load_module("daedalus_tools_get_obs_test", "tools.py")
    root = _make_workflow_root(tmp_path)

    # Create a workflow.yaml without an observability block
    (root / "config" / "workflow.yaml").write_text("""\
workflow: code-review
schema-version: 1
instance: {name: yoyopod, engine-owner: hermes}
repository: {local-path: /tmp, github-slug: o/r, active-lane-label: active-lane}
runtimes:
  acpx-codex:
    kind: acpx-codex
    session-idle-freshness-seconds: 1
    session-idle-grace-seconds: 1
    session-nudge-cooldown-seconds: 1
agents:
  coder: {default: {name: x, model: y, runtime: acpx-codex}}
  internal-reviewer: {name: x, model: y, runtime: acpx-codex}
  external-reviewer: {enabled: true, name: x}
gates: {internal-review: {}, external-review: {}, merge: {}}
triggers: {lane-selector: {type: github-label, label: active-lane}}
storage: {ledger: l, health: h, audit-log: a}
""")
    args = mock.Mock()
    args.workflow_root = root
    args.workflow = "code-review"

    out = tools.cmd_get_observability(args, parser=None)
    assert "default" in out.lower() or "false" in out.lower()


def test_get_observability_shows_override_source_when_overridden(tmp_path):
    tools = load_module("daedalus_tools_get_obs_test", "tools.py")
    root = _make_workflow_root(tmp_path)

    (root / "config" / "workflow.yaml").write_text("""\
workflow: code-review
schema-version: 1
instance: {name: yoyopod, engine-owner: hermes}
repository: {local-path: /tmp, github-slug: o/r, active-lane-label: active-lane}
runtimes:
  acpx-codex:
    kind: acpx-codex
    session-idle-freshness-seconds: 1
    session-idle-grace-seconds: 1
    session-nudge-cooldown-seconds: 1
agents:
  coder: {default: {name: x, model: y, runtime: acpx-codex}}
  internal-reviewer: {name: x, model: y, runtime: acpx-codex}
  external-reviewer: {enabled: true, name: x}
gates: {internal-review: {}, external-review: {}, merge: {}}
triggers: {lane-selector: {type: github-label, label: active-lane}}
storage: {ledger: l, health: h, audit-log: a}
""")
    # Pre-write override
    override_dir = root / "runtime" / "state" / "daedalus"
    (override_dir / "observability-overrides.json").write_text(json.dumps({
        "code-review": {"github-comments": {"enabled": True, "set-at": "2026-04-26T00:00:00Z"}}
    }))

    args = mock.Mock()
    args.workflow_root = root
    args.workflow = "code-review"
    out = tools.cmd_get_observability(args, parser=None)
    assert "override" in out.lower()
    assert "true" in out.lower() or "on" in out.lower()
