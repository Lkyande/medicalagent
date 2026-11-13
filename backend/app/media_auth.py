from contextvars import ContextVar, Token
from dataclasses import dataclass

import jwt

from app.config import settings
from app.models import UserWorkspace


MEDIA_MCP_AUDIENCE = "media-mcp"


@dataclass(frozen=True)
class MediaIdentity:
    user_id: str
    workspace_id: str


_media_identity: ContextVar[MediaIdentity | None] = ContextVar("media_identity", default=None)


def _media_signing_key() -> str:
    value = settings.media_mcp_signing_key
    if len(value) < 32:
        raise jwt.InvalidTokenError("MEDIA_MCP_SIGNING_KEY must contain at least 32 characters")
    return value


def issue_media_mcp_token(workspace: UserWorkspace) -> str:
    return jwt.encode(
        {
            "sub": workspace.user_id,
            "workspace_id": workspace.id,
            "aud": MEDIA_MCP_AUDIENCE,
        },
        _media_signing_key(),
        algorithm=settings.jwt_algorithm,
    )


def decode_media_mcp_token(value: str) -> MediaIdentity:
    payload = jwt.decode(
        value,
        _media_signing_key(),
        algorithms=[settings.jwt_algorithm],
        audience=MEDIA_MCP_AUDIENCE,
    )
    user_id = payload.get("sub")
    workspace_id = payload.get("workspace_id")
    if not isinstance(user_id, str) or not user_id or not isinstance(workspace_id, str) or not workspace_id:
        raise jwt.InvalidTokenError("Media MCP token is missing identity claims")
    return MediaIdentity(user_id=user_id, workspace_id=workspace_id)


def bind_media_identity(identity: MediaIdentity) -> Token:
    return _media_identity.set(identity)


def reset_media_identity(token: Token) -> None:
    _media_identity.reset(token)


def require_media_identity() -> MediaIdentity:
    identity = _media_identity.get()
    if identity is None:
        raise PermissionError("Media MCP identity is unavailable")
    return identity
