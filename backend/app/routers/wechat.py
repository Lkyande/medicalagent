from __future__ import annotations

import asyncio
import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.database import get_db
from app.models import EducationWechatPublication, User
from app.routers.education import (
    _owned_preview_context,
    _artifact_locks,
    _preview_sandbox,
    _read_artifact_bytes,
)
from app.services.wechat_broker_client import (
    WechatAPIError,
    WechatAuthorizationRequired,
    WechatBrokerClient,
    WechatConfigurationError,
)
from app.services.wechat_publish_service import (
    WechatPublishService,
    WechatPublishUncertainError,
)


router = APIRouter(prefix="/wechat", tags=["wechat"])
_release_locks: dict[str, asyncio.Lock] = {}
_TOKEN_ERRCODES = {40001, 40014, 42001}


class AuthorizationResponse(BaseModel):
    authorized: bool
    authorizerAppid: str | None = None
    authUrl: str | None = None
    expiresAt: datetime | None = None


class DraftRequest(BaseModel):
    revision: str = Field(pattern=r"^[0-9a-f]{64}$")


class ReleaseResponse(BaseModel):
    jobId: str
    status: str
    mediaId: str | None = None
    contentRevision: str | None = None
    publishId: str | None = None
    authorizerAppid: str | None = None
    updatedAt: datetime | None = None


class ResolvePublishRequest(BaseModel):
    published: bool


def _service_error(exc: Exception) -> HTTPException:
    if isinstance(exc, WechatConfigurationError):
        return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    if isinstance(exc, WechatAuthorizationRequired):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))


def _release_response(release: EducationWechatPublication) -> ReleaseResponse:
    return ReleaseResponse(
        jobId=release.job_id,
        status=release.status,
        mediaId=release.media_id,
        contentRevision=release.content_revision,
        publishId=release.publish_id,
        authorizerAppid=release.authorizer_appid,
        updatedAt=release.updated_at,
    )


def _upstream_errcode(payload: dict) -> int:
    try:
        return int(payload.get("errcode") or 0)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("WeChat API returned an invalid errcode") from exc


@router.get("/authorization", response_model=AuthorizationResponse)
async def authorization_status(user: User = Depends(get_current_user)):
    try:
        binding = await WechatBrokerClient().authorization_status(user.id)
    except (WechatConfigurationError, WechatAPIError) as exc:
        raise _service_error(exc) from exc
    return AuthorizationResponse(
        authorized=binding is not None,
        authorizerAppid=binding.authorizer_appid if binding else None,
    )


@router.post("/authorization", response_model=AuthorizationResponse)
async def create_authorization(user: User = Depends(get_current_user)):
    try:
        auth_url, expires_at = await WechatBrokerClient().create_authorization_url(user.id)
    except (WechatConfigurationError, WechatAPIError) as exc:
        raise _service_error(exc) from exc
    return AuthorizationResponse(authorized=False, authUrl=auth_url, expiresAt=expires_at)


@router.get("/previews/{job_id}/release", response_model=ReleaseResponse | None)
async def release_status(
    job_id: str,
    session_id: str = Query(min_length=1, max_length=128),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _owned_preview_context(job_id, session_id, user, db, require_create=True)
    result = await db.execute(
        select(EducationWechatPublication).where(
            EducationWechatPublication.user_id == user.id,
            EducationWechatPublication.job_id == job_id,
        )
    )
    release = result.scalar_one_or_none()
    if release is not None and release.status == "publishing":
        updated_at = release.updated_at
        if updated_at is not None and updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=timezone.utc)
        if updated_at is not None and updated_at < datetime.now(timezone.utc) - timedelta(minutes=5):
            release.status = "unknown"
            release.last_error = "Publish worker stopped before recording a definitive result"
            release.updated_at = datetime.now(timezone.utc)
            await db.commit()
    return _release_response(release) if release else None


