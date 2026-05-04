from workflows.lanes import _merge_readiness_is_transient


def test_mergeability_unknown_is_transient_readiness():
    assert _merge_readiness_is_transient(
        {
            "ready": False,
            "blockers": [
                {
                    "kind": "mergeability_unknown",
                    "message": "GitHub has not computed mergeability yet",
                },
                {
                    "kind": "merge_state_blocked",
                    "state": "UNKNOWN",
                    "message": "merge state is unknown",
                },
            ],
        }
    )


def test_merge_conflict_is_not_transient_readiness():
    assert not _merge_readiness_is_transient(
        {
            "ready": False,
            "blockers": [
                {
                    "kind": "merge_conflict",
                    "message": "pull request is not mergeable",
                }
            ],
        }
    )
