"""Reliable project-scoped work-item filtering for Plane 1.3.1."""

from __future__ import annotations

from typing import Any

from plane import PlaneClient
from plane.api.work_items.base import prepare_work_item_params
from plane.models.query_params import WorkItemQueryParams

_STATE_GROUPS = {"backlog", "unstarted", "started", "completed", "cancelled"}
_CURSOR_PREFIX = "plane131:"


def _split_csv(value: str | None) -> set[str]:
    return {part.strip() for part in value.split(",") if part.strip()} if value else set()


def _value_id(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        identifier = value.get("id")
        return str(identifier) if identifier else None
    identifier = getattr(value, "id", None)
    return str(identifier) if identifier else None


def _assignee_ids(item: dict[str, Any]) -> set[str]:
    values = item.get("assignees") or item.get("assignee_ids") or []
    return {identifier for value in values if (identifier := _value_id(value))}


def _state(item: dict[str, Any]) -> tuple[str | None, str | None]:
    value = item.get("state")
    if isinstance(value, dict):
        identifier = value.get("id")
        group = value.get("group")
        return (
            str(identifier) if identifier else None,
            str(group) if group else None,
        )
    return _value_id(value) or item.get("state_id"), item.get("state_group")


def _matches(
    item: dict[str, Any],
    *,
    assignee_id: str | None,
    state_groups: set[str],
    state_ids: set[str],
    parent_id: str | None,
    work_item_type_id: str | None,
) -> bool:
    state_id, state_group = _state(item)
    parent = _value_id(item.get("parent"))
    type_id = item.get("type_id") or _value_id(item.get("type"))
    return (
        (assignee_id is None or assignee_id in _assignee_ids(item))
        and (not state_groups or state_group in state_groups)
        and (not state_ids or state_id in state_ids)
        and (parent_id is None or parent == parent_id)
        and (work_item_type_id is None or type_id == work_item_type_id)
    )


def _parse_offset(cursor: str | None) -> int:
    if cursor is None:
        return 0
    if not cursor.startswith(_CURSOR_PREFIX):
        raise ValueError(
            f"Structured filters require a cursor returned by the same filtered query "
            f"(expected prefix {_CURSOR_PREFIX!r})."
        )
    try:
        offset = int(cursor.removeprefix(_CURSOR_PREFIX))
    except ValueError as exc:
        raise ValueError("Invalid structured-filter cursor.") from exc
    if offset < 0:
        raise ValueError("Invalid structured-filter cursor.")
    return offset


def _raw_project_items(
    client: PlaneClient,
    workspace_slug: str,
    project_id: str,
    *,
    order_by: str | None,
    expand: str | None,
    fields: str | None,
    external_id: str | None,
    external_source: str | None,
) -> list[dict[str, Any]]:
    """Read every project page while preserving fields the SDK model drops."""
    cursor: str | None = None
    seen_cursors: set[str] = set()
    results: list[dict[str, Any]] = []

    while True:
        params = WorkItemQueryParams(
            order_by=order_by,
            per_page=100,
            cursor=cursor,
            expand=expand,
            fields=fields,
            external_id=external_id,
            external_source=external_source,
        )
        payload = client.work_items._get(
            f"{workspace_slug}/projects/{project_id}/work-items",
            params=prepare_work_item_params(params),
        )

        if not isinstance(payload, dict):
            raise RuntimeError("Plane returned an unsupported work-item list response.")
        if "results" not in payload:
            results.append(payload)
            break

        page_results = payload.get("results") or []
        results.extend(item for item in page_results if isinstance(item, dict))
        next_cursor = payload.get("next_cursor")
        has_next = bool(payload.get("next_page_results"))
        if not has_next or not next_cursor:
            break
        next_cursor = str(next_cursor)
        if next_cursor in seen_cursors:
            raise RuntimeError("Plane repeated a pagination cursor while scanning work items.")
        seen_cursors.add(next_cursor)
        cursor = next_cursor

    return results


def list_work_items_structured(
    client: PlaneClient,
    workspace_slug: str,
    project_id: str,
    *,
    assignee_id: str | None,
    state_groups: list[str] | None,
    state_ids: list[str] | None,
    parent_id: str | None,
    work_item_type_id: str | None,
    order_by: str | None,
    per_page: int | None,
    cursor: str | None,
    expand: str | None,
    fields: str | None,
    external_id: str | None,
    external_source: str | None,
) -> dict[str, Any]:
    """Scan a project and apply structured filters locally with correct totals."""
    requested_state_groups = {value.strip().lower() for value in state_groups or [] if value.strip()}
    invalid_groups = requested_state_groups - _STATE_GROUPS
    if invalid_groups:
        raise ValueError(f"state_groups contains unsupported values: {sorted(invalid_groups)}")
    requested_state_ids = {value for value in state_ids or [] if value}

    internal_expand = _split_csv(expand)
    if assignee_id:
        internal_expand.add("assignees")
    if requested_state_groups:
        internal_expand.add("state")

    requested_fields = _split_csv(fields)
    internal_fields = set(requested_fields)
    if assignee_id:
        internal_fields.add("assignees")
    if requested_state_groups or requested_state_ids:
        internal_fields.add("state")
    if parent_id:
        internal_fields.add("parent")
    if work_item_type_id:
        internal_fields.add("type_id")

    raw_items = _raw_project_items(
        client,
        workspace_slug,
        project_id,
        order_by=order_by,
        expand=",".join(sorted(internal_expand)) or None,
        fields=",".join(sorted(internal_fields)) if fields else None,
        external_id=external_id,
        external_source=external_source,
    )
    matches = [
        item
        for item in raw_items
        if _matches(
            item,
            assignee_id=assignee_id,
            state_groups=requested_state_groups,
            state_ids=requested_state_ids,
            parent_id=parent_id,
            work_item_type_id=work_item_type_id,
        )
    ]

    page_size = 25 if per_page is None else per_page
    if not 1 <= page_size <= 100:
        raise ValueError("per_page must be between 1 and 100.")
    offset = _parse_offset(cursor)
    page = matches[offset : offset + page_size]
    if requested_fields:
        page = [{key: value for key, value in item.items() if key in requested_fields} for item in page]

    next_offset = offset + len(page)
    previous_offset = max(0, offset - page_size)
    has_next = next_offset < len(matches)
    has_previous = offset > 0
    return {
        "results": page,
        "total_count": len(matches),
        "count": len(page),
        "next_cursor": f"{_CURSOR_PREFIX}{next_offset}" if has_next else "",
        "prev_cursor": f"{_CURSOR_PREFIX}{previous_offset}" if has_previous else "",
        "next_page_results": has_next,
        "prev_page_results": has_previous,
        "compatibility": {
            "filtering": "client-side",
            "project_scan_count": len(raw_items),
        },
    }