@router.post("/previews/{job_id}/resolve", response_model=ReleaseResponse)
async def resolve_publish_result(
    job_id: str,
    payload: ResolvePublishRequest,
    session_id: str = Query(min_length=1, max_length=128),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _owned_preview_context(job_id, session_id, user, db, require_create=True)
    result = await db.execute(
        select(EducationWechatPublication)
        .where(
            EducationWechatPublication.user_id == user.id,
            EducationWechatPublication.job_id == job_id,
        )
        .with_for_update()
    )
    release = result.scalar_one_or_none()
    if release is None or release.status not in {"unknown", "publishing"}:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="No uncertain publish result needs resolution")
    updated_at = release.updated_at
    if updated_at is not None and updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=timezone.utc)
    if release.status == "publishing" and updated_at and updated_at > datetime.now(timezone.utc) - timedelta(minutes=5):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Publish request is still running")
    release.status = "submitted" if payload.published else "failed"
    release.last_error = None
    release.operation_id = None
    release.updated_at = datetime.now(timezone.utc)
    await db.commit()
    return _release_response(release)


@router.post("/previews/{job_id}/draft", response_model=ReleaseResponse)
async def sync_draft(
    job_id: str,
    payload: DraftRequest,
    session_id: str = Query(min_length=1, max_length=128),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    lock = _release_locks.setdefault(f"{user.id}\0{job_id}", asyncio.Lock())
    if lock.locked():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="WeChat release operation is already running")
    async with lock:
        return await _sync_draft_locked(job_id, payload, session_id, user, db)


async def _sync_draft_locked(
    job_id: str,
    payload: DraftRequest,
    session_id: str,
    user: User,
    db: AsyncSession,
) -> ReleaseResponse:
    session, workspace = await _owned_preview_context(job_id, session_id, user, db, require_create=True)
    artifact_lock = _artifact_locks.setdefault(f"{workspace.id}\0{job_id}", asyncio.Lock())
    async with artifact_lock:
        content = await _read_artifact_bytes(_preview_sandbox(session), workspace, job_id)
        if content is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Preview HTML has not been saved")
        if hashlib.sha256(content).hexdigest() != payload.revision:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Preview HTML changed since it was opened")
        platform = WechatBrokerClient()
        try:
            access_token, binding = await platform.authorizer_access_token(user.id)
        except (WechatConfigurationError, WechatAPIError) as exc:
            raise _service_error(exc) from exc
        result = await db.execute(
            select(EducationWechatPublication)
            .where(
                EducationWechatPublication.user_id == user.id,
                EducationWechatPublication.job_id == job_id,
            )
            .with_for_update()
        )
        release = result.scalar_one_or_none()
        now = datetime.now(timezone.utc)
        operation_id = secrets.token_hex(16)
        if release is None:
            release = EducationWechatPublication(
                user_id=user.id,
                session_id=session_id,
                job_id=job_id,
                authorizer_appid=binding.authorizer_appid,
                content_revision=payload.revision,
                status="syncing",
                operation_id=operation_id,
                created_at=now,
                updated_at=now,
            )
            db.add(release)
        else:
            updated_at = release.updated_at
            if updated_at is not None and updated_at.tzinfo is None:
                updated_at = updated_at.replace(tzinfo=timezone.utc)
            if release.status in {"publishing", "unknown", "submitted"}:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This article cannot be resynced after publish submission")
            if release.status == "syncing" and updated_at and updated_at > now - timedelta(minutes=5):
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="WeChat draft sync is already running")
            release.session_id = session_id
            release.authorizer_appid = binding.authorizer_appid
            release.content_revision = payload.revision
            release.media_id = None
            release.publish_id = None
            release.status = "syncing"
            release.last_error = None
            release.operation_id = operation_id
            release.updated_at = now
        try:
            await db.commit()
        except IntegrityError as exc:
            await db.rollback()
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="WeChat draft sync is already running") from exc
        try:
            publisher = WechatPublishService()
            upstream = await publisher.sync_official_account_draft(
                authorizer_access_token=access_token,
                html=content.decode("utf-8", errors="strict"),
            )
            errcode = _upstream_errcode(upstream)
            if errcode in _TOKEN_ERRCODES:
                await platform.invalidate_authorizer_access_token(user.id, access_token)
                access_token, binding = await platform.authorizer_access_token(user.id)
                if binding.authorizer_appid != release.authorizer_appid:
                    raise WechatAPIError("The authorized official account changed during draft sync")
                upstream = await publisher.sync_official_account_draft(
                    authorizer_access_token=access_token,
                    html=content.decode("utf-8", errors="strict"),
                )
                errcode = _upstream_errcode(upstream)
            if errcode:
                raise RuntimeError(str(upstream.get("errmsg") or f"WeChat draft error {errcode}"))
            media_id = upstream.get("media_id")
            if not isinstance(media_id, str) or not media_id:
                raise RuntimeError("Draft service omitted media_id")
        except (WechatConfigurationError, WechatAPIError) as exc:
            await db.execute(
                update(EducationWechatPublication)
                .where(
                    EducationWechatPublication.id == release.id,
                    EducationWechatPublication.operation_id == operation_id,
                )
                .values(status="failed", last_error=str(exc), operation_id=None, updated_at=datetime.now(timezone.utc))
            )
            await db.commit()
            raise _service_error(exc) from exc
        except (RuntimeError, UnicodeDecodeError) as exc:
            await db.execute(
                update(EducationWechatPublication)
                .where(
                    EducationWechatPublication.id == release.id,
                    EducationWechatPublication.operation_id == operation_id,
                )
                .values(status="failed", last_error=str(exc), operation_id=None, updated_at=datetime.now(timezone.utc))
            )
            await db.commit()
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
        completed = await db.execute(
            update(EducationWechatPublication)
            .where(
                EducationWechatPublication.id == release.id,
                EducationWechatPublication.operation_id == operation_id,
            )
            .values(media_id=media_id, status="draft", last_error=None, operation_id=None, updated_at=datetime.now(timezone.utc))
        )
        await db.commit()
        if completed.rowcount != 1:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A newer WeChat draft operation replaced this result")
        await db.refresh(release)
        return _release_response(release)


