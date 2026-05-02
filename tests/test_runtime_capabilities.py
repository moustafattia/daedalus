from runtimes.capabilities import (
    CAP_CANCEL,
    CAP_ONE_SHOT,
    CAP_SERVICE_REQUIRED,
    CAP_TOKEN_METRICS,
    recognized_runtime_kinds,
    runtime_profile_capabilities,
)


def test_runtime_capability_registry_lists_builtin_kinds():
    assert recognized_runtime_kinds() == {
        "acpx-codex",
        "claude-cli",
        "codex-app-server",
        "hermes-agent",
    }


def test_codex_app_server_external_profile_exposes_service_capabilities():
    profile = runtime_profile_capabilities(
        {"kind": "codex-app-server", "mode": "external", "endpoint": "ws://127.0.0.1:4500"}
    )

    assert profile is not None
    assert CAP_CANCEL in profile.capabilities
    assert CAP_TOKEN_METRICS in profile.capabilities
    assert CAP_SERVICE_REQUIRED in profile.capabilities


def test_hermes_agent_profile_is_one_shot_without_token_metrics():
    profile = runtime_profile_capabilities({"kind": "hermes-agent", "mode": "chat"})

    assert profile is not None
    assert CAP_ONE_SHOT in profile.capabilities
    assert CAP_TOKEN_METRICS not in profile.capabilities
