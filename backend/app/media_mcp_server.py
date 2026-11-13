import asyncio
import hashlib
import json
import time
import uuid
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Any
from urllib.parse import urlparse

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from app.config import settings
from app.database import async_session
from app.media_auth import require_media_identity
from app.models import MediaGenerationJob
from app.services.agnes_video_client import (
    AgnesVideoClient,
    AgnesVideoError,
    valid_https_url,
    validate_video_id,
)
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError


_DURATION_FRAMES = {3: 81, 5: 121, 10: 241}
_VIDEO_SIZES = {
    "480p": {
        "16:9": (832, 448),
        "9:16": (448, 832),
        "1:1": (512, 512),
        "4:3": (640, 480),
        "3:4": (480, 640),
    },
    "720p": {
        "16:9": (1280, 720),
        "9:16": (720, 1280),
        "1:1": (768, 768),
        "4:3": (960, 720),
        "3:4": (720, 960),
    },
    "1080p": {
        "16:9": (1920, 1080),
        "9:16": (1080, 1920),
        "1:1": (1024, 1024),
        "4:3": (1440, 1080),
        "3:4": (1080, 1440),
    },
}
_binding_tasks: set[asyncio.Task] = set()


def _media_transport_security() -> TransportSecuritySettings:
    allowed_hosts = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
    configured_host = urlparse(settings.media_mcp_url).netloc
    if configured_host and configured_host not in allowed_hosts:
        allowed_hosts.append(configured_host)
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=allowed_hosts,
        allowed_origins=["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"],
    )


media_mcp = FastMCP(
    "media-generation",
    stateless_http=True,
    json_response=True,
    streamable_http_path="/",
    transport_security=_media_transport_security(),
)


@media_mcp.tool()
async def video_create_from_text(
    prompt: str,
    duration_seconds: int = 5,
    aspect_ratio: str = "16:9",
    resolution: str = "720p",
    negative_prompt: str | None = None,
    seed: int | None = None,
) -> dict[str, Any]:
    """提交文生视频任务。返回 video_id 后必须复用该 ID 查询，禁止自动重复创建。"""
    payload = _video_payload(
        prompt,
        duration_seconds,
        aspect_ratio,
        resolution,
        negative_prompt,
        seed,
    )
    return await _submit_video(payload, "text")


@media_mcp.tool()
async def video_create_from_image(
    prompt: str,
    image_url: str,
    duration_seconds: int = 5,
    aspect_ratio: str = "16:9",
    resolution: str = "720p",
    negative_prompt: str | None = None,
    seed: int | None = None,
) -> dict[str, Any]:
    """提交单图生视频任务。image_url 必须是聊天附件提供的公开 HTTPS 地址。"""
    if not valid_https_url(image_url, settings.agnes_image_input_hosts):
        raise ValueError("image_url must be an HTTPS URL from the configured image host")
    payload = _video_payload(
        prompt,
        duration_seconds,
        aspect_ratio,
        resolution,
        negative_prompt,
        seed,
    )
    payload["image"] = image_url.strip()
    return await _submit_video(payload, "image")


@media_mcp.tool()
async def video_get(video_id: str) -> dict[str, Any]:
    """查询 Agnes 视频任务状态；completed 时 metadata.url 为成片地址。"""
    video_id = validate_video_id(video_id)
    await _assert_video_owned(video_id)
    return await AgnesVideoClient().get_video(video_id)


@media_mcp.tool()
async def video_wait(
    video_id: str,
    ctx: Context,
    wait_seconds: int | None = None,
) -> dict[str, Any]:
    """分段等待视频任务；超时返回同一 video_id，绝不自动创建新任务。"""
    video_id = validate_video_id(video_id)
    await _assert_video_owned(video_id)
    timeout_seconds = wait_seconds or settings.agnes_video_wait_seconds
    if timeout_seconds < 1 or timeout_seconds > settings.agnes_video_wait_seconds:
        raise ValueError(f"wait_seconds must be between 1 and {settings.agnes_video_wait_seconds}")
    return await _wait_for_video(
        AgnesVideoClient(),
        video_id,
        timeout_seconds,
        report_progress=ctx.report_progress,
    )


