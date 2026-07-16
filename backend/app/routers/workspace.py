import base64
import binascii
import asyncio
import json
import mimetypes
import tempfile
import time
import zipfile
from pathlib import PurePosixPath
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.background import BackgroundTask

from app.auth.dependencies import get_current_user
from app.database import get_db
from app.models import User, UserWorkspace
from app.proxy.opencode_proxy import basic_auth_header
from app.sandbox.manager import (
    ensure_workspace,
    get_or_create_workspace,
    run_sandbox_command,
    run_sandbox_command_capture,
)


router = APIRouter(prefix="/workspace", tags=["workspace"])

MAX_ARCHIVE_FILES = 2000
MAX_ARCHIVE_BYTES = 200 * 1024 * 1024
MAX_ARCHIVE_DEPTH = 24
DIRECTORY_JSON_START = "__OPENCODE_DIRECTORY_JSON_START__"
DIRECTORY_JSON_END = "__OPENCODE_DIRECTORY_JSON_END__"
FILE_CHECK_START = "__OPENCODE_FILE_CHECK_START__"
FILE_CHECK_END = "__OPENCODE_FILE_CHECK_END__"
DIRECTORY_LIST_SCRIPT = (
    "const fs=require('fs');"
    "const path=require('path');"
    "const root=fs.realpathSync(process.argv[1]);"
    "const target=fs.realpathSync(process.argv[2]);"
    "if(target!==root&&!target.startsWith(root+path.sep))throw new Error('outside workspace');"
    "const items=fs.readdirSync(target,{withFileTypes:true})"
    ".filter(item=>!item.isSymbolicLink()).map(item=>({"
    "name:item.name,type:item.isDirectory()?'directory':'file'}));"
    "setTimeout(()=>{"
    f"process.stdout.write('{DIRECTORY_JSON_START}'+JSON.stringify(items)+'{DIRECTORY_JSON_END}');"
    "setTimeout(()=>{},300);"
    "},500);"
)
FILE_CHECK_SCRIPT = (
    "const fs=require('fs');"
    "const path=require('path');"
    "const root=fs.realpathSync(process.argv[1]);"
    "const target=fs.realpathSync(process.argv[2]);"
    "const valid=(target===root||target.startsWith(root+path.sep))&&fs.statSync(target).isFile();"
    "setTimeout(()=>{"
    f"process.stdout.write('{FILE_CHECK_START}'+JSON.stringify(valid)+'{FILE_CHECK_END}');"
    "setTimeout(()=>{},300);"
    "},500);"
)
_directory_locks: dict[str, asyncio.Lock] = {}
_directory_cache: dict[str, tuple[float, list[dict]]] = {}


class DeleteWorkspaceEntry(BaseModel):
    path: str


def safe_relative_path(value: str, allow_root: bool = False) -> str:
    if "\x00" in value or "\\" in value:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid workspace path")
    if value in {"", "."}:
        if allow_root:
            return ""
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="A file path is required")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid workspace path")
    return str(path)


def normalize_node(item: object) -> dict | None:
    if not isinstance(item, dict):
        return None
    name = item.get("name")
    path = item.get("path")
    node_type = item.get("type")
    if not isinstance(name, str) or not isinstance(path, str) or node_type not in {"file", "directory"}:
        return None
    try:
        normalized = safe_relative_path(path.replace("\\", "/"))
    except HTTPException:
        return None
    return {
        "name": name,
        "path": normalized,
        "type": node_type,
        "ignored": bool(item.get("ignored", False)),
    }


def decode_file_content(payload: object) -> tuple[bytes, str]:
    if not isinstance(payload, dict) or payload.get("type") not in {"text", "binary"}:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Invalid workspace response")
    content = payload.get("content")
    if not isinstance(content, str):
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Invalid workspace response")
    mime_type = payload.get("mimeType")
    media_type = mime_type if isinstance(mime_type, str) and mime_type else "application/octet-stream"
    if payload.get("encoding") == "base64" or payload.get("type") == "binary":
        try:
            return base64.b64decode(content), media_type
        except (binascii.Error, ValueError) as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Invalid binary file content") from exc
    return content.encode("utf-8"), media_type if media_type != "application/octet-stream" else "text/plain; charset=utf-8"


def content_disposition(filename: str) -> str:
    safe_ascii = "".join(character if character.isascii() and character not in {'"', "\\"} else "_" for character in filename)
    safe_ascii = safe_ascii or "download"
    return f'attachment; filename="{safe_ascii}"; filename*=UTF-8\'\'{quote(filename)}'


async def workspace_context(user: User, db: AsyncSession) -> tuple[dict, UserWorkspace]:
    sandbox, workspace = await get_or_create_workspace(user, db)
    await ensure_workspace(sandbox, workspace, db)
    return sandbox, workspace


