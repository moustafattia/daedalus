from __future__ import annotations

from typing import Any

from engine.work_items import WorkItemRef, work_item_from_change_delivery_lane


def lane_to_work_item_ref(lane: dict[str, Any]) -> WorkItemRef:
    """Expose a change-delivery lane through the shared engine work-item shape."""
    return work_item_from_change_delivery_lane(lane)
