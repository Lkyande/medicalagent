from contextlib import asynccontextmanager
from datetime import datetime
from html import escape
import hmac
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import Depends, FastAPI, Header, HTTPException, Query, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings, validate_settings
from app.database import async_session, get_db, init_db
from app.models import AuthorizationAttempt
from app.wechat_service import (
    AUTHORIZATION_START_PATH,
    WechatAPIError,
    WechatAuthorizationError,
    WechatAuthorizationRequired,
    WechatConfigurationError,
    WechatService,
    as_utc,
    utcnow,
)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    validate_settings()
    await init_db()
    yield


app = FastAPI(
    title="WeChat Open Platform Broker",
    version="1.0.0",
    lifespan=lifespan,
)


class CreateAuthorizationRequest(BaseModel):
    subject_id: str = Field(alias="subjectId", min_length=1, max_length=128)
    tenant_id: str | None = Field(default=None, alias="tenantId", max_length=128)
    return_url: str | None = Field(default=None, alias="returnUrl", max_length=2048)


class AuthorizationResponse(BaseModel):
    authorization_id: str = Field(alias="authorizationId")
    status: str
    subject_id: str = Field(alias="subjectId")
    tenant_id: str | None = Field(alias="tenantId")
    authorizer_appid: str | None = Field(alias="authorizerAppid")
    auth_url: str | None = Field(default=None, alias="authUrl")
    expires_at: datetime = Field(alias="expiresAt")
    error: str | None = None


class BindingResponse(BaseModel):
    authorized: bool
    status: str
    subject_id: str = Field(alias="subjectId")
    tenant_id: str | None = Field(alias="tenantId")
    authorizer_appid: str | None = Field(alias="authorizerAppid")
    authorized_at: datetime | None = Field(alias="authorizedAt")


class AccessTokenResponse(BaseModel):
    access_token: str = Field(alias="accessToken")
    expires_at: datetime = Field(alias="expiresAt")
    authorizer_appid: str = Field(alias="authorizerAppid")


class InvalidateTokenRequest(BaseModel):
    access_token: str = Field(alias="accessToken", min_length=1, max_length=4096)


class ResolveAuthorizationStartRequest(BaseModel):
    payload: str = Field(min_length=1, max_length=8192)


async def require_internal_api_key(
    x_api_key: str = Header(default="", alias="X-API-Key"),
) -> None:
    expected = settings.internal_api_key
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Internal API authentication is not configured",
        )
    if not x_api_key or not hmac.compare_digest(x_api_key, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid internal API key",
        )


def service_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, WechatConfigurationError):
        return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    if isinstance(exc, WechatAuthorizationRequired):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    if isinstance(exc, WechatAuthorizationError):
        return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))


def attempt_response(
    attempt: AuthorizationAttempt,
    *,
    auth_url: str | None = None,
) -> AuthorizationResponse:
    current_status = attempt.status
    if current_status == "pending" and (as_utc(attempt.expires_at) or utcnow()) < utcnow():
        current_status = "expired"
    return AuthorizationResponse(
        authorizationId=attempt.id,
        status=current_status,
        subjectId=attempt.subject_id,
        tenantId=attempt.tenant_id,
        authorizerAppid=attempt.authorizer_appid,
        authUrl=auth_url,
        expiresAt=attempt.expires_at,
        error=attempt.error,
    )


def callback_result(
    success: bool,
    message: str,
    *,
    authorization_id: str | None = None,
    return_url: str | None = None,
):
    result_status = "authorized" if success else "failed"
    if return_url and authorization_id:
        parts = urlsplit(return_url)
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        query.update(
            {
                "wechatAuthorizationId": authorization_id,
                "wechatAuthorizationStatus": result_status,
            }
        )
        target = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
        return RedirectResponse(target, status_code=status.HTTP_303_SEE_OTHER)
    title = "Authorization succeeded" if success else "Authorization failed"
    body = escape(message)
    close_script = (
        "<script>setTimeout(function(){window.close();},800);</script>"
        if success
        else ""
    )
    return HTMLResponse(
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        f"<title>{title}</title></head><body><p>{body}</p>"
        f"{close_script}</body></html>"
    )


