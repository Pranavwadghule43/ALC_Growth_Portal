import uuid
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import JSON as SAJSON
from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.enums import ActivityStatus, AlcStatus, ReviewAction, Role, TaskStatus


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class SBU(Base, TimestampMixin):
    __tablename__ = "sbus"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    alcs: Mapped[list["ALC"]] = relationship(back_populates="sbu")
    users: Mapped[list["User"]] = relationship(back_populates="sbu")


class ALC(Base, TimestampMixin):
    __tablename__ = "alcs"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    alc_code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    alc_name: Mapped[str] = mapped_column(String(255))
    sbu_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("sbus.id"), nullable=True, index=True
    )
    status: Mapped[AlcStatus] = mapped_column(
        Enum(AlcStatus, native_enum=False), default=AlcStatus.ACTIVE, index=True
    )
    sbu: Mapped["SBU | None"] = relationship(back_populates="alcs")
    users: Mapped[list["User"]] = relationship(back_populates="alc")


class User(Base, TimestampMixin):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    username: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    email: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    password_hash: Mapped[str] = mapped_column(String(512))
    role: Mapped[Role] = mapped_column(Enum(Role, native_enum=False), index=True)
    alc_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("alcs.id"), nullable=True, index=True
    )
    sbu_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("sbus.id"), nullable=True, index=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    alc: Mapped[ALC | None] = relationship(back_populates="users")
    sbu: Mapped["SBU | None"] = relationship(back_populates="users")


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Partner(Base, TimestampMixin):
    __tablename__ = "partners"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    alc_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("alcs.id"), index=True)
    partner_name: Mapped[str] = mapped_column(String(255), index=True)
    partner_type: Mapped[str] = mapped_column(String(100))
    ecosystem: Mapped[str] = mapped_column(String(100), index=True)
    contact_person: Mapped[str | None] = mapped_column(String(150))
    phone: Mapped[str | None] = mapped_column(String(30))
    email: Mapped[str | None] = mapped_column(String(255))
    location: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(30), default="ACTIVE", index=True)
    notes: Mapped[str | None] = mapped_column(Text)


class Activity(Base, TimestampMixin):
    __tablename__ = "activities"
    __table_args__ = (
        Index("ix_activities_alc_status", "alc_id", "status"),
        Index("ix_activities_submitted", "submitted_at"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    activity_number: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    alc_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("alcs.id"), index=True)
    partner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("partners.id"), index=True)
    activity_type: Mapped[str] = mapped_column(String(120), index=True)
    ecosystem: Mapped[str] = mapped_column(String(120), index=True)
    collaboration_type: Mapped[str | None] = mapped_column(String(120))
    activity_date: Mapped[date] = mapped_column(Date, index=True)
    location: Mapped[str] = mapped_column(String(255))
    learners_reached: Mapped[int] = mapped_column(Integer, default=0)
    leads_generated: Mapped[int] = mapped_column(Integer, default=0)
    admissions_generated: Mapped[int] = mapped_column(Integer, default=0)
    description: Mapped[str] = mapped_column(Text)
    outcome: Mapped[str] = mapped_column(Text)
    status: Mapped[ActivityStatus] = mapped_column(
        Enum(ActivityStatus, native_enum=False), default=ActivityStatus.DRAFT, index=True
    )
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verified_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    partner: Mapped[Partner | None] = relationship()
    evidence: Mapped[list["ActivityEvidence"]] = relationship(
        back_populates="activity", cascade="all, delete-orphan", lazy="selectin"
    )
    reviews: Mapped[list["ActivityReview"]] = relationship(
        back_populates="activity",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="ActivityReview.reviewed_at",
    )
    revisions: Mapped[list["ActivityRevision"]] = relationship(
        back_populates="activity",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="ActivityRevision.revision_number",
    )


class ActivityEvidence(Base):
    __tablename__ = "activity_evidence"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    activity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("activities.id", ondelete="CASCADE"), index=True
    )
    storage_key: Mapped[str] = mapped_column(String(512), unique=True)
    original_filename: Mapped[str] = mapped_column(String(255))
    mime_type: Mapped[str] = mapped_column(String(100))
    file_size: Mapped[int] = mapped_column(Integer)
    uploaded_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    activity: Mapped[Activity] = relationship(back_populates="evidence")


class ActivityReview(Base):
    __tablename__ = "activity_reviews"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    activity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("activities.id", ondelete="CASCADE"), index=True
    )
    reviewer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    previous_status: Mapped[ActivityStatus] = mapped_column(Enum(ActivityStatus, native_enum=False))
    new_status: Mapped[ActivityStatus] = mapped_column(Enum(ActivityStatus, native_enum=False))
    action: Mapped[ReviewAction] = mapped_column(Enum(ReviewAction, native_enum=False))
    remark: Mapped[str | None] = mapped_column(Text)
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    activity: Mapped[Activity] = relationship(back_populates="reviews")


class ActivityRevision(Base):
    __tablename__ = "activity_revisions"
    __table_args__ = (UniqueConstraint("activity_id", "revision_number"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    activity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("activities.id", ondelete="CASCADE"), index=True
    )
    revision_number: Mapped[int] = mapped_column(Integer)
    changed_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    change_summary: Mapped[str] = mapped_column(Text)
    snapshot: Mapped[dict[str, Any]] = mapped_column(SAJSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    activity: Mapped[Activity] = relationship(back_populates="revisions")


class Task(Base, TimestampMixin):
    __tablename__ = "tasks"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    alc_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("alcs.id"), index=True)
    partner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("partners.id"))
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    due_date: Mapped[date] = mapped_column(Date, index=True)
    status: Mapped[TaskStatus] = mapped_column(
        Enum(TaskStatus, native_enum=False), default=TaskStatus.OPEN, index=True
    )


class ChallengeProgress(Base, TimestampMixin):
    __tablename__ = "challenge_progress"
    __table_args__ = (UniqueConstraint("alc_id", "challenge_period_start", "challenge_period_end"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    alc_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("alcs.id"), index=True)
    challenge_period_start: Mapped[date] = mapped_column(Date)
    challenge_period_end: Mapped[date] = mapped_column(Date)
    prospects_target: Mapped[int] = mapped_column(Integer, default=40)
    meetings_target: Mapped[int] = mapped_column(Integer, default=20)
    pilots_target: Mapped[int] = mapped_column(Integer, default=10)
    partnerships_target: Mapped[int] = mapped_column(Integer, default=5)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), index=True)
    actor_role: Mapped[str | None] = mapped_column(String(20))
    alc_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("alcs.id"), index=True)
    action: Mapped[str] = mapped_column(String(100), index=True)
    entity_type: Mapped[str] = mapped_column(String(100))
    entity_id: Mapped[str | None] = mapped_column(String(100))
    audit_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", SAJSON().with_variant(JSONB, "postgresql"), default=dict
    )
    ip_address: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(180))
    message: Mapped[str] = mapped_column(Text)
    type: Mapped[str] = mapped_column(String(50))
    entity_type: Mapped[str | None] = mapped_column(String(50))
    entity_id: Mapped[str | None] = mapped_column(String(100))
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
