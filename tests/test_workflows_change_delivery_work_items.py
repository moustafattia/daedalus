def test_change_delivery_lane_to_work_item_ref():
    from workflows.change_delivery.work_items import lane_to_work_item_ref

    work_item = lane_to_work_item_ref(
        {
            "lane_id": "lane-329",
            "issue_number": 329,
            "issue_title": "Ship the change",
            "issue_url": "https://github.example/issues/329",
            "workflow_state": "awaiting_review",
            "lane_status": "active",
            "active_actor_id": "actor-1",
            "current_action_id": "action-1",
        }
    )

    assert work_item.id == "lane-329"
    assert work_item.identifier == "#329"
    assert work_item.title == "Ship the change"
    assert work_item.state == "awaiting_review"
    assert work_item.source == "change-delivery"
    assert work_item.metadata["active_actor_id"] == "actor-1"
