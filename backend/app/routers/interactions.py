from typing import Any, Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.config import get_sandboxes
from app.database import get_db
from app.models import User, UserSession, UserWorkspace
from app.sandbox.manager import sandbox_auth


router = APIRouter(prefix="/interactions", tags=["interactions"])


class PermissionReply(BaseModel):
    reply: Literal["once", "always", "reject"]


class QuestionReply(BaseModel):
    answers: list[list[str]]


async def _owned_context(user: User, db: AsyncSession) -> tuple[dict, UserWorkspace, set[str]]:
    result = await db.execute(
        select(UserWorkspace).where(UserWorkspace.user_id == user.id)
    )
    workspace = result.scalar_one_or_none()
    if not workspace:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    sandbox = get_sandboxes().get(workspace.sandbox_id)
    if not sandbox:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Sandbox unavailable")
    sessions = await db.execute(
        select(UserSession.session_id).where(
            UserSession.user_id == user.id,
            UserSession.workspace_id == workspace.id,
        )
    )
    return sandbox, workspace, set(sessions.scalars().all())


async def _pending(kind: str, user: User, db: AsyncSession) -> tuple[list[dict[str, Any]], dict, UserWorkspace]:
    sandbox, workspace, owned_sessions = await _owned_context(user, db)
    try:
        async with httpx.AsyncClient(timeout=10.0, auth=sandbox_auth(sandbox)) as client:
            response = await client.get(
                f"{sandbox['url']}/{kind}",
                params={"directory": workspace.directory},
            )
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Unable to load pending interaction") from exc
    items = payload if isinstance(payload, list) else []
    return (
        [item for item in items if isinstance(item, dict) and item.get("sessionID") in owned_sessions],
        sandbox,
        workspace,
    )


@router.get("")
async def list_interactions(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    permissions, _, _ = await _pending("permission", user, db)
    questions, _, _ = await _pending("question", user, db)
    return {"permissions": permissions, "questions": questions}


async def _require_request(kind: str, request_id: str, user: User, db: AsyncSession) -> tuple[dict, UserWorkspace]:
    items, sandbox, workspace = await _pending(kind, user, db)
    if not any(item.get("id") == request_id for item in items):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Interaction not found")
    return sandbox, workspace


async def _reply(kind: str, request_id: str, suffix: str, body: dict | None, user: User, db: AsyncSession):
    sandbox, workspace = await _require_request(kind, request_id, user, db)
    try:
        async with httpx.AsyncClient(timeout=10.0, auth=sandbox_auth(sandbox)) as client:
            kwargs = {"json": body} if body is not None else {}
            response = await client.post(
                f"{sandbox['url']}/{kind}/{request_id}/{suffix}",
                params={"directory": workspace.directory},
                **kwargs,
            )
            response.raise_for_status()
            return response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Unable to reply to interaction") from exc


@router.post("/permission/{request_id}/reply")
async def reply_permission(
    request_id: str,
    payload: PermissionReply,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await _reply("permission", request_id, "reply", {"reply": payload.reply}, user, db)


@router.post("/question/{request_id}/reply")
async def reply_question(
    request_id: str,
    payload: QuestionReply,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await _reply("question", request_id, "reply", {"answers": payload.answers}, user, db)


@router.post("/question/{request_id}/reject")
async def reject_question(
    request_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await _reply("question", request_id, "reject", None, user, db)
