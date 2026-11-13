from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

import httpx

from app.config import settings


VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,200}$")
VIDEO_STATUSES = {"queued", "in_progress", "completed", "failed"}


class AgnesVideoError(RuntimeError):
    def __init__(self, message: str, *, submission_unknown: bool = False) -> None:
        super().__init__(message)
        self.submission_unknown = submission_unknown


class AgnesVideoClient:
    def __init__(self) -> None:
        if not settings.agnes_image_generation_api_key.strip():
            raise RuntimeError("AGNES_IMAGE_GENERATION_API_KEY is not configured")
        self.base_url = settings.agnes_api_base_url.rstrip("/")
        self.primary_key = settings.agnes_image_generation_api_key.strip()
        self.fallback_key = settings.agnes_image_generation_fallback_api_key.strip()
        self.timeout = httpx.Timeout(
            connect=settings.agnes_video_connect_timeout_seconds,
            read=settings.agnes_video_read_timeout_seconds,
            write=settings.agnes_video_read_timeout_seconds,
            pool=settings.agnes_video_connect_timeout_seconds,
        )

    async def create_video(self, payload: dict[str, Any]) -> dict[str, Any]:
        result = await self._request_with_fallback(
            "POST",
            "/v1/videos",
            retry_timeouts=(httpx.ConnectTimeout, httpx.PoolTimeout),
            retry_statuses={401, 403, 429},
            json=payload,
        )
        return self._validate_video(result, require_video_id=True)

    async def get_video(self, video_id: str) -> dict[str, Any]:
        video_id = validate_video_id(video_id)
        result = await self._request_with_fallback(
            "GET",
            "/agnesapi",
            retry_timeouts=(httpx.TimeoutException,),
            retry_statuses={401, 403, 404, 429, 500, 503},
            params={"video_id": video_id, "model_name": settings.agnes_video_model},
        )
        return self._validate_video(result, require_video_id=False)

    async def _request_with_fallback(
        self,
        method: str,
        path: str,
        *,
        retry_timeouts: tuple[type[BaseException], ...],
        retry_statuses: set[int],
        **kwargs: Any,
    ) -> dict[str, Any]:
        keys = [self.primary_key]
        if self.fallback_key and self.fallback_key != self.primary_key:
            keys.append(self.fallback_key)

        for index, api_key in enumerate(keys):
            has_fallback = index + 1 < len(keys)
            try:
                return await self._request(method, path, api_key=api_key, **kwargs)
            except retry_timeouts as exc:
                if has_fallback:
                    continue
                raise AgnesVideoError(f"Agnes API request timed out: {exc.__class__.__name__}") from exc
            except httpx.TimeoutException as exc:
                # A POST body may already have been accepted. Retrying could create a duplicate video.
                raise AgnesVideoError(
                    "Agnes video submission result is unknown; do not create another task",
                    submission_unknown=method == "POST",
                ) from exc
            except httpx.HTTPStatusError as exc:
                if has_fallback and exc.response.status_code in retry_statuses:
                    continue
                detail = exc.response.text[:500]
                raise AgnesVideoError(
                    f"Agnes API returned HTTP {exc.response.status_code}: {detail}",
                    submission_unknown=method == "POST" and exc.response.status_code >= 500,
                ) from exc
            except httpx.RequestError as exc:
                if method == "GET" and has_fallback:
                    continue
                raise AgnesVideoError(
                    f"Agnes API response was not received: {exc}",
                    submission_unknown=method == "POST",
                ) from exc

        raise RuntimeError("Agnes API request failed")

    async def _request(
        self,
        method: str,
        path: str,
        *,
        api_key: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.request(
                method,
                f"{self.base_url}{path}",
                headers={"Authorization": f"Bearer {api_key}"},
                **kwargs,
            )
            response.raise_for_status()
            try:
                payload = response.json()
            except ValueError as exc:
                raise RuntimeError("Agnes API returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("Agnes API returned an invalid response")
        return payload

    @staticmethod
    def _validate_video(payload: dict[str, Any], *, require_video_id: bool) -> dict[str, Any]:
        video_id = payload.get("video_id")
        if require_video_id and not isinstance(video_id, str):
            raise RuntimeError("Agnes API response did not include video_id")
        if isinstance(video_id, str):
            validate_video_id(video_id)
        status = str(payload.get("status", "")).strip().lower()
        if status not in VIDEO_STATUSES:
            raise RuntimeError("Agnes API returned an invalid video status")
        metadata = payload.get("metadata")
        if status == "completed":
            url = metadata.get("url") if isinstance(metadata, dict) else None
            if not url:
                url = payload.get("url")
            if not valid_https_url(url, settings.agnes_video_output_hosts):
                raise RuntimeError("Completed Agnes video did not include a valid HTTPS URL")
        return payload


def validate_video_id(video_id: str) -> str:
    value = video_id.strip()
    if not VIDEO_ID_RE.fullmatch(value):
        raise ValueError("invalid video_id")
    return value


def valid_https_url(value: object, allowed_hosts: str | None = None) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlparse(value.strip())
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        return False
    if allowed_hosts is None:
        return True
    hostname = parsed.hostname.lower().rstrip(".")
    rules = [item.strip().lower().rstrip(".") for item in allowed_hosts.split(",") if item.strip()]
    return any(
        hostname.endswith(rule) if rule.startswith(".") else hostname == rule
        for rule in rules
    )
