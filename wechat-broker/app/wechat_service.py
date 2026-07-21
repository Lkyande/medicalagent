from __future__ import annotations

import asyncio
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import AuthorizationAttempt, ComponentState, OfficialAccountBinding
from app.ticket_store import TicketStore, TicketStoreError


WECHAT_API = "https://api.weixin.qq.com"
_COMPONENT_TOKEN_ERRORS = {40001, 40014, 42001}
_AUTHORIZATION_ERRORS = {61007, 61023, 61024}
_component_token_lock = asyncio.Lock()
_authorizer_token_lock = asyncio.Lock()


class WechatConfigurationError(RuntimeError):
    pass


class WechatAPIError(RuntimeError):
    def __init__(self, message: str, errcode: int | None = None) -> None:
        super().__init__(message)
        self.errcode = errcode


class WechatAuthorizationRequired(WechatAPIError):
    pass


class WechatAuthorizationError(WechatAPIError):
    def __init__(
        self,
        message: str,
        *,
        authorization_id: str | None = None,
        return_url: str | None = None,
    ) -> None:
        super().__init__(message)
        self.authorization_id = authorization_id
        self.return_url = return_url


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


class WechatService:
    def __init__(self) -> None:
        required = {
            "WECHAT_BROKER_COMPONENT_APPID": settings.component_appid,
            "WECHAT_BROKER_COMPONENT_APPSECRET": settings.component_appsecret,
            "WECHAT_BROKER_AUTHORIZATION_CALLBACK_URL": settings.authorization_callback_url,
            "WECHAT_BROKER_TOKEN_ENCRYPTION_KEY": settings.token_encryption_key,
            "WECHAT_BROKER_TICKET_DATABASE_URL": settings.ticket_database_url,
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise WechatConfigurationError(f"Missing configuration: {', '.join(missing)}")
        try:
            self.cipher = Fernet(settings.token_encryption_key.encode("ascii"))
        except (ValueError, UnicodeEncodeError) as exc:
            raise WechatConfigurationError(
                "WECHAT_BROKER_TOKEN_ENCRYPTION_KEY must be a valid Fernet key"
            ) from exc

    def encrypt_token(self, value: str) -> str:
        return self.cipher.encrypt(value.encode("utf-8")).decode("ascii")

    def decrypt_token(self, value: str) -> str:
        try:
            return self.cipher.decrypt(value.encode("ascii")).decode("utf-8")
        except (InvalidToken, UnicodeError) as exc:
            raise WechatConfigurationError("Stored WeChat token could not be decrypted") from exc

    async def create_authorization(
        self,
        subject_id: str,
        tenant_id: str | None,
        return_url: str | None,
        db: AsyncSession,
    ) -> tuple[AuthorizationAttempt, str]:
        self._validate_return_url(return_url)
        component_token = await self.component_access_token(db)
        preauth = await self._post_wechat(
            "/cgi-bin/component/api_create_preauthcode",
            {"component_appid": settings.component_appid},
            access_token=component_token,
        )
        pre_auth_code = self._required_string(preauth, "pre_auth_code")
        state = secrets.token_hex(32)
        now = utcnow()
        attempt = AuthorizationAttempt(
            id=secrets.token_hex(16),
            state=state,
            subject_id=subject_id,
            tenant_id=tenant_id,
            return_url=return_url,
            status="pending",
            expires_at=now + timedelta(minutes=10),
            created_at=now,
            updated_at=now,
        )
        db.add(attempt)
        await db.execute(
            delete(AuthorizationAttempt).where(
                AuthorizationAttempt.expires_at < now - timedelta(days=7)
            )
        )
        await db.commit()

        callback_url = settings.authorization_callback_url
        callback_url += f"{'&' if '?' in callback_url else '?'}{urlencode({'state': state})}"
        query = urlencode(
            {
                "component_appid": settings.component_appid,
                "pre_auth_code": pre_auth_code,
                "redirect_uri": callback_url,
                "auth_type": "1",
            }
        )
        return attempt, f"https://mp.weixin.qq.com/cgi-bin/componentloginpage?{query}"

    async def complete_authorization(
        self,
        state: str,
        auth_code: str,
        db: AsyncSession,
    ) -> tuple[AuthorizationAttempt, OfficialAccountBinding]:
        now = utcnow()
        result = await db.execute(
            select(AuthorizationAttempt).where(
                AuthorizationAttempt.state == state,
                AuthorizationAttempt.status == "pending",
                AuthorizationAttempt.expires_at >= now,
            )
        )
        attempt = result.scalar_one_or_none()
        if attempt is None:
            raise WechatAuthorizationError("Authorization state is invalid, expired, or already used")
        claimed = await db.execute(
            update(AuthorizationAttempt)
            .where(
                AuthorizationAttempt.id == attempt.id,
                AuthorizationAttempt.status == "pending",
            )
            .values(status="processing", used_at=now, updated_at=now)
        )
        if claimed.rowcount != 1:
            await db.rollback()
            raise WechatAuthorizationError("Authorization state was already used")
        await db.commit()
        attempt_id = attempt.id
        attempt_return_url = attempt.return_url

        try:
            info = await self._query_authorization(auth_code, db)
            self._validate_function_ids(info)
            authorizer_appid = self._required_string(info, "authorizer_appid")
            refresh_token = self._required_string(info, "authorizer_refresh_token")

            owner_result = await db.execute(
                select(OfficialAccountBinding).where(
                    OfficialAccountBinding.authorizer_appid == authorizer_appid,
                    OfficialAccountBinding.subject_id != attempt.subject_id,
                )
            )
            if owner_result.scalar_one_or_none() is not None:
                raise WechatAuthorizationError(
                    "This official account is already bound to another subject",
                    authorization_id=attempt_id,
                    return_url=attempt_return_url,
                )

            binding_result = await db.execute(
                select(OfficialAccountBinding)
                .where(OfficialAccountBinding.subject_id == attempt.subject_id)
                .with_for_update()
            )
            binding = binding_result.scalar_one_or_none()
            if binding is None:
                binding = OfficialAccountBinding(
                    subject_id=attempt.subject_id,
                    tenant_id=attempt.tenant_id,
                    authorizer_appid=authorizer_appid,
                    status="active",
                    refresh_token_encrypted=self.encrypt_token(refresh_token),
                    authorized_at=now,
                    created_at=now,
                    updated_at=now,
                )
                db.add(binding)
            else:
                binding.tenant_id = attempt.tenant_id
                binding.authorizer_appid = authorizer_appid
                binding.status = "active"
                binding.refresh_token_encrypted = self.encrypt_token(refresh_token)
                binding.authorized_at = now
                binding.updated_at = now

            access_token = info.get("authorizer_access_token")
            expires_in = int(info.get("expires_in") or 0)
            if isinstance(access_token, str) and access_token and expires_in > 0:
                binding.access_token_encrypted = self.encrypt_token(access_token)
                binding.access_token_expires_at = now + timedelta(
                    seconds=max(60, expires_in - 120)
                )
            else:
                binding.access_token_encrypted = None
                binding.access_token_expires_at = None

            attempt.status = "authorized"
            attempt.authorizer_appid = authorizer_appid
            attempt.error = None
            attempt.updated_at = now
            await db.commit()
            return attempt, binding
        except Exception as exc:
            await db.rollback()
            await db.execute(
                update(AuthorizationAttempt)
                .where(AuthorizationAttempt.id == attempt_id)
                .values(status="failed", error=str(exc)[:1000], updated_at=utcnow())
            )
            await db.commit()
            if isinstance(exc, WechatAuthorizationError):
                raise
            raise WechatAuthorizationError(
                str(exc),
                authorization_id=attempt_id,
                return_url=attempt_return_url,
            ) from exc

    async def component_access_token(
        self, db: AsyncSession, *, force_refresh: bool = False
    ) -> str:
        async with _component_token_lock:
            result = await db.execute(
                select(ComponentState)
                .where(ComponentState.component_appid == settings.component_appid)
                .with_for_update()
            )
            state = result.scalar_one_or_none()
            now = utcnow()
            if (
                not force_refresh
                and state is not None
                and state.access_token_encrypted
                and (as_utc(state.access_token_expires_at) or now) > now + timedelta(seconds=60)
            ):
                try:
                    token = self.decrypt_token(state.access_token_encrypted)
                    await db.commit()
                    return token
                except WechatConfigurationError:
                    state.access_token_encrypted = None
                    state.access_token_expires_at = None

            try:
                ticket = await TicketStore().latest_ticket()
            except TicketStoreError as exc:
                raise WechatConfigurationError(str(exc)) from exc
            payload = await self._post_wechat(
                "/cgi-bin/component/api_component_token",
                {
                    "component_appid": settings.component_appid,
                    "component_appsecret": settings.component_appsecret,
                    "component_verify_ticket": ticket,
                },
            )
            token = self._required_string(payload, "component_access_token")
            expires_in = int(payload.get("expires_in") or 7200)
            if state is None:
                state = ComponentState(component_appid=settings.component_appid)
                db.add(state)
            state.access_token_encrypted = self.encrypt_token(token)
            state.access_token_expires_at = now + timedelta(seconds=max(60, expires_in - 120))
            state.updated_at = now
            await db.commit()
            return token

    async def binding(self, subject_id: str, db: AsyncSession) -> OfficialAccountBinding | None:
        result = await db.execute(
            select(OfficialAccountBinding).where(OfficialAccountBinding.subject_id == subject_id)
        )
        return result.scalar_one_or_none()

    async def authorizer_access_token(
        self, subject_id: str, db: AsyncSession
    ) -> tuple[str, datetime, OfficialAccountBinding]:
        async with _authorizer_token_lock:
            binding = await self.binding(subject_id, db)
            if binding is None or binding.status != "active":
                raise WechatAuthorizationRequired("Official account authorization is required")
            now = utcnow()
            if (
                binding.access_token_encrypted
                and (as_utc(binding.access_token_expires_at) or now) > now + timedelta(seconds=60)
            ):
                return (
                    self.decrypt_token(binding.access_token_encrypted),
                    as_utc(binding.access_token_expires_at) or now,
                    binding,
                )

            component_token = await self.component_access_token(db)
            result = await db.execute(
                select(OfficialAccountBinding)
                .where(OfficialAccountBinding.subject_id == subject_id)
                .with_for_update()
            )
            binding = result.scalar_one_or_none()
            if binding is None or binding.status != "active":
                raise WechatAuthorizationRequired("Official account authorization is required")

            async def request_token(token: str) -> dict[str, Any]:
                return await self._post_wechat(
                    "/cgi-bin/component/api_authorizer_token",
                    {
                        "component_appid": settings.component_appid,
                        "authorizer_appid": binding.authorizer_appid,
                        "authorizer_refresh_token": self.decrypt_token(
                            binding.refresh_token_encrypted
                        ),
                    },
                    access_token=token,
                )

            try:
                payload = await request_token(component_token)
            except WechatAPIError as exc:
                if exc.errcode in _COMPONENT_TOKEN_ERRORS:
                    component_token = await self.component_access_token(db, force_refresh=True)
                    try:
                        payload = await request_token(component_token)
                    except WechatAPIError as retry_exc:
                        await self._handle_authorization_error(binding, retry_exc, db)
                        raise
                else:
                    await self._handle_authorization_error(binding, exc, db)
                    raise

            token = self._required_string(payload, "authorizer_access_token")
            expires_in = int(payload.get("expires_in") or 7200)
            expires_at = utcnow() + timedelta(seconds=max(60, expires_in - 120))
            next_refresh = payload.get("authorizer_refresh_token")
            if isinstance(next_refresh, str) and next_refresh:
                binding.refresh_token_encrypted = self.encrypt_token(next_refresh)
            binding.access_token_encrypted = self.encrypt_token(token)
            binding.access_token_expires_at = expires_at
            binding.updated_at = utcnow()
            await db.commit()
            return token, expires_at, binding

    async def invalidate_authorizer_access_token(
        self, subject_id: str, rejected_token: str, db: AsyncSession
    ) -> None:
        result = await db.execute(
            select(OfficialAccountBinding)
            .where(OfficialAccountBinding.subject_id == subject_id)
            .with_for_update()
        )
        binding = result.scalar_one_or_none()
        if binding is None or not binding.access_token_encrypted:
            await db.commit()
            return
        try:
            current_token = self.decrypt_token(binding.access_token_encrypted)
        except WechatConfigurationError:
            current_token = rejected_token
        if current_token == rejected_token:
            binding.access_token_encrypted = None
            binding.access_token_expires_at = None
            binding.updated_at = utcnow()
        await db.commit()

    async def revoke_binding(self, subject_id: str, db: AsyncSession) -> bool:
        result = await db.execute(
            select(OfficialAccountBinding)
            .where(OfficialAccountBinding.subject_id == subject_id)
            .with_for_update()
        )
        binding = result.scalar_one_or_none()
        if binding is None:
            await db.commit()
            return False
        binding.status = "revoked"
        binding.access_token_encrypted = None
        binding.access_token_expires_at = None
        binding.updated_at = utcnow()
        await db.commit()
        return True

    async def _query_authorization(self, auth_code: str, db: AsyncSession) -> dict[str, Any]:
        component_token = await self.component_access_token(db)

        async def request(token: str) -> dict[str, Any]:
            return await self._post_wechat(
                "/cgi-bin/component/api_query_auth",
                {
                    "component_appid": settings.component_appid,
                    "authorization_code": auth_code,
                },
                access_token=token,
            )

        try:
            payload = await request(component_token)
        except WechatAPIError as exc:
            if exc.errcode not in _COMPONENT_TOKEN_ERRORS:
                raise
            payload = await request(await self.component_access_token(db, force_refresh=True))
        info = payload.get("authorization_info")
        if not isinstance(info, dict):
            raise WechatAPIError("WeChat authorization response omitted authorization_info")
        return info

    async def _handle_authorization_error(
        self,
        binding: OfficialAccountBinding,
        exc: WechatAPIError,
        db: AsyncSession,
    ) -> None:
        if exc.errcode not in _AUTHORIZATION_ERRORS:
            return
        binding.status = "revoked"
        binding.access_token_encrypted = None
        binding.access_token_expires_at = None
        binding.updated_at = utcnow()
        await db.commit()
        raise WechatAuthorizationRequired("Official account authorization has expired") from exc

    def _validate_function_ids(self, info: dict[str, Any]) -> None:
        required_ids = {
            value.strip() for value in settings.required_function_ids.split(",") if value.strip()
        }
        if not required_ids:
            return
        granted_ids = {
            str(item.get("funcscope_category", {}).get("id"))
            for item in info.get("func_info", [])
            if isinstance(item, dict) and isinstance(item.get("funcscope_category"), dict)
        }
        missing_ids = sorted(required_ids - granted_ids)
        if missing_ids:
            raise WechatAuthorizationError(
                f"Official account did not grant required function IDs: {', '.join(missing_ids)}"
            )

    @staticmethod
    def _validate_return_url(return_url: str | None) -> None:
        if return_url is None:
            return
        parsed = urlsplit(return_url)
        if not parsed.scheme or not parsed.netloc or parsed.username or parsed.password:
            raise WechatAuthorizationError("Return URL is invalid")
        origin = f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"
        allowed = {
            value.strip().rstrip("/").lower()
            for value in settings.allowed_return_origins.split(",")
            if value.strip()
        }
        if origin not in allowed:
            raise WechatAuthorizationError("Return URL origin is not allowed")

    async def _post_wechat(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        access_token: str | None = None,
    ) -> dict[str, Any]:
        params = {"access_token": access_token} if access_token else None
        try:
            async with httpx.AsyncClient(
                base_url=WECHAT_API,
                timeout=httpx.Timeout(settings.wechat_api_timeout_seconds),
            ) as client:
                response = await client.post(path, params=params, json=payload)
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPStatusError as exc:
            raise WechatAPIError(f"WeChat API returned HTTP {exc.response.status_code}") from exc
        except httpx.RequestError as exc:
            raise WechatAPIError(f"WeChat API is unavailable: {exc}") from exc
        except ValueError as exc:
            raise WechatAPIError("WeChat API returned invalid JSON") from exc
        if not isinstance(data, dict):
            raise WechatAPIError("WeChat API returned an invalid response")
        try:
            errcode = int(data.get("errcode") or 0)
        except (TypeError, ValueError) as exc:
            raise WechatAPIError("WeChat API returned an invalid errcode") from exc
        if errcode:
            errmsg = data.get("errmsg")
            raise WechatAPIError(
                errmsg if isinstance(errmsg, str) and errmsg else f"WeChat API error {errcode}",
                errcode,
            )
        return data

    @staticmethod
    def _required_string(payload: dict[str, Any], key: str) -> str:
        value = payload.get(key)
        if not isinstance(value, str) or not value:
            raise WechatAPIError(f"WeChat API response omitted {key}")
        return value