@app.get(AUTHORIZATION_START_PATH, response_class=HTMLResponse)
async def authorization_start():
    return HTMLResponse(
        """<!doctype html>
<html lang="zh-CN">
<head><meta charset="utf-8"><title>正在打开微信授权</title></head>
<body><p id="status">正在打开微信授权页面...</p>
<script>
(async () => {
  const status = document.getElementById("status");
  try {
    const payload = window.location.hash.slice(1);
    if (!payload) throw new Error("missing authorization target");
    const response = await window.fetch(window.location.pathname + "/resolve", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({payload}),
      credentials: "same-origin",
      cache: "no-store"
    });
    if (!response.ok) throw new Error("authorization target rejected");
    const resolved = await response.json();
    const target = new URL(resolved.target);
    if (target.origin !== "https://mp.weixin.qq.com"
        || target.username
        || target.password
        || target.pathname !== "/cgi-bin/componentloginpage"
        || !target.searchParams.get("component_appid")
        || !target.searchParams.get("pre_auth_code")
        || !target.searchParams.get("redirect_uri")) {
      throw new Error("invalid authorization target");
    }
    history.replaceState(null, "", window.location.pathname);
    window.location.replace(target.href);
  } catch (_error) {
    status.textContent = "授权地址无效或已损坏，请关闭窗口后重新发起授权。";
  }
})();
</script></body></html>""",
        headers={
            "Cache-Control": "no-store",
            "Referrer-Policy": "origin",
            "Content-Security-Policy": (
                "default-src 'none'; script-src 'unsafe-inline'; connect-src 'self'; "
                "base-uri 'none'; frame-ancestors 'none'"
            ),
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.post(f"{AUTHORIZATION_START_PATH}/resolve")
async def resolve_authorization_start(request: ResolveAuthorizationStartRequest):
    try:
        target = WechatService.resolve_authorization_target(request.payload)
    except WechatAuthorizationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Authorization start payload is invalid",
        ) from exc
    return JSONResponse({"target": target}, headers={"Cache-Control": "no-store"})


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/ready")
async def ready():
    async with async_session() as db:
        await db.execute(text("SELECT 1"))
    return {"status": "ready"}


@app.post(
    "/api/wechat-broker/internal/v1/authorizations",
    response_model=AuthorizationResponse,
    dependencies=[Depends(require_internal_api_key)],
)
async def create_authorization(
    request: CreateAuthorizationRequest,
    db: AsyncSession = Depends(get_db),
):
    try:
        attempt, auth_url = await WechatService().create_authorization(
            request.subject_id,
            request.tenant_id,
            request.return_url,
            db,
        )
    except (WechatConfigurationError, WechatAPIError) as exc:
        raise service_http_error(exc) from exc
    return attempt_response(attempt, auth_url=auth_url)


@app.get(
    "/api/wechat-broker/internal/v1/authorizations/{authorization_id}",
    response_model=AuthorizationResponse,
    dependencies=[Depends(require_internal_api_key)],
)
async def authorization_status(
    authorization_id: str,
    db: AsyncSession = Depends(get_db),
):
    attempt = await db.get(AuthorizationAttempt, authorization_id)
    if attempt is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Authorization not found")
    return attempt_response(attempt)


@app.get(
    "/api/wechat-broker/internal/v1/bindings/{subject_id}",
    response_model=BindingResponse,
    dependencies=[Depends(require_internal_api_key)],
)
async def binding_status(subject_id: str, db: AsyncSession = Depends(get_db)):
    try:
        binding = await WechatService().binding(subject_id, db)
    except (WechatConfigurationError, WechatAPIError) as exc:
        raise service_http_error(exc) from exc
    active = binding is not None and binding.status == "active"
    return BindingResponse(
        authorized=active,
        status=binding.status if binding else "not_authorized",
        subjectId=subject_id,
        tenantId=binding.tenant_id if binding else None,
        authorizerAppid=binding.authorizer_appid if active else None,
        authorizedAt=binding.authorized_at if active else None,
    )


@app.post(
    "/api/wechat-broker/internal/v1/bindings/{subject_id}/access-token",
    response_model=AccessTokenResponse,
    dependencies=[Depends(require_internal_api_key)],
)
async def authorizer_access_token(subject_id: str, db: AsyncSession = Depends(get_db)):
    try:
        token, expires_at, binding = await WechatService().authorizer_access_token(subject_id, db)
    except (WechatConfigurationError, WechatAPIError) as exc:
        raise service_http_error(exc) from exc
    return AccessTokenResponse(
        accessToken=token,
        expiresAt=expires_at,
        authorizerAppid=binding.authorizer_appid,
    )


@app.post(
    "/api/wechat-broker/internal/v1/bindings/{subject_id}/access-token/invalidate",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_internal_api_key)],
)
async def invalidate_authorizer_access_token(
    subject_id: str,
    request: InvalidateTokenRequest,
    db: AsyncSession = Depends(get_db),
):
    try:
        await WechatService().invalidate_authorizer_access_token(
            subject_id, request.access_token, db
        )
    except (WechatConfigurationError, WechatAPIError) as exc:
        raise service_http_error(exc) from exc


@app.delete(
    "/api/wechat-broker/internal/v1/bindings/{subject_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_internal_api_key)],
)
async def revoke_binding(subject_id: str, db: AsyncSession = Depends(get_db)):
    try:
        await WechatService().revoke_binding(subject_id, db)
    except (WechatConfigurationError, WechatAPIError) as exc:
        raise service_http_error(exc) from exc


@app.get("/api/wechat-broker/authorization/callback")
async def authorization_callback(
    state_value: str = Query(alias="state", min_length=64, max_length=64),
    auth_code: str = Query(min_length=1, max_length=512),
    db: AsyncSession = Depends(get_db),
):
    try:
        attempt, _binding = await WechatService().complete_authorization(
            state_value, auth_code, db
        )
    except WechatAuthorizationError as exc:
        return callback_result(
            False,
            str(exc),
            authorization_id=exc.authorization_id,
            return_url=exc.return_url,
        )
    except (WechatConfigurationError, WechatAPIError) as exc:
        return callback_result(False, str(exc))
    return callback_result(
        True,
        "Official account authorization succeeded; this window can be closed.",
        authorization_id=attempt.id,
        return_url=attempt.return_url,
    )
