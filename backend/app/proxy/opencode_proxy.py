import base64
import asyncio
import json
import re
from urllib.parse import urlencode

import httpx
from fastapi import HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse
from starlette.requests import ClientDisconnect
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User, UserSession, UserWorkspace


SESSION_PATH = re.compile(r"^session/([^/]+)(?:/|$)")
PUBLIC_PROXY_PATHS = {
    "session",
    "session/status",
    "provider",
    "agent",
    "app/agents",
    "global/config",
    "global/event",
    "global/health",
    "path",
    "project",
}
_session_claim_lock = asyncio.Lock()


def basic_auth_header(sandbox: dict) -> dict:
    if not sandbox.get("password"):
        return {}
    cred = base64.b64encode(f"{sandbox['username']}:{sandbox['password']}".encode()).decode()
    return {"Authorization": f"Basic {cred}"}


def filter_request_headers(request: Request) -> dict:
    skip = {
        "host",
        "origin",
        "referer",
        "connection",
        "content-length",
        "transfer-encoding",
        "authorization",
    }
    return {key: value for key, value in request.headers.items() if key.lower() not in skip}


def _target_url(path: str, request: Request, sandbox: dict, directory: str) -> str:
    params = [(key, value) for key, value in request.query_params.multi_items() if key not in {"directory", "workspace"}]
    if not path.startswith("global/"):
        params.append(("directory", directory))
    query = urlencode(params, doseq=True)
    return f"{sandbox['url']}/{path}" + (f"?{query}" if query else "")


async def _require_owned_session(path: str, user: User, db: AsyncSession) -> str | None:
    if path.rstrip("/") == "session/status":
        return None
    match = SESSION_PATH.match(path)
    if not match:
        return None
    session_id = match.group(1)
    result = await db.execute(
        select(UserSession.session_id).where(
            UserSession.session_id == session_id,
            UserSession.user_id == user.id,
        )
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    return session_id


def _json_response(payload: object, source: httpx.Response) -> Response:
    headers = {
        key: value
        for key, value in source.headers.items()
        if key.lower() not in {"transfer-encoding", "connection", "content-encoding", "content-length", "content-type"}
    }
    return Response(
        content=json.dumps(payload, ensure_ascii=False).encode(),
        status_code=source.status_code,
        headers=headers,
        media_type="application/json",
    )


async def _record_sessions(
    payload: object,
    user: User,
    workspace: UserWorkspace,
    db: AsyncSession,
) -> list[dict]:
    if not isinstance(payload, list):
        return []
    visible = [item for item in payload if isinstance(item, dict) and item.get("directory") == workspace.directory]
    for item in visible:
        session_id = item.get("id")
        if isinstance(session_id, str):
            await _claim_session(session_id, user, workspace, db)
    return visible


async def _record_created_session(
    payload: object,
    user: User,
    workspace: UserWorkspace,
    db: AsyncSession,
) -> None:
    if not isinstance(payload, dict):
        return
    session_id = payload.get("id")
    if not isinstance(session_id, str):
        return
    await _claim_session(session_id, user, workspace, db)


async def _claim_session(
    session_id: str,
    user: User,
    workspace: UserWorkspace,
    db: AsyncSession,
) -> None:
    async with _session_claim_lock:
        result = await db.execute(select(UserSession).where(UserSession.session_id == session_id))
        existing = result.scalar_one_or_none()
        if existing:
            if existing.user_id != user.id or existing.workspace_id != workspace.id:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Session ownership conflict")
            return
        db.add(
            UserSession(
                session_id=session_id,
                user_id=user.id,
                workspace_id=workspace.id,
                sandbox_id=workspace.sandbox_id,
            )
        )
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            result = await db.execute(select(UserSession).where(UserSession.session_id == session_id))
            existing = result.scalar_one_or_none()
            if not existing or existing.user_id != user.id or existing.workspace_id != workspace.id:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Session ownership conflict")


def _filter_status(payload: object, owned: set[str]) -> object:
    if isinstance(payload, dict):
        return {key: value for key, value in payload.items() if key in owned}
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict) and item.get("id") in owned]
    return payload


def _filter_projects(payload: object, directory: str) -> list[dict]:
    if not isinstance(payload, list):
        return []
    return [
        item
        for item in payload
        if isinstance(item, dict) and item.get("worktree") == directory
    ]


