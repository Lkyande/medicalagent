from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import httpx

from app.config import settings


class WechatConfigurationError(RuntimeError):
    pass


class WechatAPIError(RuntimeError):
    pass


class WechatAuthorizationRequired(WechatAPIError):
    pass


@dataclass(frozen=True)
class WechatBinding:
    authorizer_appid: str


@dataclass(frozen=True)
class WechatAuthorizationAttempt:
    authorization_id: str
    status: str
    subject_id: str
    authorizer_appid: str | None
    auth_url: str | None
    expires_at: datetime
    error: str | None


class WechatBrokerClient:
    def __init__(self) -> None:
        if not settings.wechat_broker_base_url:
            raise WechatConfigurationError("WECHAT_BROKER_BASE_URL is not configured")
        if not settings.wechat_broker_api_key:
            raise WechatConfigurationError("WECHAT_BROKER_API_KEY is not configured")
        self.base_url = settings.wechat_broker_base_url.rstrip("/") + "/"
        if not self.base_url.startswith("https://") and not settings.wechat_broker_allow_insecure_http:
            raise WechatConfigurationError(
                "WeChat broker must use HTTPS unless insecure HTTP is explicitly enabled"
            )

    async def authorization_status(self, subject_id: str) -> WechatBinding | None:
        payload = await self._request(
            "GET", f"internal/v1/bindings/{quote(subject_id, safe='')}"
        )
        if payload.get("authorized") is not True:
            return None
        return WechatBinding(
            authorizer_appid=self._required_string(payload, "authorizerAppid")
        )

    async def create_authorization(self, subject_id: str) -> WechatAuthorizationAttempt:
        payload = await self._request(
            "POST",
            "internal/v1/authorizations",
            json={"subjectId": subject_id},
        )
        return self._authorization_attempt(payload, require_auth_url=True)

    async def authorization_attempt(
        self, authorization_id: str
    ) -> WechatAuthorizationAttempt:
        payload = await self._request(
            "GET",
            f"internal/v1/authorizations/{quote(authorization_id, safe='')}",
        )
        return self._authorization_attempt(payload)

    async def authorizer_access_token(
        self, subject_id: str
    ) -> tuple[str, WechatBinding]:
        payload = await self._request(
            "POST", f"internal/v1/bindings/{quote(subject_id, safe='')}/access-token"
        )
        return (
            self._required_string(payload, "accessToken"),
            WechatBinding(
                authorizer_appid=self._required_string(payload, "authorizerAppid")
            ),
        )

    async def invalidate_authorizer_access_token(
        self, subject_id: str, rejected_token: str
    ) -> None:
        await self._request(
            "POST",
            f"internal/v1/bindings/{quote(subject_id, safe='')}/access-token/invalidate",
            json={"accessToken": rejected_token},
            expect_json=False,
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        expect_json: bool = True,
        **kwargs,
    ) -> dict[str, Any]:
        headers = dict(kwargs.pop("headers", {}))
        headers["X-API-Key"] = settings.wechat_broker_api_key
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(settings.wechat_broker_timeout_seconds),
            ) as client:
                response = await client.request(method, path, headers=headers, **kwargs)
        except httpx.RequestError as exc:
            raise WechatAPIError(f"WeChat broker is unavailable: {exc}") from exc
        if response.status_code >= 400:
            detail = self._response_detail(response)
            if response.status_code == 409:
                raise WechatAuthorizationRequired(detail)
            if response.status_code == 503:
                raise WechatConfigurationError(detail)
            raise WechatAPIError(
                f"WeChat broker returned HTTP {response.status_code}: {detail}"
            )
        if not expect_json:
            return {}
        try:
            payload = response.json()
        except ValueError as exc:
            raise WechatAPIError("WeChat broker returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise WechatAPIError("WeChat broker returned an invalid response")
        return payload

    @staticmethod
    def _response_detail(response: httpx.Response) -> str:
        try:
            payload = response.json()
        except ValueError:
            return response.text[:500] or "Request failed"
        if isinstance(payload, dict) and isinstance(payload.get("detail"), str):
            return payload["detail"][:500]
        return "Request failed"

    @staticmethod
    def _required_string(payload: dict[str, Any], key: str) -> str:
        value = payload.get(key)
        if not isinstance(value, str) or not value:
            raise WechatAPIError(f"WeChat broker response omitted {key}")
        return value

    @staticmethod
    def _required_datetime(payload: dict[str, Any], key: str) -> datetime:
        value = WechatBrokerClient._required_string(payload, key)
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise WechatAPIError(f"WeChat broker returned an invalid {key}") from exc
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed

    @classmethod
    def _authorization_attempt(
        cls,
        payload: dict[str, Any],
        *,
        require_auth_url: bool = False,
    ) -> WechatAuthorizationAttempt:
        auth_url = payload.get("authUrl")
        if require_auth_url and (not isinstance(auth_url, str) or not auth_url):
            raise WechatAPIError("WeChat broker response omitted authUrl")
        if auth_url is not None and not isinstance(auth_url, str):
            raise WechatAPIError("WeChat broker returned an invalid authUrl")
        authorizer_appid = payload.get("authorizerAppid")
        if authorizer_appid is not None and not isinstance(authorizer_appid, str):
            raise WechatAPIError("WeChat broker returned an invalid authorizerAppid")
        error = payload.get("error")
        if error is not None and not isinstance(error, str):
            raise WechatAPIError("WeChat broker returned an invalid authorization error")
        return WechatAuthorizationAttempt(
            authorization_id=cls._required_string(payload, "authorizationId"),
            status=cls._required_string(payload, "status"),
            subject_id=cls._required_string(payload, "subjectId"),
            authorizer_appid=authorizer_appid,
            auth_url=auth_url,
            expires_at=cls._required_datetime(payload, "expiresAt"),
            error=error,
        )
