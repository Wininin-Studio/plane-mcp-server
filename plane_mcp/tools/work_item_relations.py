"""Work item relation tools for Plane MCP Server.

Consolidates the two relation systems behind one set of tools:

- Built-in dependencies — six fixed directional types (blocking, blocked_by,
  start_before, start_after, finish_before, finish_after).
- Custom relations — workspace-defined types created via
  list/create_work_item_relation_definition, each with an outward/inward label.

create_work_item_relation routes between them by which argument is supplied.
The LLM discovers both kinds in one place via list_work_item_relation_definitions
(built_in_dependencies + custom_definitions) and matches the user's wording to an
entry there, so a custom label like "dependent on" is never mistaken for the
built-in blocked_by.
"""

from typing import Any, get_args

from fastmcp import FastMCP
from plane.errors.errors import HttpError
from plane.models.work_items import (
    CreateWorkItemCustomRelation,
    CreateWorkItemDependency,
    CreateWorkItemRelation,
    DependencyTypeEnum,
    WorkItemWithRelationType,
)

from plane_mcp.client import get_plane_client_context
from plane_mcp.compatibility import (
    PlaneCompatibilityError,
    compatibility_capabilities,
    mark_legacy_api,
    relation_remove_supported,
    uses_legacy_api,
)

# Built-in dependency relation_type values (sourced from the SDK contract).
_DEPENDENCY_TYPES: tuple[str, ...] = get_args(DependencyTypeEnum)
_LEGACY_EXTRA_TYPES = ("duplicate", "relates_to")
_LEGACY_RELATION_TYPES = (*_DEPENDENCY_TYPES, *_LEGACY_EXTRA_TYPES)


def _normalize_relation_item(item: Any, relation_type: str) -> WorkItemWithRelationType:
    if isinstance(item, str):
        payload: dict[str, Any] = {"id": item}
    elif isinstance(item, dict):
        payload = dict(item)
        payload["id"] = payload.get("id") or payload.get("issue_id")
        payload.pop("issue_id", None)
    elif hasattr(item, "model_dump"):
        payload = item.model_dump()
    else:
        payload = {"id": str(item)}
    payload["relation_type"] = relation_type
    return WorkItemWithRelationType.model_validate(payload)


def _legacy_list_relations(
    client: Any,
    workspace_slug: str,
    project_id: str,
    work_item_id: str,
) -> dict[str, list[WorkItemWithRelationType]]:
    raw = client.work_items.relations._get(
        f"{workspace_slug}/projects/{project_id}/work-items/{work_item_id}/relations"
    )
    if not isinstance(raw, dict):
        raise RuntimeError("Plane returned an unsupported legacy relation response.")
    return {
        relation_type: [_normalize_relation_item(item, relation_type) for item in raw.get(relation_type, [])]
        for relation_type in _LEGACY_RELATION_TYPES
    }


def _legacy_create_relation(
    client: Any,
    workspace_slug: str,
    project_id: str,
    work_item_id: str,
    relation_type: str,
    work_item_ids: list[str],
) -> list[WorkItemWithRelationType]:
    if not relation_remove_supported():
        raise PlaneCompatibilityError(
            "Refusing to create a relation on Plane 1.3.1 because the server "
            "cannot remove it later. Deploy a public, relation-type-aware "
            "/relations/remove/ backport and set "
            "PLANE_RELATION_REMOVE_SUPPORTED=true first."
        )
    data = CreateWorkItemRelation(relation_type=relation_type, issues=work_item_ids)
    client.work_items.relations._post(
        f"{workspace_slug}/projects/{project_id}/work-items/{work_item_id}/relations",
        data.model_dump(exclude_none=True),
    )
    current = _legacy_list_relations(client, workspace_slug, project_id, work_item_id)
    by_id = {str(item.id): item for item in current[relation_type]}
    missing = [related_id for related_id in work_item_ids if related_id not in by_id]
    if missing:
        raise RuntimeError(f"Plane accepted the relation request but read-back verification failed for: {missing}")
    return [by_id[related_id] for related_id in work_item_ids]


