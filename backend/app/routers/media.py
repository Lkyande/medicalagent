import json
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.background import BackgroundTask

from app.auth.dependencies import get_current_user
from app.config import settings
from app.database import get_db
from app.models import MediaGenerationJob, User, UserSession, UserWorkspace
from app.services.agnes_video_client import AgnesVideoClient, valid_https_url, validate_video_id


router = APIRouter(prefix="/media", tags=["media"])


class VideoProgressResponse(BaseModel):
    videoId: str
    taskId: str | None = None
    status: str
    progress: int
    seconds: str | None = None
    size: str | None = None
    url: str | None = None
    error: str | None = None


def video_progress(payload: dict[str, Any], video_id: str) -> VideoProgressResponse:
    status_value = str(payload.get("status") or "in_progress").strip().lower()
    status_name = status_value if status_value in {"completed", "failed"} else "running"
    progress_value = payload.get("progress")
    progress = (
        max(0, min(100, int(progress_value)))
        if isinstance(progress_value, (int, float)) and not isinstance(progress_value, bool)
        else 100 if status_name == "completed" else 0
    )
    metadata = payload.get("metadata")
    result_url = metadata.get("url") if isinstance(metadata, dict) else None
    if not result_url:
        result_url = payload.get("url")
    error_value = payload.get("error")
    if isinstance(error_value, dict):
        error_value = error_value.get("message") or error_value.get("detail") or json.dumps(error_value)
    return VideoProgressResponse(
        videoId=video_id,
        taskId=_optional_text(payload.get("task_id") or payload.get("id")),
        status=status_name,
        progress=progress,
        seconds=_optional_text(payload.get("seconds")),
        size=_optional_text(payload.get("size")),
        url=(
            result_url.strip()
            if status_name == "completed" and valid_https_url(result_url, settings.agnes_video_output_hosts)
            else None
        ),
        error=str(error_value)[:500] if status_name == "failed" and error_value else None,
    )


def _optional_text(value: object) -> str | None:
    return str(value).strip() if value is not None and str(value).strip() else None


async def _owned_video_context(
    video_id: str,
    session_id: str,
    user: User,
    db: AsyncSession,
) -> tuple[UserSession, UserWorkspace]:
    try:
        video_id = validate_video_id(video_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid video_id") from exc
    owned = await db.execute(
        select(UserSession, UserWorkspace)
        .join(UserWorkspace, UserWorkspace.id == UserSession.workspace_id)
        .where(UserSession.session_id == session_id, UserSession.user_id == user.id)
    )
    row = owned.one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    session, workspace = row
    media_job = await db.scalar(
        select(MediaGenerationJob.id).where(
            MediaGenerationJob.video_id == video_id,
            MediaGenerationJob.user_id == user.id,
            MediaGenerationJob.workspace_id == workspace.id,
        )
    )
    if media_job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Video job not found")
    return session, workspace


@router.get("/videos/{video_id}/progress", response_model=VideoProgressResponse)
async def get_video_progress(
    video_id: str,
    session_id: str = Query(min_length=1, max_length=128),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _owned_video_context(video_id, session_id, user, db)
    try:
        payload = await AgnesVideoClient().get_video(video_id)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Unable to query video progress") from exc
    return video_progress(payload, video_id)


@router.get("/videos/{video_id}/download")
async def download_video(
    video_id: str,
    session_id: str = Query(min_length=1, max_length=128),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _owned_video_context(video_id, session_id, user, db)
    try:
        payload = await AgnesVideoClient().get_video(video_id)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Unable to load generated video") from exc
    progress = video_progress(payload, video_id)
    if progress.status != "completed" or not progress.url:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Video is not completed")

    client = httpx.AsyncClient(
        timeout=httpx.Timeout(
            connect=settings.agnes_video_connect_timeout_seconds,
            read=settings.agnes_video_read_timeout_seconds,
            write=settings.agnes_video_read_timeout_seconds,
            pool=settings.agnes_video_connect_timeout_seconds,
        ),
        follow_redirects=False,
    )
    try:
        response = await client.send(client.build_request("GET", progress.url), stream=True)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        await client.aclose()
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Unable to download generated video") from exc

    async def close_stream() -> None:
        await response.aclose()
        await client.aclose()

    return StreamingResponse(
        response.aiter_raw(),
        media_type=response.headers.get("content-type", "video/mp4"),
        headers={
            "Content-Disposition": f'attachment; filename="{video_id}.mp4"',
        },
        background=BackgroundTask(close_stream),
    )