async def remote_json(
    client: httpx.AsyncClient,
    sandbox: dict,
    workspace: UserWorkspace,
    endpoint: str,
    path: str,
    allow_root: bool = False,
) -> object:
    try:
        response = await client.get(
            f"{sandbox['url']}/{endpoint}",
            params={"directory": workspace.directory, "path": safe_relative_path(path, allow_root=allow_root)},
            headers=basic_auth_header(sandbox),
        )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Workspace service unavailable") from exc
    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail="Unable to read workspace")
    try:
        return response.json()
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Invalid workspace response") from exc


async def workspace_request(
    endpoint: str,
    path: str,
    user: User,
    db: AsyncSession,
    allow_root: bool = False,
) -> object:
    sandbox, workspace = await workspace_context(user, db)
    async with httpx.AsyncClient(timeout=30.0) as client:
        return await remote_json(client, sandbox, workspace, endpoint, path, allow_root=allow_root)


def remote_target(directory: str, relative_path: str) -> str:
    if not relative_path:
        return directory.rstrip("/\\")
    separator = "\\" if "\\" in directory else "/"
    return directory.rstrip("/\\") + separator + relative_path.replace("/", separator)


async def remote_directory_entries(
    sandbox: dict,
    workspace: UserWorkspace,
    path: str,
    fresh: bool = False,
) -> list[dict]:
    relative_path = safe_relative_path(path, allow_root=True)
    key = f"{sandbox['id']}\0{workspace.directory}\0{relative_path}"
    lock = _directory_locks.setdefault(key, asyncio.Lock())
    async with lock:
        cached = _directory_cache.get(key)
        if not fresh and cached and time.monotonic() - cached[0] < 0.75:
            return cached[1]
        for attempt in range(2):
            try:
                result = await _read_remote_directory(sandbox, workspace, relative_path)
                _directory_cache[key] = (time.monotonic(), result)
                return result
            except HTTPException:
                if attempt == 1:
                    raise
                await asyncio.sleep(0.15)
    raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Unable to list workspace")


async def _read_remote_directory(
    sandbox: dict,
    workspace: UserWorkspace,
    relative_path: str,
) -> list[dict]:
    target = remote_target(workspace.directory, relative_path)
    exit_code, output = await run_sandbox_command_capture(
        sandbox=sandbox,
        directory=workspace.directory,
        command="node",
        args=["-e", DIRECTORY_LIST_SCRIPT, workspace.directory, target],
        cwd=workspace.directory,
        title=f"List {relative_path or 'workspace'}",
    )
    # Windows PTYs hard-wrap long output at the terminal width.
    output = output.replace("\r\n", "")
    start = output.find(DIRECTORY_JSON_START)
    end = output.find(DIRECTORY_JSON_END, start + len(DIRECTORY_JSON_START))
    if exit_code != 0 or start < 0 or end < 0:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Unable to list workspace")
    try:
        payload = json.loads(output[start + len(DIRECTORY_JSON_START):end])
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Invalid workspace response") from exc
    if not isinstance(payload, list):
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Invalid workspace response")

    result: list[dict] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        node_type = item.get("type")
        if not isinstance(name, str) or not name or name in {".", ".."} or "/" in name or "\\" in name:
            continue
        if node_type not in {"file", "directory"}:
            continue
        node_path = f"{relative_path}/{name}" if relative_path else name
        result.append({"name": name, "path": node_path, "type": node_type, "ignored": False})
    return result


async def validate_remote_file(sandbox: dict, workspace: UserWorkspace, relative_path: str) -> None:
    target = remote_target(workspace.directory, relative_path)
    exit_code, output = await run_sandbox_command_capture(
        sandbox=sandbox,
        directory=workspace.directory,
        command="node",
        args=["-e", FILE_CHECK_SCRIPT, workspace.directory, target],
        cwd=workspace.directory,
        title=f"Validate {relative_path}",
    )
    output = output.replace("\r\n", "")
    start = output.find(FILE_CHECK_START)
    end = output.find(FILE_CHECK_END, start + len(FILE_CHECK_START))
    if exit_code != 0 or start < 0 or end < 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace file not found")
    if output[start + len(FILE_CHECK_START):end] != "true":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid workspace file")