def _legacy_remove_relation(
    client: Any,
    workspace_slug: str,
    project_id: str,
    work_item_id: str,
    related_work_item_id: str,
    relation_type: str | None,
) -> None:
    if not relation_remove_supported():
        raise PlaneCompatibilityError(
            "Plane 1.3.1 has no public relation-removal API. A server-side public "
            "/relations/remove/ backport is required; after deploying it, set "
            "PLANE_RELATION_REMOVE_SUPPORTED=true."
        )
    if relation_type not in _LEGACY_RELATION_TYPES:
        raise ValueError(f"relation_type must be one of {list(_LEGACY_RELATION_TYPES)} for Plane 1.3.1.")
    client.work_items.relations._post(
        f"{workspace_slug}/projects/{project_id}/work-items/{work_item_id}/relations/remove",
        {
            "related_issue": related_work_item_id,
            "relation_type": relation_type,
        },
    )
    current = _legacy_list_relations(client, workspace_slug, project_id, work_item_id)
    if any(str(item.id) == related_work_item_id for item in current[relation_type]):
        raise RuntimeError("Plane returned success but the relation still exists after read-back verification.")


def register_work_item_relation_tools(mcp: FastMCP) -> None:
    """Register work item relation tools with the MCP server."""

    @mcp.tool()
    def list_work_item_relations(
        project_id: str,
        work_item_id: str,
    ) -> dict[str, Any]:
        """List every relation for a work item.

        Args:
            project_id: UUID of the project.
            work_item_id: UUID of the work item.

        Returns:
            dependencies: Built-in dependencies grouped by the six directions.
            custom: Custom relations grouped by definition label.
            other: Legacy duplicate/relates_to relations when using Plane 1.3.1.
            capabilities: Relation capabilities for the active API profile.
        """
        client, workspace_slug = get_plane_client_context()
        if uses_legacy_api(client, probe=True):
            legacy = _legacy_list_relations(client, workspace_slug, project_id, work_item_id)
            return {
                "dependencies": {key: [item.model_dump() for item in legacy[key]] for key in _DEPENDENCY_TYPES},
                "custom": {},
                "other": {key: [item.model_dump() for item in legacy[key]] for key in _LEGACY_EXTRA_TYPES},
                "capabilities": compatibility_capabilities(client, probe=False)["capabilities"],
            }

        try:
            dependencies = client.work_items.dependencies.list(
                workspace_slug=workspace_slug,
                project_id=project_id,
                work_item_id=work_item_id,
            )
        except HttpError as exc:
            if exc.status_code != 404:
                raise
            mark_legacy_api(client)
            legacy = _legacy_list_relations(client, workspace_slug, project_id, work_item_id)
            return {
                "dependencies": {key: [item.model_dump() for item in legacy[key]] for key in _DEPENDENCY_TYPES},
                "custom": {},
                "other": {key: [item.model_dump() for item in legacy[key]] for key in _LEGACY_EXTRA_TYPES},
                "capabilities": compatibility_capabilities(client, probe=False)["capabilities"],
            }

        try:
            custom = client.work_items.custom_relations.list(
                workspace_slug=workspace_slug,
                project_id=project_id,
                work_item_id=work_item_id,
            )
            custom_dump = {label: [item.model_dump() for item in items] for label, items in custom.items()}
        except HttpError as exc:
            if exc.status_code != 404:
                raise
            custom_dump = {}
        return {
            "dependencies": dependencies.model_dump(),
            "custom": custom_dump,
            "other": {},
            "capabilities": compatibility_capabilities(client, probe=False)["capabilities"],
        }

    @mcp.tool()
    def create_work_item_relation(
        project_id: str,
        work_item_id: str,
        work_item_ids: list[str],
        relation_type: str | None = None,
        relation_definition_id: str | None = None,
        relation_definition_label: str | None = None,
    ) -> list[WorkItemWithRelationType]:
        """Relate a work item to one or more targets.

        Always call list_work_item_relation_definitions first and match the user's
        wording to an entry there. If it is a built_in_dependencies value, pass it
        as relation_type. If it is a custom_definitions entry, pass that
        definition's id as relation_definition_id and the matched outward/inward
        label as relation_definition_label (the label sets directionality).

        Args:
            project_id: UUID of the project.
            work_item_id: UUID of the source work item.
            work_item_ids: UUIDs of the target work items.
            relation_type: A built_in_dependencies value, or None for a custom relation.
            relation_definition_id: UUID of the relation definition (custom relations).
            relation_definition_label: Definition's outward or inward label (custom relations).

        Returns:
            List of created WorkItemWithRelationType objects.
        """
        client, workspace_slug = get_plane_client_context()
        if relation_type:
            legacy = uses_legacy_api(client, probe=relation_type in _LEGACY_EXTRA_TYPES)
            allowed_types = _LEGACY_RELATION_TYPES if legacy else _DEPENDENCY_TYPES
            if relation_type not in allowed_types:
                raise ValueError(
                    f"relation_type must be one of {list(allowed_types)}. For any "
                    "other relationship, pass relation_definition_id + "
                    "relation_definition_label from list_work_item_relation_definitions."
                )
            if legacy:
                return _legacy_create_relation(
                    client,
                    workspace_slug,
                    project_id,
                    work_item_id,
                    relation_type,
                    work_item_ids,
                )
            try:
                return client.work_items.dependencies.create(
                    workspace_slug=workspace_slug,
                    project_id=project_id,
                    work_item_id=work_item_id,
                    data=CreateWorkItemDependency(
                        relation_type=relation_type,  # type: ignore[arg-type]
                        work_item_ids=work_item_ids,
                    ),
                )
            except HttpError as exc:
                if exc.status_code != 404:
                    raise
                mark_legacy_api(client)
                return _legacy_create_relation(
                    client,
                    workspace_slug,
                    project_id,
                    work_item_id,
                    relation_type,
                    work_item_ids,
                )
        if relation_definition_id and relation_definition_label:
            if uses_legacy_api(client, probe=True):
                raise PlaneCompatibilityError("Plane 1.3.1 does not expose custom relations through its public API.")
            try:
                return client.work_items.custom_relations.create(
                    workspace_slug=workspace_slug,
                    project_id=project_id,
                    work_item_id=work_item_id,
                    data=CreateWorkItemCustomRelation(
                        relation_definition_id=relation_definition_id,
                        relation_definition_type=relation_definition_label,
                        work_item_ids=work_item_ids,
                    ),
                )
            except HttpError as exc:
                if exc.status_code != 404:
                    raise
                mark_legacy_api(client)
                raise PlaneCompatibilityError(
                    "This Plane server does not expose custom relations through its public API."
                ) from exc
        raise ValueError(
            "Provide relation_type for a built-in dependency, or "
            "relation_definition_id + relation_definition_label for a custom "
            "relation (call list_work_item_relation_definitions to find one)."
        )

    @mcp.tool()
    def remove_work_item_relation(
        project_id: str,
        work_item_id: str,
        related_work_item_id: str,
        is_dependency: bool,
        relation_type: str | None = None,
    ) -> None:
        """Remove ONE relation between two work items.

        A built-in dependency and a custom relation are removed independently —
        removing one leaves the other intact. Set is_dependency from the relation
        the user named (see list_work_item_relations): True for a built-in
        dependency (blocking, blocked_by, start/finish ordering), False for a
        custom relation.

        Args:
            project_id: UUID of the project.
            work_item_id: UUID of the source work item.
            related_work_item_id: UUID of the related work item.
            is_dependency: True to remove a built-in dependency, False to remove a
                custom relation.
            relation_type: Required on Plane 1.3.1 so a server backport can
                remove exactly one relation type without deleting another
                relation between the same work items.
        """
        client, workspace_slug = get_plane_client_context()
        if uses_legacy_api(client, probe=True):
            if not is_dependency:
                raise PlaneCompatibilityError("Plane 1.3.1 does not expose custom relations through its public API.")
            return _legacy_remove_relation(
                client,
                workspace_slug,
                project_id,
                work_item_id,
                related_work_item_id,
                relation_type,
            )
        remove = client.work_items.dependencies.remove if is_dependency else client.work_items.custom_relations.remove
        try:
            remove(
                workspace_slug=workspace_slug,
                project_id=project_id,
                work_item_id=work_item_id,
                related_work_item_id=related_work_item_id,
            )
        except HttpError as exc:
            if exc.status_code != 404 or not is_dependency:
                raise
            mark_legacy_api(client)
            return _legacy_remove_relation(
                client,
                workspace_slug,
                project_id,
                work_item_id,
                related_work_item_id,
                relation_type,
            )
