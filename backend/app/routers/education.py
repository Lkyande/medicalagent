import asyncio
import hashlib
import json
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.database import get_db
from app.config import get_sandboxes
from app.education_mcp_server import JOB_ID_RE
from app.models import User, UserSession, UserWorkspace
from app.proxy.opencode_proxy import basic_auth_header
from app.services.wechat_agent_client import WeChatAgentClient
from app.services.image_upload_client import ImageUploadClient
from app.sandbox.manager import replace_sandbox_file, upload_sandbox_file
from app.routers.workspace import (
    decode_file_content,
    invalidate_workspace_directory_cache,
    remote_json,
    safe_html_path,
    spool_upload_file,
    validate_remote_file,
    workspace_context,
)


router = APIRouter(prefix="/education", tags=["education"])


class PreviewProgressResponse(BaseModel):
    jobId: str
    status: str
    stage: str
    label: str
    percent: int
    title: str | None = None
    summary: str | None = None
    editableHtmlUrl: str | None = None


class PreviewArtifactMetadata(BaseModel):
    jobId: str
    path: str
    filename: str
    revision: str
    size: int


class PreviewArtifactResponse(PreviewArtifactMetadata):
    html: str


class PreviewArtifactSave(BaseModel):
    html: str
    revision: str = Field(pattern=r"^[0-9a-f]{64}$")


class PreviewImageUploadResponse(BaseModel):
    url: str
    mime: str
    size: int




_STAGE_ESTIMATES = (
    (("completed", "complete", "done", "完成"), 100, "预览生成完成"),
    (("failed", "error", "失败"), 0, "预览生成失败"),
    (("saving", "upload", "persist", "finalize", "保存", "收尾"), 95, "正在保存预览"),
    (("layout", "format", "render", "排版", "渲染"), 88, "正在进行微信排版"),
    (("image", "illustration", "picture", "配图", "图片"), 72, "正在生成配图"),
    (("review", "polish", "audit", "审核", "润色", "校对"), 58, "正在审核和润色"),
    (("content", "article", "writing", "正文", "文章", "撰写"), 45, "正在生成正文"),
    (("outline", "plan", "提纲", "大纲"), 25, "正在生成内容提纲"),
    (("prepare", "material", "initialize", "准备", "素材", "初始化"), 12, "正在准备素材"),
    (("pending", "queued", "waiting", "排队", "等待"), 5, "等待任务启动"),
)
_MAX_HTML_BYTES = 10 * 1024 * 1024
_SUPPORTED_IMAGE_MIMES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
_artifact_locks: dict[str, asyncio.Lock] = {}


def preview_progress(payload: dict[str, Any], job_id: str) -> PreviewProgressResponse:
    status_value = str(payload.get("stage") or payload.get("status") or "running").strip().lower()
    details = [status_value]
    for key in ("message", "detail", "current_step"):
        value = payload.get(key)
        if isinstance(value, str):
            details.append(value)
    progress = payload.get("progress")
    if isinstance(progress, list):
        details.extend(str(item) for item in progress[-3:])
    elif isinstance(progress, str):
        details.append(progress)
    searchable = " ".join(details).lower()

    percent = 15
    label = "正在生成预览"
    for keywords, estimate, stage_label in _STAGE_ESTIMATES:
        if any(keyword in searchable for keyword in keywords):
            percent = estimate
            label = stage_label
            break

    status_name = "failed" if status_value == "failed" else "completed" if status_value == "completed" else "running"
    if status_name == "failed" and percent == 0:
        percent = 15
    result = payload.get("result")
    completed_result = result if status_name == "completed" and isinstance(result, dict) else {}
    title = completed_result.get("title")
    summary = completed_result.get("summary")
    editable_url = completed_result.get("editable_html_url")
    return PreviewProgressResponse(
        jobId=job_id,
        status=status_name,
        stage=status_value,
        label=label,
        percent=percent,
        title=title if isinstance(title, str) and title.strip() else None,
        summary=summary if isinstance(summary, str) and summary.strip() else None,
        editableHtmlUrl=(
            editable_url
            if isinstance(editable_url, str) and editable_url.startswith(("https://", "http://"))
            else None
        ),
    )


def _tool_output_job_id(output: object) -> str | None:
    payload = output
    if isinstance(output, str):
        try:
            payload = json.loads(output)
        except json.JSONDecodeError:
            return None
    if not isinstance(payload, dict):
        return None
    value = payload.get("job_id")
    if isinstance(value, str):
        return value
    data = payload.get("data")
    if isinstance(data, dict) and isinstance(data.get("job_id"), str):
        return data["job_id"]
    return None


