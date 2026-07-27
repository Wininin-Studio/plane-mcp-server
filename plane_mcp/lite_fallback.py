"""Fallback helpers for Plane servers without ``-lite`` API routes."""

from collections.abc import Callable
from typing import Any, TypeVar

from plane.errors.errors import HttpError
from pydantic import BaseModel

TLite = TypeVar("TLite", bound=BaseModel)


def lite_or_fallback(
    lite_call: Callable[[], TLite],
    full_call: Callable[[], Any],
    lite_item_cls: type[BaseModel],
    lite_response_cls: type[TLite],
) -> TLite:
    """Retry a missing ``-lite`` endpoint through its full equivalent.

    Plane 1.3.1 Community Edition does not register several ``-lite`` routes
    used by newer versions of the Python SDK. The corresponding full routes
    return either a paginated envelope or a bare list. Normalize both shapes
    to the lite response advertised by the MCP tool.
    """
    try:
        return lite_call()
    except HttpError as exc:
        if exc.status_code != 404:
            raise

    full = full_call()
    items = full if isinstance(full, list) else full.results
    results = [lite_item_cls.model_validate(item.model_dump()) for item in items]

    if isinstance(full, list):
        return lite_response_cls.model_validate(
            {
                "results": results,
                "total_count": len(results),
                "next_cursor": "",
                "prev_cursor": "",
                "next_page_results": False,
                "prev_page_results": False,
                "count": len(results),
                "total_pages": 1,
                "total_results": len(results),
            }
        )

    return lite_response_cls.model_validate({**full.model_dump(), "results": results})
