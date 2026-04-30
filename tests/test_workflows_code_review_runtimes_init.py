from pathlib import Path


def test_runtime_protocol_declares_four_methods():
    from workflows.change_delivery.runtimes import Runtime

    # Protocol bodies are duck-typed; we verify the required method names exist
    # in the Protocol's namespace.
    required = {"ensure_session", "run_prompt", "assess_health", "close_session"}
    declared = {name for name in dir(Runtime) if not name.startswith("_")}
    missing = required - declared
    assert not missing, f"Runtime protocol missing methods: {missing}"


def test_build_runtimes_returns_empty_dict_when_config_is_empty():
    from workflows.change_delivery.runtimes import build_runtimes

    assert build_runtimes({}) == {}
