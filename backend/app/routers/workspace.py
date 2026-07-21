import base64
import binascii
import asyncio
import codecs
import hashlib
import mimetypes
import re
import tempfile
import time
import unicodedata
import uuid
import zipfile
from contextlib import suppress
from pathlib import PurePosixPath
from typing import Annotated
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.background import BackgroundTask

from app.auth.dependencies import get_current_user
from app.database import get_db
from app.models import User, UserWorkspace
from app.proxy.opencode_proxy import basic_auth_header
from app.sandbox.manager import (
    delete_sandbox_files,
    ensure_workspace,
    get_or_create_workspace,
    replace_sandbox_file,
    run_sandbox_command,
    run_sandbox_command_capture,
    upload_sandbox_file,
)


router = APIRouter(prefix="/workspace", tags=["workspace"])

MAX_ARCHIVE_FILES = 2000
MAX_ARCHIVE_BYTES = 200 * 1024 * 1024
MAX_ARCHIVE_DEPTH = 24
MAX_UPLOAD_FILES = 5
MAX_UPLOAD_FILE_BYTES = 10 * 1024 * 1024
MAX_UPLOAD_TOTAL_BYTES = 50 * 1024 * 1024
UPLOAD_READ_BYTES = 64 * 1024
UPLOAD_SPOOL_BYTES = 1024 * 1024
MAX_UPLOAD_FILENAME_BYTES = 150
MAX_HTML_BYTES = 10 * 1024 * 1024
FILE_CHECK_START = "__OPENCODE_FILE_CHECK_START__"
FILE_CHECK_END = "__OPENCODE_FILE_CHECK_END__"
FILE_CHECK_SCRIPT = (
    "const fs=require('fs');"
    "const path=require('path');"
    "const root=fs.realpathSync(process.argv[1]);"
    "const target=fs.realpathSync(process.argv[2]);"
    "const valid=(target===root||target.startsWith(root+path.sep))&&fs.statSync(target).isFile();"
    f"process.stdout.write('{FILE_CHECK_START}'+JSON.stringify(valid)+'{FILE_CHECK_END}');"
)
_directory_locks: dict[str, asyncio.Lock] = {}
_directory_cache: dict[str, tuple[float, list[dict]]] = {}
_session_id_pattern = re.compile(r"[A-Za-z0-9_-]{1,128}")
_mime_type_pattern = re.compile(r"[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+")
_windows_device_pattern = re.compile(r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])", re.IGNORECASE)
_windows_filename_characters = set('<>:"/\\|?*')


class DeleteWorkspaceEntry(BaseModel):
    path: str


class WorkspaceHtmlSave(BaseModel):
    html: str
    revision: str = Field(pattern=r"^[0-9a-f]{64}$")


class WorkspaceHtmlMetadata(BaseModel):
    path: str
    filename: str
    revision: str
    size: int


class WorkspaceHtmlResponse(WorkspaceHtmlMetadata):
    html: str


def validate_upload_session_id(value: str) -> str:
    if not _session_id_pattern.fullmatch(value):
        raise ValueError("Invalid upload session_id")
    return value


