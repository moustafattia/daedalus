from workflows.readiness import build_readiness_recommendations


def test_readiness_recommends_configure_runtime_for_issue_runner_binding_failure():
    recommendations = build_readiness_recommendations(
        [
            {
                "name": "runtime-binding:agent",
                "status": "fail",
                "detail": "agent references missing runtime profile 'codex'",
            }
        ],
        workflow="issue-runner",
        source_path="/repo/WORKFLOW.md",
    )

    assert recommendations == [
        "Run `hermes daedalus configure-runtime --runtime codex-app-server --role agent`, or define the referenced runtime profile manually."
    ]


def test_readiness_recommends_codex_service_doctor_for_external_listener_warning():
    recommendations = build_readiness_recommendations(
        [
            {
                "name": "runtime-availability:codex-app-server",
                "status": "warn",
                "detail": "ws://127.0.0.1:4500 is not reachable yet: connection refused",
            }
        ],
        workflow="change-delivery",
    )

    assert recommendations == [
        "Start or diagnose the shared Codex listener with `hermes daedalus codex-app-server up` and `hermes daedalus codex-app-server doctor`."
    ]


def test_readiness_deduplicates_recommendations():
    recommendations = build_readiness_recommendations(
        [
            {"name": "github-auth", "status": "fail", "detail": "missing auth"},
            {"name": "github-auth", "status": "fail", "detail": "still missing auth"},
        ],
        workflow="issue-runner",
    )

    assert recommendations == [
        "Run `gh auth status` and `gh auth login` for the configured GitHub host."
    ]
