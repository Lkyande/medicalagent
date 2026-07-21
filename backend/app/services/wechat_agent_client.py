from __future__ import annotations

from typing import Any

import httpx

from app.config import settings


class WeChatAgentUncertainError(RuntimeError):
    """The helper may have accepted a write, but no trustworthy response arrived."""


class WeChatAgentClient:
    def __init__(self) -> None:
        if not settings.wechat_agent_base_url:
            raise RuntimeError("WECHAT_AGENT_BASE_URL is not configured")
        self.base_url = settings.wechat_agent_base_url.rstrip("/")

    async def create_preview(
        self,
        *,
        topic: str,
        writing_requirements: str | None = None,
        image_style: str | None = None,
        template_name: str | None = None,
    ) -> dict[str, Any]:
        payload = {
            key: value
            for key, value in {
                "topic": topic,
                "writing_requirements": writing_requirements,
                "image_style": image_style,
                "template_name": template_name,
            }.items()
            if value is not None
        }
        return await self._request("POST", "/api/jobs", json=payload)

    async def get_preview(self, job_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/api/jobs/{job_id}")

    async def list_templates(self) -> dict[str, Any]:
        return await self._request("GET", "/api/templates")

    async def sync_official_account_draft(
        self,
        *,
        job_id: str,
        authorizer_access_token: str,
        html: str,
    ) -> dict[str, Any]:
        return await self._publish_request(
            "POST",
            "/api/publish-to-wechat",
            json={
                "job_id": job_id,
                "authorizer_access_token": authorizer_access_token,
                "html": html,
            },
        )

    async def submit_official_account_publish(
        self,
        *,
        media_id: str,
        authorizer_access_token: str,
    ) -> dict[str, Any]:
        return await self._publish_request(
            "POST",
            "/api/freepublish-submit",
            json={
                "media_id": media_id,
                "authorizer_access_token": authorizer_access_token,
            },
        )

    async def _publish_request(self, method: str, path: str, **kwargs) -> dict[str, Any]:
        if not self.base_url.startswith("https://") and not settings.wechat_agent_allow_insecure_http:
            raise RuntimeError(
                "Wechat Agent publish API must use HTTPS unless WECHAT_AGENT_ALLOW_INSECURE_HTTP is explicitly enabled"
            )
        headers = dict(kwargs.pop("headers", {}))
        if settings.wechat_agent_api_key:
            headers["X-API-Key"] = settings.wechat_agent_api_key
        return await self._request(method, path, headers=headers, **kwargs)

    async def _request(self, method: str, path: str, **kwargs) -> dict[str, Any]:
        timeout = httpx.Timeout(settings.wechat_agent_timeout_seconds)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.request(method, f"{self.base_url}{path}", **kwargs)
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:500]
            raise RuntimeError(f"Wechat Agent returned HTTP {exc.response.status_code}: {detail}") from exc
        except httpx.RequestError as exc:
            raise WeChatAgentUncertainError(f"Wechat Agent response was not received: {exc}") from exc
        except ValueError as exc:
            raise WeChatAgentUncertainError("Wechat Agent returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise WeChatAgentUncertainError("Wechat Agent returned an invalid response")
        return payload
