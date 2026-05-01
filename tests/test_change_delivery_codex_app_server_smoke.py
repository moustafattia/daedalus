import os
import subprocess
import sys
from pathlib import Path

import pytest

from workflows.contract import load_workflow_contract


@pytest.mark.skipif(
    not os.environ.get("DAEDALUS_CHANGE_DELIVERY_CODEX_E2E"),
    reason="set DAEDALUS_CHANGE_DELIVERY_CODEX_E2E=1 to run the change-delivery Codex app-server smoke skeleton",
)
def test_live_change_delivery_codex_app_server_fixture_is_runnable():
    workflow_root_raw = os.environ.get("DAEDALUS_CHANGE_DELIVERY_E2E_WORKFLOW_ROOT")
    if not workflow_root_raw:
        pytest.skip("set DAEDALUS_CHANGE_DELIVERY_E2E_WORKFLOW_ROOT to a prepared change-delivery workflow root")

    workflow_root = Path(workflow_root_raw).expanduser().resolve()
    assert workflow_root.exists(), workflow_root

    contract = load_workflow_contract(workflow_root)
    config = contract.config
    assert config["workflow"] == "change-delivery"

    runtime_profiles = config.get("runtimes") or (config.get("daedalus") or {}).get("runtimes") or {}
    assert any(
        isinstance(profile, dict) and profile.get("kind") == "codex-app-server"
        for profile in runtime_profiles.values()
    ), "prepared fixture must use at least one codex-app-server runtime"

    status = subprocess.run(
        [
            sys.executable,
            "-m",
            "workflows",
            "--workflow-root",
            str(workflow_root),
            "status",
            "--json",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert status.returncode == 0, status.stderr or status.stdout