async def _wait_for_video(
    client: AgnesVideoClient,
    video_id: str,
    timeout_seconds: float,
    report_progress: Callable[[float, float | None, str | None], Awaitable[None]] | None = None,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return _video_waiting_payload(video_id)
        try:
            payload = await asyncio.wait_for(client.get_video(video_id), timeout=remaining)
        except asyncio.TimeoutError:
            return _video_waiting_payload(video_id)
        progress = _progress_value(payload)
        if report_progress:
            await report_progress(float(progress), 100.0, _progress_message(payload))
        if payload.get("status") in {"completed", "failed"}:
            return payload
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return _video_waiting_payload(video_id, payload)
        await asyncio.sleep(min(settings.agnes_video_poll_seconds, remaining))


def _video_payload(
    prompt: str,
    duration_seconds: int,
    aspect_ratio: str,
    resolution: str,
    negative_prompt: str | None,
    seed: int | None,
) -> dict[str, Any]:
    prompt = prompt.strip()
    if not prompt or len(prompt) > 4000:
        raise ValueError("prompt must contain 1 to 4000 characters")
    if duration_seconds not in _DURATION_FRAMES:
        raise ValueError("duration_seconds must be 3, 5, or 10")
    if resolution not in _VIDEO_SIZES or aspect_ratio not in _VIDEO_SIZES[resolution]:
        raise ValueError("unsupported resolution or aspect_ratio")
    if negative_prompt is not None:
        negative_prompt = negative_prompt.strip()
        if not negative_prompt or len(negative_prompt) > 2000:
            raise ValueError("negative_prompt must contain 1 to 2000 characters")
    if seed is not None and (seed < 0 or seed > 2**31 - 1):
        raise ValueError("seed must be between 0 and 2147483647")
    width, height = _VIDEO_SIZES[resolution][aspect_ratio]
    return {
        key: value
        for key, value in {
            "model": settings.agnes_video_model,
            "prompt": prompt,
            "width": width,
            "height": height,
            "num_frames": _DURATION_FRAMES[duration_seconds],
            "frame_rate": 24,
            "negative_prompt": negative_prompt,
            "seed": seed,
        }.items()
        if value is not None
    }


def _progress_value(payload: dict[str, Any]) -> int:
    value = payload.get("progress")
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return max(0, min(100, int(value)))
    return 100 if payload.get("status") == "completed" else 0


def _progress_message(payload: dict[str, Any]) -> str:
    status = str(payload.get("status") or "in_progress")
    return f"{status}: {_progress_value(payload)}%"


def _video_waiting_payload(
    video_id: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        **(payload or {}),
        "video_id": video_id,
        "status": "in_progress",
        "timed_out": True,
        "message": "视频仍在后台生成，请使用相同 video_id 继续查询；不得重新创建任务。",
    }


async def _submit_video(payload: dict[str, Any], generation_type: str) -> dict[str, Any]:
    client = AgnesVideoClient()
    # Limit deduplication to one submission window so explicit later regenerations remain possible.
    dedupe_window = int(time.time() // 600)
    fingerprint = hashlib.sha256(
        json.dumps(
            {
                "generation_type": generation_type,
                "payload": payload,
                "dedupe_window": dedupe_window,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    reservation_id, existing_video_id = await _reserve_video(generation_type, fingerprint)
    if existing_video_id:
        if existing_video_id.startswith("pending_"):
            raise RuntimeError("An identical video submission is already in progress; do not create another task")
        return await client.get_video(existing_video_id)
    try:
        result = await client.create_video(payload)
    except AgnesVideoError as exc:
        if not exc.submission_unknown:
            with suppress(Exception):
                await _discard_reservation(reservation_id)
        raise
    if not await _track_video_binding(reservation_id, result["video_id"]):
        return {
            **result,
            "tracking_error": True,
            "message": "视频已创建，平台正在后台保存任务归属；请保留此 video_id，禁止重新创建。",
        }
    return result


async def _reserve_video(generation_type: str, fingerprint: str) -> tuple[str, str | None]:
    identity = require_media_identity()
    async with async_session() as db:
        existing = await db.scalar(
            select(MediaGenerationJob).where(
                MediaGenerationJob.user_id == identity.user_id,
                MediaGenerationJob.workspace_id == identity.workspace_id,
                MediaGenerationJob.request_fingerprint == fingerprint,
            )
        )
        if existing is not None:
            return existing.id, existing.video_id
        reservation_id = uuid.uuid4().hex
        db.add(
            MediaGenerationJob(
                id=reservation_id,
                user_id=identity.user_id,
                workspace_id=identity.workspace_id,
                video_id=f"pending_{reservation_id}",
                request_fingerprint=fingerprint,
                generation_type=generation_type,
            )
        )
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            existing = await db.scalar(
                select(MediaGenerationJob).where(
                    MediaGenerationJob.user_id == identity.user_id,
                    MediaGenerationJob.workspace_id == identity.workspace_id,
                    MediaGenerationJob.request_fingerprint == fingerprint,
                )
            )
            if existing is None:
                raise
            return existing.id, existing.video_id
    return reservation_id, None


async def _track_video_binding(reservation_id: str, video_id: str) -> bool:
    task = asyncio.create_task(_bind_video_until_success(reservation_id, video_id))
    _binding_tasks.add(task)
    task.add_done_callback(_binding_tasks.discard)
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=10)
        return True
    except asyncio.TimeoutError:
        return False


async def _bind_video_until_success(reservation_id: str, video_id: str) -> None:
    delay = 0.5
    while True:
        try:
            await _bind_video(reservation_id, video_id)
            return
        except asyncio.CancelledError:
            raise
        except Exception:
            await asyncio.sleep(delay)
            delay = min(30.0, delay * 2)


async def _bind_video(reservation_id: str, video_id: str) -> None:
    identity = require_media_identity()
    async with async_session() as db:
        result = await db.execute(
            update(MediaGenerationJob)
            .where(
                MediaGenerationJob.id == reservation_id,
                MediaGenerationJob.user_id == identity.user_id,
                MediaGenerationJob.workspace_id == identity.workspace_id,
                MediaGenerationJob.video_id == f"pending_{reservation_id}",
            )
            .values(video_id=video_id)
        )
        if result.rowcount != 1:
            raise RuntimeError("Media job reservation was not found")
        await db.commit()


async def _discard_reservation(reservation_id: str) -> None:
    identity = require_media_identity()
    async with async_session() as db:
        await db.execute(
            delete(MediaGenerationJob).where(
                MediaGenerationJob.id == reservation_id,
                MediaGenerationJob.user_id == identity.user_id,
                MediaGenerationJob.workspace_id == identity.workspace_id,
                MediaGenerationJob.video_id == f"pending_{reservation_id}",
            )
        )
        await db.commit()


async def _assert_video_owned(video_id: str) -> None:
    identity = require_media_identity()
    async with async_session() as db:
        owned = await db.scalar(
            select(MediaGenerationJob.id).where(
                MediaGenerationJob.video_id == video_id,
                MediaGenerationJob.user_id == identity.user_id,
                MediaGenerationJob.workspace_id == identity.workspace_id,
            )
        )
    if owned is None:
        raise PermissionError("Video job does not belong to this workspace")