def _truncate_utf8(value: str, maximum_bytes: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= maximum_bytes:
        return value
    return encoded[:maximum_bytes].decode("utf-8", errors="ignore")


def sanitize_upload_filename(value: str | None) -> str:
    basename = re.split(r"[/\\]", value or "")[-1]
    basename = unicodedata.normalize("NFKC", basename)
    cleaned = "".join(
        "_"
        if character in _windows_filename_characters or unicodedata.category(character).startswith("C")
        else character
        for character in basename
    ).strip()
    cleaned = cleaned.rstrip(". ")
    if not cleaned or cleaned in {".", ".."}:
        cleaned = "upload"
    device_stem = cleaned.split(".", 1)[0].rstrip(" ")
    if _windows_device_pattern.fullmatch(device_stem):
        cleaned = "_" + cleaned
    cleaned = _truncate_utf8(cleaned, MAX_UPLOAD_FILENAME_BYTES).rstrip(". ")
    return cleaned or "upload"


def normalize_declared_mime(value: str | None) -> str:
    candidate = (value or "").split(";", 1)[0].strip().lower()
    if len(candidate) <= 127 and _mime_type_pattern.fullmatch(candidate):
        return candidate
    return "application/octet-stream"


def _supported_magic(prefix: bytes) -> str | None:
    if prefix.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if prefix.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if prefix.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(prefix) >= 12 and prefix.startswith(b"RIFF") and prefix[8:12] == b"WEBP":
        return "image/webp"
    if prefix.startswith(b"%PDF-"):
        return "application/pdf"
    return None


def _classify_upload_content(
    prefix: bytes,
    declared_mime: str | None,
    utf8_valid: bool,
    has_nul: bool,
    control_characters: int,
    character_count: int,
) -> tuple[str, bool]:
    magic_mime = _supported_magic(prefix)
    if magic_mime:
        return magic_mime, True
    control_ratio = control_characters / max(character_count, 1)
    if utf8_valid and not has_nul and control_ratio <= 0.02:
        return "text/plain", True
    return normalize_declared_mime(declared_mime), False


def detect_upload_content(data: bytes, declared_mime: str | None = None) -> tuple[str, bool]:
    try:
        text = data.decode("utf-8", errors="strict")
        utf8_valid = True
    except UnicodeDecodeError:
        text = ""
        utf8_valid = False
    controls = sum(
        character not in "\t\r\n" and unicodedata.category(character) == "Cc"
        for character in text
    )
    return _classify_upload_content(
        data[:16],
        declared_mime,
        utf8_valid,
        "\x00" in text,
        controls,
        len(text),
    )


async def spool_upload_file(upload: UploadFile, total_before: int) -> dict:
    spool = tempfile.SpooledTemporaryFile(max_size=UPLOAD_SPOOL_BYTES, mode="w+b")
    digest = hashlib.sha256()
    decoder = codecs.getincrementaldecoder("utf-8")("strict")
    prefix = bytearray()
    size = 0
    utf8_valid = True
    has_nul = False
    control_characters = 0
    character_count = 0

    def inspect_text(chunk: bytes, final: bool = False) -> None:
        nonlocal utf8_valid, has_nul, control_characters, character_count
        if not utf8_valid:
            return
        try:
            text = decoder.decode(chunk, final=final)
        except UnicodeDecodeError:
            utf8_valid = False
            return
        has_nul = has_nul or "\x00" in text
        character_count += len(text)
        control_characters += sum(
            character not in "\t\r\n" and unicodedata.category(character) == "Cc"
            for character in text
        )

    try:
        while chunk := await upload.read(UPLOAD_READ_BYTES):
            size += len(chunk)
            if size > MAX_UPLOAD_FILE_BYTES:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail="An uploaded file exceeds 10 MiB",
                )
            if total_before + size > MAX_UPLOAD_TOTAL_BYTES:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail="The upload batch exceeds 50 MiB",
                )
            spool.write(chunk)
            digest.update(chunk)
            if len(prefix) < 16:
                prefix.extend(chunk[:16 - len(prefix)])
            inspect_text(chunk)
        inspect_text(b"", final=True)
        mime, direct = _classify_upload_content(
            bytes(prefix),
            upload.content_type,
            utf8_valid,
            has_nul,
            control_characters,
            character_count,
        )
        spool.seek(0)
        return {
            "name": sanitize_upload_filename(upload.filename),
            "spool": spool,
            "size": size,
            "sha256": digest.hexdigest(),
            "mime": mime,
            "direct": direct,
        }
    except BaseException:
        spool.close()
        raise


def invalidate_workspace_directory_cache(sandbox: dict, workspace: UserWorkspace) -> None:
    prefix = f"{sandbox['id']}\0{workspace.directory}\0"
    for key in [key for key in _directory_cache if key.startswith(prefix)]:
        _directory_cache.pop(key, None)


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


def safe_html_path(value: str) -> str:
    path = safe_relative_path(value)
    if PurePosixPath(path).suffix.lower() not in {".html", ".htm"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="An HTML file path is required")
    return path


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
    async with httpx.AsyncClient(timeout=30.0) as client:
        payload = await remote_json(client, sandbox, workspace, "file", relative_path, allow_root=True)
    if not isinstance(payload, list):
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Invalid workspace response")

    expected_parent = PurePosixPath(relative_path) if relative_path else PurePosixPath(".")
    result: list[dict] = []
    for item in payload:
        node = normalize_node(item)
        if not node or PurePosixPath(node["path"]).parent != expected_parent:
            continue
        if not node["name"] or node["name"] in {".", ".."} or "/" in node["name"] or "\\" in node["name"]:
            continue
        result.append(node)
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


async def read_remote_file_bytes(sandbox: dict, workspace: UserWorkspace, relative_path: str) -> bytes:
    await validate_remote_file(sandbox, workspace, relative_path)
    async with httpx.AsyncClient(timeout=30.0) as client:
        payload = await remote_json(client, sandbox, workspace, "file/content", relative_path)
    content, _ = decode_file_content(payload)
    return content


