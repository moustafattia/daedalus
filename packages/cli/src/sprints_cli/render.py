import json
from typing import Any

from .formatters import format_doctor, format_status


def render_result(
    command: str,
    result: dict[str, Any],
    *,
    json_output: bool | None = None,
    output_format: str | None = None,
) -> str:
    # Resolve effective format. New callers pass output_format; legacy callers pass json_output.
    if output_format is None:
        output_format = "json" if json_output else "text"
    if output_format == "json":
        return json.dumps(result, indent=2, sort_keys=True)
    if command == "status":
        return format_status(result)
    if command == "doctor":
        return format_doctor(result)
    if command == "validate":
        checks = result.get("checks") or []
        failures = result.get("failures") or []
        warnings = result.get("warnings") or []
        lines = [
            f"workflow contract valid={result.get('ok')} workflow={result.get('workflow')}",
            f"source={result.get('source_path')}",
            f"checks={len(checks)} failures={len(failures)} warnings={len(warnings)}",
        ]
        for check in checks:
            prefix = {
                "pass": "PASS",
                "warn": "WARN",
                "fail": "FAIL",
                "skip": "SKIP",
            }.get(
                str(check.get("status")),
                str(check.get("status")).upper(),
            )
            lines.append(f"- {prefix} {check.get('name')}: {check.get('detail')}")
            for item in (check.get("items") or [])[:5]:
                path = item.get("path") if isinstance(item, dict) else None
                message = item.get("message") if isinstance(item, dict) else str(item)
                lines.append(f"  {path or '<root>'}: {message}")
        recommendations = result.get("recommendations") or []
        if recommendations:
            lines.append("next steps:")
            lines.extend(f"- {item}" for item in recommendations[:8])
        return "\n".join(lines)
    if command == "apply-contract":
        lines = [
            (
                f"workflow contract applied ok={result.get('ok')} "
                f"source={result.get('source_ref')}"
            ),
            f"source_commit={result.get('source_commit')}",
            f"active_contract={result.get('active_contract_path')}",
            f"contract_sha256={result.get('contract_sha256')}",
        ]
        active_lanes = result.get("active_lanes") or []
        if active_lanes:
            lines.append("active_lanes=" + ", ".join(active_lanes))
        return "\n".join(lines)
    if command == "configure-runtime":
        bindings = result.get("bindings") or []
        availability = result.get("availability_checks") or []
        mode = "dry-run " if result.get("dry_run") else ""
        lines = [
            (
                f"{mode}configured runtime preset={result.get('runtime_preset')} "
                f"profile={result.get('runtime_name')} workflow={result.get('workflow')}"
            ),
            f"contract={result.get('contract_path')}",
            "changed_roles=" + ", ".join(result.get("changed_roles") or []),
        ]
        for binding in bindings:
            lines.append(
                f"- {binding.get('role')} -> {binding.get('runtime')} "
                f"kind={binding.get('kind')} exists={binding.get('profile_exists')}"
            )
        for check in availability:
            lines.append(
                f"- {check.get('status')} {check.get('name')}: {check.get('detail')}"
            )
        return "\n".join(lines)
    if command == "runtime-matrix":
        lines = [
            (
                f"runtime matrix ok={result.get('ok')} workflow={result.get('workflow')} "
                f"execute={result.get('execute')}"
            ),
            f"contract={result.get('contract_path')}",
        ]
        missing = result.get("missing") or {}
        if missing.get("roles") or missing.get("runtimes"):
            lines.append(
                f"missing roles={missing.get('roles') or []} runtimes={missing.get('runtimes') or []}"
            )
        for item in result.get("matrix") or []:
            binding = item.get("binding") or {}
            availability = item.get("availability") or {}
            smoke = item.get("smoke") or {}
            detail = (
                f"- {item.get('role')} -> {item.get('runtime')} kind={item.get('kind')} "
                f"binding={binding.get('status')} availability={availability.get('status')}"
            )
            if smoke:
                detail += f" smoke={'pass' if smoke.get('ok') else 'fail'}"
            lines.append(detail)
            if availability.get("detail"):
                lines.append(f"  availability: {availability.get('detail')}")
            if smoke.get("error"):
                lines.append(f"  smoke error: {smoke.get('error')}")
            elif smoke.get("output_preview"):
                lines.append(f"  output: {smoke.get('output_preview')}")
        return "\n".join(lines)
    if command == "runs":
        if result.get("mode") == "show":
            run = result.get("run") or {}
            lines = [
                f"run={run.get('run_id')}",
                f"workflow={result.get('workflow')} mode={run.get('mode')} status={run.get('status')}",
                f"started_at={run.get('started_at')} completed_at={run.get('completed_at')}",
                f"selected={run.get('selected_count')} completed={run.get('completed_count')} age_seconds={run.get('age_seconds')}",
            ]
            if run.get("error"):
                lines.append(f"error={run.get('error')}")
            timeline = result.get("timeline") or []
            lines.append(f"timeline_events={len(timeline)}")
            for event in timeline[:10]:
                payload = (
                    event.get("payload")
                    if isinstance(event.get("payload"), dict)
                    else {}
                )
                kind = (
                    event.get("event")
                    or payload.get("event")
                    or event.get("action")
                    or payload.get("action")
                    or event.get("event_type")
                    or "event"
                )
                at = (
                    event.get("at")
                    or payload.get("at")
                    or event.get("created_at")
                    or event.get("time")
                    or ""
                )
                detail = (
                    event.get("summary")
                    or payload.get("summary")
                    or event.get("error")
                    or payload.get("error")
                    or event.get("reason")
                    or payload.get("reason")
                    or ""
                )
                lines.append(f"- {at} {kind} {detail}".strip())
            return "\n".join(lines)
        runs = result.get("runs") or []
        if not runs:
            return f"workflow={result.get('workflow')} runs=0 mode={result.get('mode')}"
        lines = [
            f"workflow={result.get('workflow')} mode={result.get('mode')} runs={len(runs)}"
        ]
        for run in runs:
            stale = " stale=true" if run.get("stale") else ""
            lines.append(
                f"- {run.get('run_id')} {run.get('mode')} {run.get('status')} "
                f"selected={run.get('selected_count')} completed={run.get('completed_count')} "
                f"started={run.get('started_at')}{stale}"
            )
        return "\n".join(lines)
    if command == "events":
        if result.get("mode") == "stats":
            stats = result.get("stats") or {}
            retention = stats.get("retention") or {}
            lines = [
                f"workflow={result.get('workflow')} total_events={stats.get('total_events')}",
                f"oldest_event_at={stats.get('oldest_event_at')} oldest_age_seconds={stats.get('oldest_age_seconds')}",
                f"newest_event_at={stats.get('newest_event_at')}",
                (
                    f"retention_configured={retention.get('configured')} "
                    f"overdue={retention.get('overdue')} "
                    f"max_age_seconds={retention.get('max_age_seconds')} "
                    f"max_rows={retention.get('max_rows')} "
                    f"excess_rows={retention.get('excess_rows')}"
                ),
            ]
            if stats.get("by_type"):
                lines.append(f"by_type={stats.get('by_type')}")
            if stats.get("by_severity"):
                lines.append(f"by_severity={stats.get('by_severity')}")
            return "\n".join(lines)
        if result.get("mode") == "prune":
            retention = result.get("retention") or {}
            return (
                f"workflow={result.get('workflow')} pruned_events={result.get('deleted')} "
                f"remaining={result.get('remaining')} "
                f"max_age_days={retention.get('max_age_days')} max_rows={retention.get('max_rows')}"
            )
        events = result.get("events") or []
        filters = result.get("filters") or {}
        lines = [
            f"workflow={result.get('workflow')} events={len(events)}"
            + (f" filters={filters}" if filters else "")
        ]
        for event in events[:50]:
            payload = (
                event.get("payload") if isinstance(event.get("payload"), dict) else {}
            )
            detail = (
                payload.get("summary")
                or payload.get("error")
                or payload.get("reason")
                or event.get("work_id")
                or event.get("run_id")
                or ""
            )
            lines.append(
                f"- {event.get('created_at')} {event.get('severity')} "
                f"{event.get('event_type')} work={event.get('work_id') or '-'} "
                f"run={event.get('run_id') or '-'} {detail}".strip()
            )
        return "\n".join(lines)
    if command == "codex-app-server":
        action = result.get("action")
        if action == "install":
            return (
                f"codex-app-server installed service={result.get('service_name')} "
                f"listen={result.get('listen')} ok={result.get('ok')}"
            )
        if action == "up":
            status = result.get("status") or {}
            return (
                f"codex-app-server up service={result.get('service_name')} "
                f"listen={result.get('listen')} active={status.get('active')} "
                f"enabled={status.get('enabled')} ready={(status.get('ready') or {}).get('ok')}"
            )
        if action == "down":
            status = result.get("status") or {}
            return (
                f"codex-app-server down service={result.get('service_name')} "
                f"active={status.get('active')} enabled={status.get('enabled')}"
            )
        if action == "restart":
            status = result.get("status") or {}
            return (
                f"codex-app-server restart service={result.get('service_name')} "
                f"ok={result.get('ok')} active={status.get('active')} "
                f"ready={(status.get('ready') or {}).get('ok')}"
            )
        if action == "logs":
            output = result.get("stdout") or result.get("stderr") or ""
            return output if output else f"no logs for {result.get('service_name')}"
        if action == "status":
            ready = result.get("ready") or {}
            return (
                f"codex-app-server service={result.get('service_name')} "
                f"installed={result.get('installed')} active={result.get('active')} "
                f"enabled={result.get('enabled')} ready={ready.get('ok')}"
            )
        if action == "doctor":
            failed = [
                check
                for check in result.get("checks") or []
                if check.get("status") == "fail"
            ]
            warned = [
                check
                for check in result.get("checks") or []
                if check.get("status") == "warn"
            ]
            first_problem = failed[0] if failed else (warned[0] if warned else None)
            suffix = ""
            if first_problem:
                suffix = f" first_problem={first_problem.get('name')}:{first_problem.get('detail')}"
            return (
                f"codex-app-server doctor ok={result.get('ok')} mode={result.get('mode')} "
                f"endpoint={result.get('endpoint')} failures={len(failed)} warnings={len(warned)}"
                f"{suffix}"
            )
    if command == "daemon":
        action = result.get("action")
        if action == "run":
            return (
                f"workflow daemon {result.get('status')} workflow={result.get('workflow')} "
                f"ticks={result.get('tick_count')} owner={result.get('owner_instance_id')}"
                + (
                    f" error={result.get('last_error')}"
                    if result.get("last_error")
                    else ""
                )
            )
        if action == "install":
            intervals = result.get("intervals") or {}
            return (
                f"workflow daemon installed service={result.get('service_name')} "
                f"workflow={result.get('workflow')} ok={result.get('ok')} "
                f"active_interval={intervals.get('active_interval')} "
                f"idle_interval={intervals.get('idle_interval')}"
            )
        if action == "up":
            status = result.get("status") or {}
            return (
                f"workflow daemon up service={result.get('service_name')} "
                f"workflow={result.get('workflow')} active={status.get('active')} "
                f"enabled={status.get('enabled')}"
            )
        if action == "down":
            status = result.get("status") or {}
            return (
                f"workflow daemon down service={result.get('service_name')} "
                f"active={status.get('active')} enabled={status.get('enabled')}"
            )
        if action == "restart":
            status = result.get("status") or {}
            return (
                f"workflow daemon restart service={result.get('service_name')} "
                f"ok={result.get('ok')} active={status.get('active')}"
            )
        if action == "logs":
            output = result.get("stdout") or result.get("stderr") or ""
            return output if output else f"no logs for {result.get('service_name')}"
        if action == "status":
            lease = result.get("lease") or {}
            return (
                f"workflow daemon service={result.get('service_name')} "
                f"installed={result.get('installed')} active={result.get('active')} "
                f"enabled={result.get('enabled')} lease_owner={lease.get('owner_instance_id')} "
                f"lease_stale={lease.get('stale')}"
            )
    return json.dumps(result, sort_keys=True)