@router.post("/previews/{job_id}/publish", response_model=ReleaseResponse)
async def publish_draft(
    job_id: str,
    session_id: str = Query(min_length=1, max_length=128),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    lock = _release_locks.setdefault(f"{user.id}\0{job_id}", asyncio.Lock())
    if lock.locked():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="WeChat release operation is already running")
    async with lock:
        return await _publish_draft_locked(job_id, session_id, user, db)


async def _publish_draft_locked(
    job_id: str,
    session_id: str,
    user: User,
    db: AsyncSession,
) -> ReleaseResponse:
    session, workspace = await _owned_preview_context(job_id, session_id, user, db, require_create=True)
    artifact_lock = _artifact_locks.setdefault(f"{workspace.id}\0{job_id}", asyncio.Lock())
    async with artifact_lock:
        platform = WechatBrokerClient()
        try:
            access_token, binding = await platform.authorizer_access_token(user.id)
        except (WechatConfigurationError, WechatAPIError) as exc:
            raise _service_error(exc) from exc
        result = await db.execute(
            select(EducationWechatPublication)
            .where(
                EducationWechatPublication.user_id == user.id,
                EducationWechatPublication.job_id == job_id,
            )
            .with_for_update()
        )
        release = result.scalar_one_or_none()
        if release is None or not release.media_id:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Sync the article to WeChat draft first")
        content = await _read_artifact_bytes(_preview_sandbox(session), workspace, job_id)
        if content is None or hashlib.sha256(content).hexdigest() != release.content_revision:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Preview changed after the WeChat draft was synced")
        if binding.authorizer_appid != release.authorizer_appid:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="The authorized official account changed; sync the draft again",
            )
        now = datetime.now(timezone.utc)
        updated_at = release.updated_at
        if updated_at is not None and updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=timezone.utc)
        if release.status in {"submitted", "unknown"}:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This article has already entered the publish flow")
        if release.status == "publishing":
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="The previous publish result is not yet known")
        if release.status == "syncing" and updated_at and updated_at > now - timedelta(minutes=5):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="WeChat release operation is already running")
        operation_id = secrets.token_hex(16)
        release.status = "publishing"
        release.operation_id = operation_id
        release.updated_at = now
        await db.commit()
        try:
            publisher = WechatPublishService()
            upstream = await publisher.submit_official_account_publish(
                media_id=release.media_id,
                authorizer_access_token=access_token,
            )
            errcode = _upstream_errcode(upstream)
            if errcode in _TOKEN_ERRCODES:
                await platform.invalidate_authorizer_access_token(user.id, access_token)
                access_token, binding = await platform.authorizer_access_token(user.id)
                if binding.authorizer_appid != release.authorizer_appid:
                    raise WechatAPIError("The authorized official account changed during publish")
                upstream = await publisher.submit_official_account_publish(
                    media_id=release.media_id,
                    authorizer_access_token=access_token,
                )
                errcode = _upstream_errcode(upstream)
        except (WechatConfigurationError, WechatAPIError) as exc:
            await db.execute(
                update(EducationWechatPublication)
                .where(
                    EducationWechatPublication.id == release.id,
                    EducationWechatPublication.operation_id == operation_id,
                )
                .values(status="failed", last_error=str(exc), operation_id=None, updated_at=datetime.now(timezone.utc))
            )
            await db.commit()
            raise _service_error(exc) from exc
        except WechatPublishUncertainError as exc:
            await db.execute(
                update(EducationWechatPublication)
                .where(
                    EducationWechatPublication.id == release.id,
                    EducationWechatPublication.operation_id == operation_id,
                )
                .values(status="unknown", last_error=str(exc), updated_at=datetime.now(timezone.utc))
            )
            await db.commit()
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Publish submission result is unknown; check the WeChat admin before any retry",
            ) from exc
        except RuntimeError as exc:
            await db.execute(
                update(EducationWechatPublication)
                .where(
                    EducationWechatPublication.id == release.id,
                    EducationWechatPublication.operation_id == operation_id,
                )
                .values(status="failed", last_error=str(exc), operation_id=None, updated_at=datetime.now(timezone.utc))
            )
            await db.commit()
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
        publish_id = upstream.get("publish_id")
        if errcode:
            message = str(upstream.get("errmsg") or f"WeChat publish error {errcode}")
            await db.execute(
                update(EducationWechatPublication)
                .where(
                    EducationWechatPublication.id == release.id,
                    EducationWechatPublication.operation_id == operation_id,
                )
                .values(status="failed", last_error=message, operation_id=None, updated_at=datetime.now(timezone.utc))
            )
            await db.commit()
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=message)
        if not isinstance(publish_id, str) or not publish_id:
            await db.execute(
                update(EducationWechatPublication)
                .where(
                    EducationWechatPublication.id == release.id,
                    EducationWechatPublication.operation_id == operation_id,
                )
                .values(status="unknown", last_error="Publish service omitted publish_id", updated_at=datetime.now(timezone.utc))
            )
            await db.commit()
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Publish submission result is unknown")
        completed = await db.execute(
            update(EducationWechatPublication)
            .where(
                EducationWechatPublication.id == release.id,
                EducationWechatPublication.operation_id == operation_id,
            )
            .values(publish_id=publish_id, status="submitted", last_error=None, operation_id=None, updated_at=datetime.now(timezone.utc))
        )
        await db.commit()
        if completed.rowcount != 1:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A newer WeChat publish operation replaced this result")
        await db.refresh(release)
        return _release_response(release)