async def _session_contains_preview_job(
    session: UserSession,
    workspace: UserWorkspace,
    job_id: str,
    require_create: bool = False,
) -> bool:
    sandbox = get_sandboxes().get(session.sandbox_id)
    if not sandbox:
        return False
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{sandbox['url']}/session/{session.session_id}/message",
                params={"directory": workspace.directory},
                headers=basic_auth_header(sandbox),
            )
            response.raise_for_status()
            messages = response.json()
    except (httpx.HTTPError, ValueError):
        return False
    if not isinstance(messages, list):
        return False
    has_create = False
    has_wait = False
    for message in messages:
        if not isinstance(message, dict):
            continue
        for part in message.get("parts", []):
            if not isinstance(part, dict) or part.get("type") != "tool":
                continue
            tool = str(part.get("tool", ""))
            state = part.get("state")
            if not isinstance(state, dict):
                continue
            if tool.endswith("education_create_preview") and _tool_output_job_id(state.get("output")) == job_id:
                has_create = True
            if tool.endswith("education_wait_for_preview"):
                tool_input = state.get("input")
                if isinstance(tool_input, dict) and tool_input.get("job_id") == job_id:
                    has_wait = True
    return has_wait and (has_create or not require_create)


async def _owned_preview_context(
    job_id: str,
    session_id: str,
    user: User,
    db: AsyncSession,
    require_create: bool = False,
) -> tuple[UserSession, UserWorkspace]:
    if not JOB_ID_RE.fullmatch(job_id):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid job_id")
    owned = await db.execute(
        select(UserSession, UserWorkspace)
        .join(UserWorkspace, UserWorkspace.id == UserSession.workspace_id)
        .where(
            UserSession.session_id == session_id,
            UserSession.user_id == user.id,
        )
    )
    row = owned.one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    session, workspace = row
    contains_job = (
        await _session_contains_preview_job(session, workspace, job_id, require_create=True)
        if require_create
        else await _session_contains_preview_job(session, workspace, job_id)
    )
    if not contains_job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Preview job not found")
    return session, workspace


def _preview_sandbox(session: UserSession) -> dict:
    sandbox = get_sandboxes().get(session.sandbox_id)
    if not sandbox:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Sandbox unavailable")
    return sandbox


def _artifact_path(job_id: str) -> str:
    return f"education-previews/{job_id}.html"


def education_preview_job_id(path: str) -> str:
    relative_path = safe_html_path(path)
    prefix = "education-previews/"
    if not relative_path.startswith(prefix) or not relative_path.endswith(".html"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="An education preview HTML path is required")
    job_id = relative_path[len(prefix):-len(".html")]
    if not JOB_ID_RE.fullmatch(job_id) or relative_path != _artifact_path(job_id):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="An education preview HTML path is required")
    return job_id


async def _owned_preview_file_context(
    path: str,
    user: User,
    db: AsyncSession,
) -> tuple[str, dict, UserWorkspace]:
    job_id = education_preview_job_id(path)
    sandbox, workspace = await workspace_context(user, db)
    await validate_remote_file(sandbox, workspace, _artifact_path(job_id))
    return job_id, sandbox, workspace


def _artifact_metadata(job_id: str, content: bytes) -> PreviewArtifactMetadata:
    path = _artifact_path(job_id)
    return PreviewArtifactMetadata(
        jobId=job_id,
        path=path,
        filename=f"{job_id}.html",
        revision=hashlib.sha256(content).hexdigest(),
        size=len(content),
    )


async def _read_artifact_bytes(sandbox: dict, workspace: UserWorkspace, job_id: str) -> bytes | None:
    path = _artifact_path(job_id)
    try:
        await validate_remote_file(sandbox, workspace, path)
    except HTTPException as exc:
        if exc.status_code == status.HTTP_404_NOT_FOUND:
            return None
        raise
    async with httpx.AsyncClient(timeout=30.0) as client:
        payload = await remote_json(client, sandbox, workspace, "file/content", path)
    content, _ = decode_file_content(payload)
    return content


@router.get("/preview-progress/{job_id}", response_model=PreviewProgressResponse)
async def get_preview_progress(
    job_id: str,
    session_id: str = Query(min_length=1, max_length=128),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _owned_preview_context(job_id, session_id, user, db)
    try:
        payload = await WeChatAgentClient().get_preview(job_id)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Unable to query preview progress",
        ) from exc
    return preview_progress(payload, job_id)


