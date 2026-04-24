#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
from typing import Any

from workflows.code_review.paths import resolve_default_workflow_root as resolve_yoyopod_core_workflow_root
from workflows.code_review.paths import runtime_paths as yoyopod_runtime_paths

PLUGIN_DIR = Path(__file__).resolve().parent
DEFAULT_WORKFLOW_ROOT_ENV_VARS = ("YOYOPOD_RELAY_WORKFLOW_ROOT", "HERMES_RELAY_WORKFLOW_ROOT")


def resolve_default_workflow_root() -> Path:
    return resolve_yoyopod_core_workflow_root(plugin_dir=PLUGIN_DIR)


DEFAULT_WORKFLOW_ROOT = resolve_default_workflow_root()
DEFAULT_STATE_PATH = yoyopod_runtime_paths(DEFAULT_WORKFLOW_ROOT)["alert_state_path"]


def _load_tools_module():
    module_path = PLUGIN_DIR / "tools.py"
    spec = importlib.util.spec_from_file_location("yoyopod_relay_plugin_tools_for_alerts", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load relay plugin tools from {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _execute_plugin_command(command: str) -> str:
    tools_module = _load_tools_module()
    result = tools_module.execute_raw_args(command)
    if result.startswith("relay error:"):
        raise RuntimeError(result)
    return result


def _load_optional_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _critical_issues(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    doctor = snapshot.get("doctor") or {}
    for check in doctor.get("checks") or []:
        if check.get("severity") == "critical" and check.get("status") == "fail":
            reasons = ((check.get("details") or {}).get("reasons") or [])
            issues.append(
                {
                    "code": check.get("code"),
                    "summary": check.get("summary"),
                    "reasons": [str(reason) for reason in reasons],
                }
            )
    active_gate = snapshot.get("active_gate") or {}
    if not active_gate.get("allowed", True):
        issues.append(
            {
                "code": "active_execution_gate",
                "summary": "Relay active execution gate is blocked",
                "reasons": [str(reason) for reason in (active_gate.get("reasons") or [])],
            }
        )
    return issues


def _fingerprint_for_issues(issues: list[dict[str, Any]]) -> str | None:
    if not issues:
        return None
    normalized = [
        {
            "code": str(issue.get("code")),
            "summary": str(issue.get("summary")),
            "reasons": sorted(str(reason) for reason in (issue.get("reasons") or [])),
        }
        for issue in issues
    ]
    return json.dumps(sorted(normalized, key=lambda issue: (issue["code"], issue["summary"], issue["reasons"])), sort_keys=True, separators=(",", ":"))


def _owner_summary(snapshot: dict[str, Any]) -> dict[str, Any]:
    return ((snapshot.get("doctor") or {}).get("owner_summary") or {})


def _alert_message(*, issues: list[dict[str, Any]], snapshot: dict[str, Any]) -> str:
    owner = _owner_summary(snapshot)
    issue_bits = []
    for issue in issues:
        reasons = issue.get("reasons") or []
        suffix = f" ({', '.join(reasons)})" if reasons else ""
        issue_bits.append(f"{issue.get('code')}{suffix}")
    return (
        "YoYoPod Relay alert: "
        f"primary={owner.get('primary_owner')} "
        f"issues=" + "; ".join(issue_bits)
    )


def _resolution_message(snapshot: dict[str, Any]) -> str:
    owner = _owner_summary(snapshot)
    return (
        "YoYoPod Relay recovered: "
        f"primary={owner.get('primary_owner')} "
        f"gate_allowed={owner.get('gate_allowed')}"
    )


def build_alert_decision(*, snapshot: dict[str, Any], previous_state: dict[str, Any] | None) -> dict[str, Any]:
    previous_state = previous_state or {}
    issues = _critical_issues(snapshot)
    fingerprint = _fingerprint_for_issues(issues)
    previous_active = bool(previous_state.get("active"))
    previous_fingerprint = previous_state.get("fingerprint")
    report_generated_at = snapshot.get("report_generated_at") or ((snapshot.get("doctor") or {}).get("report_generated_at"))

    should_alert = bool(issues) and (not previous_active or previous_fingerprint != fingerprint)
    should_resolve = (not issues) and previous_active

    return {
        "should_alert": should_alert,
        "should_resolve": should_resolve,
        "fingerprint": fingerprint,
        "message": _alert_message(issues=issues, snapshot=snapshot) if issues else None,
        "resolution_message": _resolution_message(snapshot) if should_resolve else None,
        "issues": issues,
        "next_state_on_alert": {
            "active": True,
            "fingerprint": fingerprint,
            "lastSentAt": report_generated_at,
        },
        "next_state_on_resolve": {
            "active": False,
            "fingerprint": None,
            "lastResolvedAt": report_generated_at,
        },
    }



def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp_path.replace(path)



def persist_alert_state(*, state_path: Path, decision: dict[str, Any], delivery_result: dict[str, Any]) -> dict[str, Any]:
    if not delivery_result.get("delivered"):
        return {"persisted": False, "reason": "delivery-not-successful"}
    if decision.get("should_alert"):
        next_state = dict(decision.get("next_state_on_alert") or {})
        state_kind = "alert"
    elif decision.get("should_resolve"):
        next_state = dict(decision.get("next_state_on_resolve") or {})
        state_kind = "resolve"
    else:
        return {"persisted": False, "reason": "no-state-change"}
    payload = {
        **next_state,
        "state_kind": state_kind,
        "delivery": {key: value for key, value in delivery_result.items() if value is not None},
    }
    _write_json_atomic(state_path, payload)
    return {"persisted": True, "state_path": str(state_path), "state_kind": state_kind}



def collect_snapshot(*, workflow_root: Path) -> dict[str, Any]:
    doctor_text = _execute_plugin_command(f"doctor --workflow-root {workflow_root} --json")
    active_gate_text = _execute_plugin_command(f"active-gate-status --workflow-root {workflow_root} --json")
    doctor = json.loads(doctor_text)
    active_gate = json.loads(active_gate_text)
    return {
        "report_generated_at": doctor.get("report_generated_at"),
        "doctor": doctor,
        "active_gate": active_gate,
    }


def build_current_decision(*, workflow_root: Path, state_path: Path) -> dict[str, Any]:
    snapshot = collect_snapshot(workflow_root=workflow_root)
    previous_state = _load_optional_json(state_path)
    return {
        "snapshot": snapshot,
        "previous_state": previous_state,
        "decision": build_alert_decision(snapshot=snapshot, previous_state=previous_state),
        "state_path": str(state_path),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build Relay outage alert decisions.")
    parser.add_argument("--workflow-root", default=str(DEFAULT_WORKFLOW_ROOT))
    parser.add_argument("--state-path", default=str(DEFAULT_STATE_PATH))
    parser.add_argument("--delivery-json")
    parser.add_argument("--json", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    result = build_current_decision(
        workflow_root=Path(args.workflow_root),
        state_path=Path(args.state_path),
    )
    if args.delivery_json:
        delivery_result = json.loads(args.delivery_json)
        result["persistence"] = persist_alert_state(
            state_path=Path(args.state_path),
            decision=result["decision"],
            delivery_result=delivery_result,
        )
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        decision = result["decision"]
        if decision.get("should_alert"):
            print(decision.get("message"))
        elif decision.get("should_resolve"):
            print(decision.get("resolution_message"))
        else:
            print("NO_ALERT")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