def _filter_sse_record(record: bytes, directory: str) -> bytes:
    data_lines = []
    for line in record.splitlines():
        if line.startswith(b"data:"):
            data_lines.append(line[5:].lstrip())
    if not data_lines:
        return record + b"\n\n"
    try:
        event = json.loads(b"\n".join(data_lines))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return b""
    if not isinstance(event, dict):
        return b""
    payload = event.get("payload")
    event_type = payload.get("type") if isinstance(payload, dict) else None
    if event_type not in {"server.connected", "server.heartbeat", "global.disposed"} and event.get("directory") != directory:
        return b""
    return record + b"\n\n"


async def _iter_filtered_sse(chunks, directory: str):
    buffer = b""
    async for chunk in chunks:
        buffer = (buffer + chunk).replace(b"\r\n", b"\n")
        while b"\n\n" in buffer:
            record, buffer = buffer.split(b"\n\n", 1)
            filtered = _filter_sse_record(record, directory)
            if filtered:
                yield filtered


def _secure_request_body(path: str, method: str, body: bytes) -> bytes:
    normalized = path.rstrip("/")
    is_create = method == "POST" and normalized == "session"
    is_update = method in {"PATCH", "PUT"} and bool(re.fullmatch(r"session/[^/]+", normalized))
    if not is_create and not is_update:
        return body
    try:
        payload = json.loads(body) if body else {}
    except (json.JSONDecodeError, UnicodeDecodeError):
        return body
    if not isinstance(payload, dict):
        return body
    payload["permission"] = [
        {"permission": "external_directory", "pattern": "*", "action": "deny"},
    ]
    return json.dumps(payload, ensure_ascii=False).encode()


async def proxy_to_sandbox(
    path: str,
    request: Request,
    sandbox: dict,
    user: User,
    workspace: UserWorkspace,
    db: AsyncSession,
) -> Response:
    normalized_path = path.rstrip("/")
    if normalized_path not in PUBLIC_PROXY_PATHS and not SESSION_PATH.match(normalized_path):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Endpoint not available")
    if re.fullmatch(r"session/[^/]+/shell", normalized_path):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Endpoint not available")
    session_id = await _require_owned_session(path, user, db)
    directory = workspace.directory
    target_url = _target_url(path, request, sandbox, directory)
    headers = {**filter_request_headers(request), **basic_auth_header(sandbox)}
    is_sse = path.startswith("global/event") or "text/event-stream" in request.headers.get("accept", "")

    # Release the connection before waiting on the sandbox. Later persistence
    # operations can check out a fresh connection from the same session.
    await db.commit()

    if is_sse:
        async def stream():
            timeout = httpx.Timeout(86400.0, connect=30.0)
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream("GET", target_url, headers=headers) as resp:
                    resp.raise_for_status()
                    async for record in _iter_filtered_sse(resp.aiter_raw(), directory):
                        yield record

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    try:
        raw_body = await request.body()
    except ClientDisconnect:
        return Response(status_code=499)

    body = _secure_request_body(path, request.method, raw_body)
    timeout = httpx.Timeout(300.0, connect=30.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.request(request.method, target_url, headers=headers, content=body)

    payload = None
    if "application/json" in resp.headers.get("content-type", ""):
        try:
            payload = resp.json()
        except json.JSONDecodeError:
            payload = None

    is_session_collection = path.rstrip("/") == "session"
    if resp.is_success and request.method == "GET" and is_session_collection:
        visible = await _record_sessions(payload, user, workspace, db)
        return _json_response(visible, resp)
    if resp.is_success and request.method == "POST" and is_session_collection:
        await _record_created_session(payload, user, workspace, db)
    if resp.is_success and request.method == "GET" and path.rstrip("/") == "session/status":
        result = await db.execute(select(UserSession.session_id).where(UserSession.user_id == user.id))
        return _json_response(_filter_status(payload, set(result.scalars().all())), resp)
    if resp.is_success and request.method == "GET" and normalized_path == "project":
        return _json_response(_filter_projects(payload, workspace.directory), resp)
    if resp.is_success and request.method == "GET" and normalized_path == "path":
        return _json_response(
            {
                "home": workspace.directory,
                "state": workspace.directory,
                "config": workspace.directory,
                "worktree": workspace.directory,
                "directory": workspace.directory,
            },
            resp,
        )
    if (
        resp.is_success
        and request.method == "DELETE"
        and session_id
        and normalized_path == f"session/{session_id}"
    ):
        await db.execute(
            delete(UserSession).where(
                UserSession.session_id == session_id,
                UserSession.user_id == user.id,
            )
        )
        await db.commit()

    excluded_headers = {"transfer-encoding", "connection", "content-encoding", "content-length"}
    response_headers = {key: value for key, value in resp.headers.items() if key.lower() not in excluded_headers}
    return Response(
        content=resp.content,
        status_code=resp.status_code,
        headers=response_headers,
        media_type=resp.headers.get("content-type"),
    )