@router.get("")
async def get_workspace(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _, workspace = await workspace_context(user, db)
    return {
        "id": workspace.id,
        "sandboxId": workspace.sandbox_id,
        "directory": workspace.directory,
    }


@router.get("/files")
async def list_workspace_files(
    path: str = Query(""),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    sandbox, workspace = await workspace_context(user, db)
    return await remote_directory_entries(sandbox, workspace, path)


@router.get("/content")
async def read_workspace_file(
    path: str = Query(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    relative_path = safe_relative_path(path)
    sandbox, workspace = await workspace_context(user, db)
    await validate_remote_file(sandbox, workspace, relative_path)
    async with httpx.AsyncClient(timeout=30.0) as client:
        payload = await remote_json(client, sandbox, workspace, "file/content", relative_path)
    if not isinstance(payload, dict):
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Invalid workspace response")
    return payload


@router.get("/download")
async def download_workspace_file(
    path: str = Query(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    relative_path = safe_relative_path(path)
    sandbox, workspace = await workspace_context(user, db)
    await validate_remote_file(sandbox, workspace, relative_path)
    async with httpx.AsyncClient(timeout=30.0) as client:
        payload = await remote_json(client, sandbox, workspace, "file/content", relative_path)
    content, payload_media_type = decode_file_content(payload)
    filename = PurePosixPath(relative_path).name
    guessed_media_type = mimetypes.guess_type(filename)[0]
    return Response(
        content=content,
        media_type=payload_media_type or guessed_media_type or "application/octet-stream",
        headers={"Content-Disposition": content_disposition(filename)},
    )


@router.get("/archive")
async def download_workspace_archive(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    sandbox, workspace = await workspace_context(user, db)
    spool = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b")
    file_count = 0
    total_bytes = 0
    visited: set[str] = set()

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            with zipfile.ZipFile(spool, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
                async def add_directory(path: str, depth: int) -> None:
                    nonlocal file_count, total_bytes
                    if depth > MAX_ARCHIVE_DEPTH:
                        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Workspace is too deep to archive")
                    if path in visited:
                        return
                    visited.add(path)
                    nodes = await remote_directory_entries(sandbox, workspace, path)
                    if path and not nodes:
                        archive.writestr(path.rstrip("/") + "/", b"")
                    for node in nodes:
                        if node["type"] == "directory":
                            await add_directory(node["path"], depth + 1)
                            continue
                        file_count += 1
                        if file_count > MAX_ARCHIVE_FILES:
                            raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Workspace has too many files")
                        content_payload = await remote_json(client, sandbox, workspace, "file/content", node["path"])
                        content, _ = decode_file_content(content_payload)
                        total_bytes += len(content)
                        if total_bytes > MAX_ARCHIVE_BYTES:
                            raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Workspace is too large to archive")
                        archive.writestr(node["path"], content)

                await add_directory("", 0)
        spool.seek(0, 2)
        content_length = spool.tell()
        spool.seek(0)
    except Exception:
        spool.close()
        raise

    def stream_archive():
        while chunk := spool.read(64 * 1024):
            yield chunk

    filename = f"workspace-{workspace.id[:8]}.zip"
    return StreamingResponse(
        stream_archive(),
        media_type="application/zip",
        headers={
            "Content-Disposition": content_disposition(filename),
            "Content-Length": str(content_length),
        },
        background=BackgroundTask(spool.close),
    )


@router.delete("/files")
async def delete_workspace_entry(
    request: DeleteWorkspaceEntry,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    relative_path = safe_relative_path(request.path)
    parent = str(PurePosixPath(relative_path).parent)
    parent = "" if parent == "." else parent
    sandbox, workspace = await workspace_context(user, db)

    nodes = await remote_directory_entries(sandbox, workspace, parent, fresh=True)
    node = next((item for item in nodes if item["path"] == relative_path), None)
    if node is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace entry not found")
    if node["type"] == "directory":
        children = await remote_directory_entries(sandbox, workspace, relative_path, fresh=True)
        if children:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Directory is not empty")

    target = remote_target(workspace.directory, relative_path)
    windows = "\\" in workspace.directory or (len(workspace.directory) > 1 and workspace.directory[1] == ":")
    if windows:
        if node["type"] == "directory":
            script = (
                "& { param([string]$Target) "
                "if ((Get-ChildItem -LiteralPath $Target -Force | Select-Object -First 1)) { exit 3 }; "
                "Remove-Item -LiteralPath $Target -Force }"
            )
        else:
            script = "& { param([string]$Target) Remove-Item -LiteralPath $Target -Force }"
        command = "powershell.exe"
        args = ["-NoProfile", "-NonInteractive", "-Command", script, target]
    else:
        command = "/bin/rmdir" if node["type"] == "directory" else "/bin/rm"
        args = ["--", target]

    exit_code = await run_sandbox_command(
        sandbox=sandbox,
        directory=workspace.directory,
        command=command,
        args=args,
        cwd=workspace.directory,
        title=f"Delete {relative_path}",
    )
    if exit_code == 3 and node["type"] == "directory":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Directory is not empty")
    if exit_code != 0:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Unable to delete workspace entry")

    remaining = await remote_directory_entries(sandbox, workspace, parent, fresh=True)
    if any(item["path"] == relative_path for item in remaining):
        detail = "Directory is not empty" if node["type"] == "directory" else "Unable to delete workspace entry"
        code = status.HTTP_409_CONFLICT if node["type"] == "directory" else status.HTTP_502_BAD_GATEWAY
        raise HTTPException(status_code=code, detail=detail)
    return {"deleted": relative_path, "type": node["type"]}
