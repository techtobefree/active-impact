"""Shared FastAPI dependencies and error helper.

Errors are FastAPI-native: HTTPException(status, detail=code) serializes to
{"detail": "<snake_case_code>"} exactly as the API contract specifies.
"""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException, Query


def api_error(status: int, code: str) -> HTTPException:
    """Build a {"detail": code} error. Raise the return value."""
    return HTTPException(status_code=status, detail=code)


@dataclass
class Page:
    limit: int
    offset: int


def pagination(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> Page:
    """Shared list pagination: ?limit (default 50, max 100) & ?offset."""
    return Page(limit=limit, offset=offset)