def workspace_html_metadata(path: str, content: bytes) -> WorkspaceHtmlMetadata:
    return WorkspaceHtmlMetadata(
        path=path,
        filename=PurePosixPath(path).name,
        revision=hashlib.sha256(content).hexdigest(),
        size=len(content),
    )


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


@router.post("/uploads")
async def upload_workspace_files(
    session_id: Annotated[str, Form(...)],
    files: Annotated[list[UploadFile], File(...)],
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        safe_session_id = validate_upload_session_id(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if not files:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="At least one file is required")
    if len(files) > MAX_UPLOAD_FILES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="No more than 5 files may be uploaded at once",
        )

    prepared: list[dict] = []
    uploaded_paths: list[str] = []
    sandbox = None
    workspace = None
    total_size = 0
    try:
        for upload in files:
            item = await spool_upload_file(upload, total_size)
            total_size += item["size"]
            item["path"] = (
                f"attachments/{safe_session_id}/{uuid.uuid4().hex}-{item['name']}"
            )
            prepared.append(item)

        sandbox, workspace = await workspace_context(user, db)
        response_files = []
        for item in prepared:
            url = await upload_sandbox_file(
                sandbox=sandbox,
                directory=workspace.directory,
                relative_path=item["path"],
                source=item["spool"],
                size=item["size"],
                sha256=item["sha256"],
            )
            uploaded_paths.append(item["path"])
            response_files.append(
                {
                    "name": item["name"],
                    "path": item["path"],
                    "mime": item["mime"],
                    "size": item["size"],
                    "url": url,
                    "direct": item["direct"],
                }
            )
        invalidate_workspace_directory_cache(sandbox, workspace)
        return {"files": response_files}
    except BaseException:
        if sandbox is not None and workspace is not None and uploaded_paths:
            with suppress(BaseException):
                await asyncio.shield(delete_sandbox_files(sandbox, workspace.directory, uploaded_paths))
            invalidate_workspace_directory_cache(sandbox, workspace)
        raise
    finally:
        for item in prepared:
            item["spool"].close()
        for upload in files:
            with suppress(BaseException):
                await upload.close()


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


@router.get("/html", response_model=WorkspaceHtmlResponse)
async def read_workspace_html(
    path: str = Query(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    relative_path = safe_html_path(path)
    sandbox, workspace = await workspace_context(user, db)
    content = await read_remote_file_bytes(sandbox, workspace, relative_path)
    if len(content) > MAX_HTML_BYTES:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="HTML file exceeds 10 MiB")
    try:
        html = content.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail="HTML file is not valid UTF-8") from exc
    metadata = workspace_html_metadata(relative_path, content)
    return WorkspaceHtmlResponse(**metadata.model_dump(), html=html)


@router.put("/html", response_model=WorkspaceHtmlMetadata)
async def save_workspace_html(
    payload: WorkspaceHtmlSave,
    path: str = Query(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    relative_path = safe_html_path(path)
    content = payload.html.encode("utf-8")
    if not payload.html.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="HTML file cannot be empty")
    if len(content) > MAX_HTML_BYTES:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="HTML file exceeds 10 MiB")

    sandbox, workspace = await workspace_context(user, db)
    current = await read_remote_file_bytes(sandbox, workspace, relative_path)
    current_revision = hashlib.sha256(current).hexdigest()
    if current_revision != payload.revision:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="HTML file changed since it was opened")
    metadata = workspace_html_metadata(relative_path, content)
    if metadata.revision == current_revision:
        return metadata
    await replace_sandbox_file(
        sandbox=sandbox,
        directory=workspace.directory,
        relative_path=relative_path,
        source=content,
        size=metadata.size,
        sha256=metadata.revision,
        expected_sha256=current_revision,
    )
    invalidate_workspace_directory_cache(sandbox, workspace)
    return metadata


@router.get("/download")
async def download_workspace_file(
    path: str = Query(...),
    filename: str | None = Query(None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    relative_path = safe_relative_path(path)
    sandbox, workspace = await workspace_context(user, db)
    await validate_remote_file(sandbox, workspace, relative_path)
    async with httpx.AsyncClient(timeout=30.0) as client:
        payload = await remote_json(client, sandbox, workspace, "file/content", relative_path)
    content, payload_media_type = decode_file_content(payload)
    download_name = sanitize_upload_filename(filename) if filename else PurePosixPath(relative_path).name
    guessed_media_type = mimetypes.guess_type(download_name)[0]
    return Response(
        content=content,
        media_type=payload_media_type or guessed_media_type or "application/octet-stream",
        headers={"Content-Disposition": content_disposition(download_name)},
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
