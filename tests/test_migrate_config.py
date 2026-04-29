import json
import subprocess
import sys
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
MIGRATE_SCRIPT = REPO_ROOT / "scripts" / "migrate_config.py"


def _sample_old_json():
    return {
        "repoPath": "/home/radxa/.hermes/workspaces/example-repo",
        "cronJobsPath": "/home/radxa/.hermes/workflows/workflow-example/archive/openclaw-cron-jobs.json",
        "ledgerPath": "/home/radxa/.hermes/workflows/workflow-example/memory/workflow-status.json",
        "healthPath": "/home/radxa/.hermes/workflows/workflow-example/memory/workflow-health.json",
        "auditLogPath": "/home/radxa/.hermes/workflows/workflow-example/memory/workflow-audit.jsonl",
        "activeLaneLabel": "active-lane",
        "engineOwner": "hermes",
        "coreJobNames": [],
        "hermesJobNames": ["workflow-milestone-notifier"],
        "issueWatcherNameRegex": "issue-\\d+-watch",
        "staleness": {
            "coreJobMissMultiplier": 2.5,
            "activeLaneWithoutPrMinutes": 45,
            "reviewHeadMissingMinutes": 20,
        },
        "sessionPolicy": {
            "codexModel": "gpt-5.3-codex-spark/high",
            "codexModelLargeEffort": "gpt-5.3-codex",
            "codexModelEscalated": "gpt-5.4",
            "codexEscalateRestartCount": 2,
            "codexEscalateLocalReviewCount": 3,
            "codexEscalatePostpublishFindingCount": 3,
            "laneFailureRetryBudget": 3,
            "laneNoProgressTickBudget": 3,
            "laneOperatorAttentionRetryThreshold": 5,
            "laneOperatorAttentionNoProgressThreshold": 5,
            "codexSessionFreshnessSeconds": 900,
            "codexSessionPokeGraceSeconds": 1800,
            "codexSessionNudgeCooldownSeconds": 600,
        },
        "reviewPolicy": {
            "interReviewAgentPassWithFindingsReviews": 1,
            "interReviewAgentModel": "claude-sonnet-4-6",
            "interReviewAgentMaxTurns": 24,
            "interReviewAgentTimeoutSeconds": 1200,
            "freezeCoderWhileInterReviewAgentRunning": True,
        },
        "agentLabels": {
            "internalCoderAgent": "Internal_Coder_Agent",
            "escalationCoderAgent": "Escalation_Coder_Agent",
            "internalReviewerAgent": "Internal_Reviewer_Agent",
            "externalReviewerAgent": "External_Reviewer_Agent",
            "advisoryReviewerAgent": "Advisory_Reviewer_Agent",
        },
    }


def test_migrate_emits_valid_workflow_yaml(tmp_path):
    json_path = tmp_path / "legacy-workflow.json"
    json_path.write_text(json.dumps(_sample_old_json()), encoding="utf-8")
    workflow_root = tmp_path / "attmous-daedalus-code-review"
    yaml_path = workflow_root / "config" / "workflow.yaml"

    result = subprocess.run(
        [
            sys.executable,
            str(MIGRATE_SCRIPT),
            str(json_path),
            str(yaml_path),
            "--github-slug",
            "attmous/daedalus",
            "--milestone-chat-id",
            "123456",
        ],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr

    cfg = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))

    # Validate against the live schema
    import jsonschema
    schema_path = REPO_ROOT / "daedalus" / "workflows" / "code_review" / "schema.yaml"
    schema = yaml.safe_load(schema_path.read_text(encoding="utf-8"))
    jsonschema.validate(cfg, schema)

    # Spot-check key translations
    assert cfg["workflow"] == "code-review"
    assert cfg["schema-version"] == 1
    assert cfg["instance"]["engine-owner"] == "hermes"
    assert cfg["instance"]["name"] == "attmous-daedalus-code-review"
    assert cfg["repository"]["local-path"] == "/home/radxa/.hermes/workspaces/example-repo"
    assert cfg["runtimes"]["acpx-codex"]["session-idle-freshness-seconds"] == 900
    assert cfg["runtimes"]["claude-cli"]["max-turns-per-invocation"] == 24
    assert cfg["agents"]["coder"]["default"]["model"] == "gpt-5.3-codex-spark/high"
    assert cfg["agents"]["internal-reviewer"]["model"] == "claude-sonnet-4-6"
    assert cfg["agents"]["external-reviewer"]["provider"] == "codex-cloud"
    assert cfg["repository"]["github-slug"] == "attmous/daedalus"
    assert cfg["schedules"]["milestone-notifier"]["delivery"]["chat-id"] == "123456"


def test_migrate_requires_github_slug_when_legacy_json_has_none(tmp_path):
    json_path = tmp_path / "legacy-workflow.json"
    payload = _sample_old_json()
    payload.pop("githubSlug", None)
    payload.pop("repositorySlug", None)
    json_path.write_text(json.dumps(payload), encoding="utf-8")
    yaml_path = tmp_path / "workflow.yaml"

    result = subprocess.run(
        [sys.executable, str(MIGRATE_SCRIPT), str(json_path), str(yaml_path)],
        capture_output=True, text=True, check=False,
    )

    assert result.returncode == 2
    assert "--github-slug" in result.stderr
    assert not yaml_path.exists()


def test_migrate_omits_milestone_notifier_when_no_chat_id_is_available(tmp_path):
    json_path = tmp_path / "legacy-workflow.json"
    payload = _sample_old_json()
    payload["githubSlug"] = "attmous/daedalus"
    json_path.write_text(json.dumps(payload), encoding="utf-8")
    yaml_path = tmp_path / "workflow.yaml"

    result = subprocess.run(
        [sys.executable, str(MIGRATE_SCRIPT), str(json_path), str(yaml_path)],
        capture_output=True, text=True, check=False,
    )

    assert result.returncode == 0, result.stderr
    cfg = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    assert "milestone-notifier" not in cfg["schedules"]


def test_migrate_refuses_to_overwrite_existing_yaml(tmp_path):
    json_path = tmp_path / "legacy-workflow.json"
    json_path.write_text(json.dumps(_sample_old_json()), encoding="utf-8")
    yaml_path = tmp_path / "workflow.yaml"
    yaml_path.write_text("pre-existing: true\n", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(MIGRATE_SCRIPT), str(json_path), str(yaml_path)],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode != 0
    assert "refusing to overwrite" in result.stderr.lower() or "exists" in result.stderr.lower()
    # Pre-existing content intact
    assert yaml_path.read_text(encoding="utf-8") == "pre-existing: true\n"
