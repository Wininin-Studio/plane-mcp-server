"""Tools that expose the active Plane API compatibility contract."""

from fastmcp import FastMCP
from pydantic import BaseModel

from plane_mcp.client import get_plane_client_context
from plane_mcp.compatibility import compatibility_capabilities


class PlaneAPICompatibilityReport(BaseModel):
    """Serializable capability report for the connected Plane server."""

    configured_profile: str
    resolved_profile: str
    server_version: str | None
    version_source: str
    version_probe_error: str | None
    capabilities: dict[str, bool | None]


def register_compatibility_tools(mcp: FastMCP) -> None:
    """Register compatibility and server-version discovery tools."""

    @mcp.tool()
    def get_plane_api_capabilities(probe_instance: bool = True) -> PlaneAPICompatibilityReport:
        """Report the Plane server version and MCP compatibility capabilities.

        Call this before workflows that require PQL, workspace-wide work-item
        listing, custom relations, relation deletion, or Plane Pages. A false
        capability is authoritative: do not downgrade a workflow that depends
        on it.

        Args:
            probe_instance: Read Plane's public instance metadata when the
                compatibility profile is ``auto``. No workspace data is read.

        Returns:
            Configured/resolved profile, server version source, and a capability
            map. A false capability means callers must not downgrade the
            requested workflow.
        """
        client, _ = get_plane_client_context()
        return PlaneAPICompatibilityReport.model_validate(compatibility_capabilities(client, probe=probe_instance))
