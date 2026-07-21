import asyncio
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlparse

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from app.config import settings
from app.services.wechat_agent_client import WeChatAgentClient


JOB_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")


def _education_transport_security() -> TransportSecuritySettings:
    allowed_hosts = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
    configured_host = urlparse(settings.education_mcp_url).netloc
    if configured_host and configured_host not in allowed_hosts:
        allowed_hosts.append(configured_host)
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=allowed_hosts,
        allowed_origins=["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"],
    )


education_mcp = FastMCP(
    "education-content",
    stateless_http=True,
    json_response=True,
    streamable_http_path="/",
    transport_security=_education_transport_security(),
)


@education_mcp.tool()
async def education_create_preview(
    topic: str,
    writing_requirements: str | None = None,
    image_style: str | None = None,
    template_name: str | None = None,
) -> dict[str, Any]:
    """提交医学科普微信公众号预览任务，返回 job_id；不会创建草稿或正式发布。"""
    topic, writing_requirements, image_style, template_name = _validate_preview_input(
        topic, writing_requirements, image_style, template_name
    )
    submitted = await WeChatAgentClient().create_preview(
        topic=topic,
        writing_requirements=writing_requirements,
        image_style=image_style,
        template_name=template_name,
    )
    job_id = str(submitted.get("job_id", "")).strip()
    if not JOB_ID_RE.fullmatch(job_id):
        raise RuntimeError("Wechat Agent returned an invalid job_id")
    return {**submitted, "job_id": job_id}


@education_mcp.tool()
async def education_get_preview(job_id: str) -> dict[str, Any]:
    """查询科普预览任务进度；完成后返回标题、摘要、预览地址和图片信息。"""
    job_id = job_id.strip()
    if not JOB_ID_RE.fullmatch(job_id):
        raise ValueError("invalid job_id")
    return _compact_preview_payload(await WeChatAgentClient().get_preview(job_id))


@education_mcp.tool()
async def education_wait_for_preview(
    job_id: str,
    ctx: Context,
    wait_seconds: int | None = None,
) -> dict[str, Any]:
    """分段等待科普预览；未完成时返回同一 job_id，期间不会重复创建任务。"""
    job_id = job_id.strip()
    if not JOB_ID_RE.fullmatch(job_id):
        raise ValueError("invalid job_id")
    timeout_seconds = wait_seconds or settings.wechat_agent_preview_wait_seconds
    if timeout_seconds < 1 or timeout_seconds > settings.wechat_agent_preview_wait_seconds:
        raise ValueError(
            f"wait_seconds must be between 1 and {settings.wechat_agent_preview_wait_seconds}"
        )

    return await _wait_for_preview(
        WeChatAgentClient(),
        job_id,
        timeout_seconds,
        report_progress=ctx.report_progress,
    )


async def _wait_for_preview(
    client: WeChatAgentClient,
    job_id: str,
    timeout_seconds: int,
    report_progress: Callable[[float, float | None, str | None], Awaitable[None]] | None = None,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    poll_count = 0
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return _preview_waiting_payload(job_id)
        try:
            payload = await asyncio.wait_for(client.get_preview(job_id), timeout=remaining)
        except asyncio.TimeoutError:
            return _preview_waiting_payload(job_id)
        poll_count += 1
        if report_progress:
            await report_progress(
                float(poll_count),
                None,
                _preview_progress_message(payload),
            )
        if payload.get("stage") in {"completed", "failed"}:
            return _compact_preview_payload(payload)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return _preview_waiting_payload(job_id, payload)
        await asyncio.sleep(min(settings.wechat_agent_preview_poll_seconds, remaining))


def _preview_waiting_payload(job_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        **_compact_preview_payload(payload or {"job_id": job_id, "stage": "running"}),
        "job_id": job_id,
        "stage": "running",
        "timed_out": True,
        "message": "任务仍在后台生成，请使用相同 job_id 继续查询；不得重新创建任务。",
    }


def _preview_progress_message(payload: dict[str, Any]) -> str:
    progress = payload.get("progress")
    if isinstance(progress, list) and progress:
        return str(progress[-1])[:200]
    for key in ("message", "current_step", "stage"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:200]
    return "running"


def _validate_preview_input(
    topic: str,
    writing_requirements: str | None,
    image_style: str | None,
    template_name: str | None,
) -> tuple[str, str | None, str | None, str | None]:
    topic = topic.strip()
    if not topic or len(topic) > 20000:
        raise ValueError("topic must contain 1 to 20000 characters")
    values = [writing_requirements, image_style, template_name]
    for name, value, limit in (
        ("writing_requirements", writing_requirements, 4000),
        ("image_style", image_style, 1000),
        ("template_name", template_name, 200),
    ):
        if value is not None and (not value.strip() or len(value) > limit):
            raise ValueError(f"{name} must contain 1 to {limit} characters")
    return topic, *(value.strip() if value else None for value in values)


def _compact_preview_payload(payload: dict[str, Any]) -> dict[str, Any]:
    result = payload.get("result")
    if isinstance(result, dict):
        result = {key: value for key, value in result.items() if key != "html"}
        payload = {**payload, "result": result}
    return payload


@education_mcp.tool()
async def education_list_templates() -> dict[str, Any]:
    """列出可用微信公众号排版模板及其适用场景。"""
    return await WeChatAgentClient().list_templates()
