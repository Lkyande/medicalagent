from __future__ import annotations

from typing import Any

import httpx

from app.config import settings


class SmartMarketClient:
    def __init__(self) -> None:
        if not settings.smart_market_base_url:
            raise RuntimeError("SMART_MARKET_BASE_URL is not configured")
        self.base_url = settings.smart_market_base_url.rstrip("/")

    async def search_hot_topics(self, payload: dict[str, Any]) -> dict[str, Any]:
        result = await self._request(
            "POST",
            "/track/hot_topics/search",
            json=payload,
        )
        if not isinstance(result, dict) or not isinstance(result.get("items"), list):
            raise RuntimeError("Smart Market returned an invalid hot-topic response")
        return result

    async def list_tracks(self) -> dict[str, str]:
        result = await self._request("GET", "/track/tracks")
        if not isinstance(result, dict) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in result.items()
        ):
            raise RuntimeError("Smart Market returned an invalid track response")
        return result

    async def _request(self, method: str, path: str, **kwargs) -> Any:
        headers = {}
        if settings.smart_market_api_key:
            headers["X-API-Key"] = settings.smart_market_api_key
        timeout = httpx.Timeout(settings.smart_market_timeout_seconds)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.request(
                    method,
                    f"{self.base_url}{path}",
                    headers=headers,
                    **kwargs,
                )
                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:500]
            raise RuntimeError(
                f"Smart Market returned HTTP {exc.response.status_code}: {detail}"
            ) from exc
        except httpx.RequestError as exc:
            raise RuntimeError(f"Smart Market is unavailable: {exc}") from exc
        except ValueError as exc:
            raise RuntimeError("Smart Market returned invalid JSON") from exc
