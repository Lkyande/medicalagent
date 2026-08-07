from sqlalchemy import Column, String, DateTime, ForeignKey, Boolean, Text, UniqueConstraint
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
import uuid
from app.database import Base


def _gen_id() -> str:
    return uuid.uuid4().hex


class User(Base):
    __tablename__ = "users"
    id = Column(String(64), primary_key=True, default=_gen_id)
    email = Column(String(255), unique=True, nullable=True, index=True)
    phone = Column(String(32), unique=True, nullable=True, index=True)
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


class EducationWechatPublication(Base):
    __tablename__ = "education_wechat_publications"
    __table_args__ = (UniqueConstraint("user_id", "job_id", name="uq_education_wechat_user_job"),)

    id = Column(String(64), primary_key=True, default=_gen_id)
    user_id = Column(String(64), ForeignKey("users.id"), nullable=False, index=True)
    session_id = Column(String(128), nullable=False, index=True)
    job_id = Column(String(128), nullable=False, index=True)
    authorizer_appid = Column(String(64), nullable=True)
    media_id = Column(String(255), nullable=True)
    content_revision = Column(String(64), nullable=False)
    publish_id = Column(String(255), nullable=True)
    status = Column(String(32), nullable=False, default="draft")
    last_error = Column(Text, nullable=True)
    operation_id = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)


class MediaGenerationJob(Base):
    __tablename__ = "media_generation_jobs"
    __table_args__ = (
        UniqueConstraint("video_id", name="uq_media_generation_video"),
        UniqueConstraint(
            "user_id",
            "workspace_id",
            "request_fingerprint",
            name="uq_media_generation_request",
        ),
    )

    id = Column(String(64), primary_key=True, default=_gen_id)
    user_id = Column(String(64), ForeignKey("users.id"), nullable=False, index=True)
    workspace_id = Column(String(64), ForeignKey("user_workspaces.id"), nullable=False, index=True)
    video_id = Column(String(200), nullable=False, index=True)
    request_fingerprint = Column(String(64), nullable=False)
    generation_type = Column(String(32), nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
