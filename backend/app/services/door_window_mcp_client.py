from __future__ import annotations

import re
from pathlib import Path
from typing import BinaryIO

import httpx

from app.config import settings


_DRAWING_SESSION_ID = re.compile(r"^drawing_[0-9a-f]{32}$")


async def register_uploaded_pdf(source: BinaryIO, filename: str) -> dict[str, object]:
    """Register a PDF in the LAN thumbnail service and return its drawing session."""
    base_url = settings.door_window_mcp_url.rstrip("/")
    if base_url.endswith("/mcp"):
        base_url = base_url[:-4]
    if not base_url:
        raise RuntimeError("Door/window MCP URL is not configured")

    source.seek(0)
    headers = {}
    if settings.door_window_mcp_token:
        headers["Authorization"] = f"Bearer {settings.door_window_mcp_token}"

    timeout = httpx.Timeout(settings.door_window_mcp_timeout_seconds)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{base_url}/api/drawings/upload",
                headers=headers,
                files={"file": (Path(filename).name or "drawing.pdf", source, "application/pdf")},
            )
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise RuntimeError("Unable to register PDF with the door/window MCP") from exc

    session_id = payload.get("session_id") if isinstance(payload, dict) else None
    if not isinstance(session_id, str) or not _DRAWING_SESSION_ID.fullmatch(session_id):
        raise RuntimeError("Door/window MCP returned an invalid drawing session")
    return payload