@router.post("/previews/{job_id}/materialize", response_model=PreviewArtifactMetadata)
async def materialize_preview_artifact(
    job_id: str,
    session_id: str = Query(min_length=1, max_length=128),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    session, workspace = await _owned_preview_context(job_id, session_id, user, db, require_create=True)
    sandbox = _preview_sandbox(session)
    lock_key = f"{workspace.id}\0{job_id}"
    lock = _artifact_locks.setdefault(lock_key, asyncio.Lock())
    async with lock:
        existing = await _read_artifact_bytes(sandbox, workspace, job_id)
        if existing is not None:
            return _artifact_metadata(job_id, existing)
        try:
            payload = await WeChatAgentClient().get_preview(job_id)
        except RuntimeError as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Unable to load preview HTML") from exc
        if preview_progress(payload, job_id).status != "completed":
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Preview is not completed")
        result = payload.get("result")
        html = result.get("html") if isinstance(result, dict) else None
        if not isinstance(html, str) or not html.strip():
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Completed preview did not include HTML")
        content = html.encode("utf-8")
        if len(content) > _MAX_HTML_BYTES:
            raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Preview HTML exceeds 10 MiB")
        metadata = _artifact_metadata(job_id, content)
        await upload_sandbox_file(
            sandbox=sandbox,
            directory=workspace.directory,
            relative_path=metadata.path,
            source=content,
            size=metadata.size,
            sha256=metadata.revision,
        )
        invalidate_workspace_directory_cache(sandbox, workspace)
        return metadata


@router.get("/previews/{job_id}/html", response_model=PreviewArtifactResponse)
async def get_preview_artifact(
    job_id: str,
    session_id: str = Query(min_length=1, max_length=128),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    session, workspace = await _owned_preview_context(job_id, session_id, user, db, require_create=True)
    sandbox = _preview_sandbox(session)
    content = await _read_artifact_bytes(sandbox, workspace, job_id)
    if content is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Preview HTML has not been saved")
    try:
        html = content.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Preview HTML is not valid UTF-8") from exc
    metadata = _artifact_metadata(job_id, content)
    return PreviewArtifactResponse(**metadata.model_dump(), html=html)


@router.put("/previews/{job_id}/html", response_model=PreviewArtifactMetadata)
async def save_preview_artifact(
    job_id: str,
    payload: PreviewArtifactSave,
    session_id: str = Query(min_length=1, max_length=128),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    session, workspace = await _owned_preview_context(job_id, session_id, user, db, require_create=True)
    lock = _artifact_locks.setdefault(f"{workspace.id}\0{job_id}", asyncio.Lock())
    async with lock:
        return await _save_preview_artifact(job_id, payload, session, workspace)


async def _save_preview_artifact(
    job_id: str,
    payload: PreviewArtifactSave,
    session: UserSession,
    workspace: UserWorkspace,
):
    sandbox = _preview_sandbox(session)
    content = payload.html.encode("utf-8")
    if not payload.html.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Preview HTML cannot be empty")
    if len(content) > _MAX_HTML_BYTES:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Preview HTML exceeds 10 MiB")
    current = await _read_artifact_bytes(sandbox, workspace, job_id)
    if current is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Preview HTML has not been saved")
    current_revision = hashlib.sha256(current).hexdigest()
    if current_revision != payload.revision:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Preview HTML changed since it was opened")
    metadata = _artifact_metadata(job_id, content)
    if metadata.revision == current_revision:
        return metadata
    await replace_sandbox_file(
        sandbox=sandbox,
        directory=workspace.directory,
        relative_path=metadata.path,
        source=content,
        size=metadata.size,
        sha256=metadata.revision,
        expected_sha256=current_revision,
    )
    invalidate_workspace_directory_cache(sandbox, workspace)
    return metadata


@router.post("/previews/{job_id}/images", response_model=PreviewImageUploadResponse)
async def upload_preview_image(
    job_id: str,
    file: Annotated[UploadFile, File(...)],
    session_id: str = Query(min_length=1, max_length=128),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _owned_preview_context(job_id, session_id, user, db, require_create=True)
    return await _upload_preview_image(file)


@router.post("/files/images", response_model=PreviewImageUploadResponse)
async def upload_preview_file_image(
    file: Annotated[UploadFile, File(...)],
    path: str = Query(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _owned_preview_file_context(path, user, db)
    return await _upload_preview_image(file)


async def _upload_preview_image(file: UploadFile) -> PreviewImageUploadResponse:
    prepared = None
    try:
        prepared = await spool_upload_file(file, 0)
        if prepared["size"] == 0:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Image file cannot be empty")
        if prepared["mime"] not in _SUPPORTED_IMAGE_MIMES:
            raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail="Unsupported image format")
        try:
            url = await ImageUploadClient().upload(
                prepared["name"],
                prepared["spool"],
                prepared["mime"],
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Unable to upload preview image") from exc
        return PreviewImageUploadResponse(url=url, mime=prepared["mime"], size=prepared["size"])
    finally:
        if prepared is not None:
            prepared["spool"].close()
        await file.close()
