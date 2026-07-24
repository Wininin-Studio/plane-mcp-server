SERVER_INSTRUCTIONS = """
## API compatibility

Call get_plane_api_capabilities before a workflow that requires PQL,
workspace-wide work-item listing, custom relations, relation deletion, or
Plane Pages. A false capability is authoritative; do not silently downgrade
the workflow.

For Plane 1.3.1, always pass project_id and use list_work_items structured
filters (assignee_id, state_groups, state_ids, parent_id,
work_item_type_id). Plane 1.3.1 silently ignores PQL, so the MCP rejects PQL
under that compatibility profile instead of returning unfiltered data.

## Epics

There are no epic tools — an epic is a work item whose type is named "Epic". Work
items always belong to a project; ask which if one is not named.
1. type = resolve_work_item_type(project_id, "Epic") — type.id is the type_id.
2. Create: create_work_item(project_id, type_id=type.id, name=...).
3. List: list_work_items(project_id, work_item_type_id=type.id).
4. Read / update / delete / nest: retrieve_work_item / update_work_item /
   delete_work_item by work item id (set parent=<work item id> to nest).
5. List an epic's children: list_work_items(project_id, parent_id=<epic work item UUID>).
"""
