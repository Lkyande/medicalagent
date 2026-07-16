from sqlalchemy import Column, String, DateTime, ForeignKey, Boolean
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
import uuid
from app.database import Base


def _gen_id() -> str:
    return uuid.uuid4().hex


class User(Base):
    __tablename__ = "users"
    id = Column(String(64), primary_key=True, default=_gen_id)
    email = Column(String(255), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    sandbox_id = Column(String(64), nullable=True)
    sandbox_assignments = relationship("SandboxAssignment", back_populates="user")


class SandboxAssignment(Base):
    __tablename__ = "sandbox_assignments"
    id = Column(String(64), primary_key=True, default=_gen_id)
    user_id = Column(String(64), ForeignKey("users.id"), nullable=False)
    sandbox_id = Column(String(64), nullable=False)
    active = Column(Boolean, default=True)
    assigned_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    user = relationship("User", back_populates="sandbox_assignments")


class UserWorkspace(Base):
    __tablename__ = "user_workspaces"
    id = Column(String(64), primary_key=True, default=_gen_id)
    user_id = Column(String(64), ForeignKey("users.id"), nullable=False, unique=True, index=True)
    sandbox_id = Column(String(64), nullable=False, index=True)
    directory = Column(String(512), nullable=False, unique=True)
    ready = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class UserSession(Base):
    __tablename__ = "user_sessions"
    session_id = Column(String(128), primary_key=True)
    user_id = Column(String(64), ForeignKey("users.id"), nullable=False, index=True)
    workspace_id = Column(String(64), ForeignKey("user_workspaces.id"), nullable=False, index=True)
    sandbox_id = Column(String(64), nullable=False, index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
