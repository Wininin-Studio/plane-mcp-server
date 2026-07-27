"""Plane API compatibility profile discovery and capability reporting."""

from __future__ import annotations

import os
from typing import Any

from plane import PlaneClient

PLANE_1_3_1_PROFILE = "1.3.1"
LEGACY_API_PROFILE = "legacy"
_PROFILE_ENV = "PLANE_API_COMPAT"
_RELATION_REMOVE_ENV = "PLANE_RELATION_REMOVE_SUPPORTED"
_PAGES_API_ENV = "PLANE_PAGES_API_SUPPORTED"
_INSTANCE_CACHE: dict[str, dict[str, Any]] = {}
_LEGACY_BASE_PATHS: set[str] = set()


class PlaneCompatibilityError(RuntimeError):
    """A Plane operation is unavailable for the selected API profile."""


def _normalized_profile(value: str | None) -> str:
    profile = (value or "auto").strip().lower()
    aliases = {
        "v1.3.1": PLANE_1_3_1_PROFILE,
        "plane-1.3.1": PLANE_1_3_1_PROFILE,
        "ce-1.3.1": PLANE_1_3_1_PROFILE,
    }
    return aliases.get(profile, profile)


def configured_profile() -> str:
    """Return the configured compatibility profile (``auto`` by default)."""
    return _normalized_profile(os.getenv(_PROFILE_ENV))


def relation_remove_supported() -> bool:
    """Whether a self-hosted server has the optional public remove backport."""
    return os.getenv(_RELATION_REMOVE_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def pages_api_supported() -> bool:
    """Whether a self-hosted server has the optional public Pages backport."""
    return os.getenv(_PAGES_API_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def _api_base_path(client: PlaneClient) -> str:
    return client.work_items.config.base_path.rstrip("/")


def mark_legacy_api(client: PlaneClient) -> None:
    """Remember that a client's origin requires legacy endpoint fallbacks."""
    _LEGACY_BASE_PATHS.add(_api_base_path(client))


def discover_instance_version(client: PlaneClient) -> dict[str, Any]:
    """Read the public Plane instance metadata without exposing configuration.

    Plane serves instance metadata at ``/api/instances/`` rather than below
    ``/api/v1``. Only the current version is retained and returned.
    """
    api_base = _api_base_path(client)
    cached = _INSTANCE_CACHE.get(api_base)
    if cached is not None:
        return cached

    suffix = "/api/v1"
    origin = api_base[: -len(suffix)] if api_base.lower().endswith(suffix) else api_base
    url = f"{origin.rstrip('/')}/api/instances/"
    resource = client.work_items

    try:
        response = resource.session.get(
            url,
            headers=resource._headers(),
            timeout=resource.config.timeout,
        )
        payload = resource._handle_response(response)
        instance = payload.get("instance", {}) if isinstance(payload, dict) else {}
        version = instance.get("current_version") if isinstance(instance, dict) else None
        result = {
            "version": str(version) if version else None,
            "source": "instance_api",
            "probe_error": None,
        }
    except Exception as exc:
        result = {
            "version": None,
            "source": "unavailable",
            "probe_error": type(exc).__name__,
        }

    _INSTANCE_CACHE[api_base] = result
    return result


def resolve_compatibility(client: PlaneClient, *, probe: bool = False) -> dict[str, Any]:
    """Resolve the active API profile from configuration and optional probing."""
    configured = configured_profile()
    api_base = _api_base_path(client)

    if configured == PLANE_1_3_1_PROFILE:
        mark_legacy_api(client)
        discovered = (
            discover_instance_version(client)
            if probe
            else {
                "version": None,
                "source": "not_probed",
                "probe_error": None,
            }
        )
        return {
            "profile": PLANE_1_3_1_PROFILE,
            "version": discovered["version"],
            "source": discovered["source"],
            "probe_error": discovered["probe_error"],
        }

    if configured != "auto":
        discovered = (
            discover_instance_version(client)
            if probe
            else {
                "version": None,
                "source": "not_probed",
                "probe_error": None,
            }
        )
        return {
            "profile": configured,
            "version": discovered["version"],
            "source": discovered["source"],
            "probe_error": discovered["probe_error"],
        }

    if probe:
        discovered = discover_instance_version(client)
        version = discovered["version"]
        if isinstance(version, str):
            if version.lstrip("v").startswith("1.3.1"):
                mark_legacy_api(client)
                profile = PLANE_1_3_1_PROFILE
            elif api_base in _LEGACY_BASE_PATHS:
                profile = LEGACY_API_PROFILE
            else:
                profile = "modern"
            return {
                "profile": profile,
                "version": version,
                "source": discovered["source"],
                "probe_error": discovered["probe_error"],
            }
    else:
        discovered = {
            "version": None,
            "source": "not_probed",
            "probe_error": None,
        }

    if api_base in _LEGACY_BASE_PATHS:
        return {
            "profile": LEGACY_API_PROFILE,
            "version": None,
            "source": "endpoint_fallback",
            "probe_error": discovered["probe_error"],
        }

    return {
        "profile": "auto",
        "version": None,
        "source": discovered["source"],
        "probe_error": discovered["probe_error"],
    }


def uses_legacy_api(client: PlaneClient, *, probe: bool = False) -> bool:
    """Return whether the client should use the legacy compatibility path."""
    should_probe = probe and configured_profile() == "auto"
    return resolve_compatibility(client, probe=should_probe)["profile"] in {
        PLANE_1_3_1_PROFILE,
        LEGACY_API_PROFILE,
    }


def ensure_pql_supported(client: PlaneClient, *, fallback_hint: str) -> None:
    """Reject PQL unless the server is positively known to support it."""
    resolved = resolve_compatibility(client, probe=configured_profile() == "auto")
    if resolved["profile"] in {PLANE_1_3_1_PROFILE, LEGACY_API_PROFILE}:
        raise PlaneCompatibilityError(f"This Plane API silently ignores PQL. {fallback_hint}")
    if resolved["profile"] == "auto":
        raise PlaneCompatibilityError(
            "Could not verify that this Plane server supports PQL. Set PLANE_API_COMPAT explicitly; " + fallback_hint
        )


def compatibility_capabilities(client: PlaneClient, *, probe: bool = True) -> dict[str, Any]:
    """Return stable capability metadata for agents before they write."""
    resolved = resolve_compatibility(client, probe=probe)
    legacy = resolved["profile"] in {PLANE_1_3_1_PROFILE, LEGACY_API_PROFILE}
    unknown = resolved["profile"] == "auto"
    relation_remove = relation_remove_supported() if legacy else None if unknown else True
    pages_api = pages_api_supported() if legacy else None if unknown else True
    capabilities: dict[str, bool | None] = {
        "project_and_member_fallbacks": True,
        "project_scoped_work_items": True,
        "structured_work_item_filters": True,
        "workspace_scoped_work_items": False if legacy else None if unknown else True,
        "pql": False if legacy else None if unknown else True,
        "built_in_relation_list": True,
        "built_in_relation_create": relation_remove if legacy else None if unknown else True,
        "built_in_relation_remove": relation_remove,
        "custom_relations": False if legacy else None if unknown else True,
        "relation_definitions": False if legacy else None if unknown else True,
        "pages_public_api": pages_api,
    }
    return {
        "configured_profile": configured_profile(),
        "resolved_profile": resolved["profile"],
        "server_version": resolved["version"],
        "version_source": resolved["source"],
        "version_probe_error": resolved["probe_error"],
        "capabilities": capabilities,
    }
