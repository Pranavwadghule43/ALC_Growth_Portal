import uuid
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

from app.enums import ActivityStatus, AlcStatus, Role, TaskStatus


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class AlcBrief(ORMModel):
    id: uuid.UUID
    alc_code: str
    alc_name: str
    status: AlcStatus


class UserOut(ORMModel):
    id: uuid.UUID
    username: str
    email: EmailStr | None
    role: Role
    alc_id: uuid.UUID | None
    alc: AlcBrief | None = None
    is_active: bool
    must_change_password: bool


class LoginIn(BaseModel):
    identifier: str = Field(min_length=2, max_length=255)
    password: str = Field(min_length=8, max_length=256)
    portal: Role


class ChangePasswordIn(BaseModel):
    current_password: str
    new_password: str = Field(min_length=12, max_length=256)


class PartnerIn(BaseModel):
    partner_name: str = Field(min_length=2, max_length=255)
    partner_type: str = Field(min_length=2, max_length=100)
    ecosystem: str = Field(min_length=2, max_length=100)
    contact_person: str | None = Field(default=None, max_length=150)
    phone: str | None = Field(default=None, max_length=30)
    email: EmailStr | None = None
    location: str | None = Field(default=None, max_length=255)
    status: str = Field(default="ACTIVE", max_length=30)
    notes: str | None = Field(default=None, max_length=4000)


class PartnerOut(PartnerIn, ORMModel):
    id: uuid.UUID
    alc_id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class ActivityIn(BaseModel):
    partner_id: uuid.UUID | None = None
    activity_type: str = Field(min_length=2, max_length=120)
    ecosystem: str = Field(min_length=2, max_length=120)
    collaboration_type: str | None = Field(default=None, max_length=120)
    activity_date: date
    location: str = Field(min_length=2, max_length=255)
    learners_reached: int = Field(default=0, ge=0)
    leads_generated: int = Field(default=0, ge=0)
    admissions_generated: int = Field(default=0, ge=0)
    description: str = Field(min_length=10, max_length=10000)
    outcome: str = Field(min_length=2, max_length=10000)

    @model_validator(mode="after")
    def no_future_activity(self):
        if self.activity_date > date.today():
            raise ValueError("Future activity dates are not allowed")
        return self


class EvidenceOut(ORMModel):
    id: uuid.UUID
    original_filename: str
    mime_type: str
    file_size: int
    uploaded_at: datetime


class ReviewOut(ORMModel):
    id: uuid.UUID
    previous_status: ActivityStatus
    new_status: ActivityStatus
    action: str
    remark: str | None
    reviewed_at: datetime


class RevisionOut(ORMModel):
    id: uuid.UUID
    revision_number: int
    change_summary: str
    snapshot: dict[str, Any]
    created_at: datetime


class ActivityOut(ActivityIn, ORMModel):
    id: uuid.UUID
    activity_number: str
    alc_id: uuid.UUID
    status: ActivityStatus
    submitted_at: datetime | None
    verified_at: datetime | None
    created_at: datetime
    updated_at: datetime
    partner: PartnerOut | None = None
    evidence: list[EvidenceOut] = []
    reviews: list[ReviewOut] = []
    revisions: list[RevisionOut] = []

    @field_validator("evidence", mode="before")
    @classmethod
    def active_evidence_only(cls, value):
        return [item for item in value if getattr(item, "is_active", True)]


class ReviewDecisionIn(BaseModel):
    remark: str | None = Field(default=None, max_length=4000)


class TaskIn(BaseModel):
    partner_id: uuid.UUID | None = None
    title: str = Field(min_length=2, max_length=255)
    description: str | None = Field(default=None, max_length=4000)
    due_date: date


class TaskOut(TaskIn, ORMModel):
    id: uuid.UUID
    alc_id: uuid.UUID
    status: TaskStatus
    created_at: datetime
    updated_at: datetime


class Paginated(BaseModel):
    items: list[Any]
    page: int
    page_size: int
    total: int
    pages: int


class UserCreate(BaseModel):
    username: str = Field(min_length=2, max_length=120)
    email: EmailStr | None = None
    role: Role
    alc_id: uuid.UUID | None = None
    password: str = Field(min_length=12, max_length=256)
    must_change_password: bool = True


class UserPatch(BaseModel):
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=12, max_length=256)
    must_change_password: bool | None = None


class AlcStatusPatch(BaseModel):
    status: AlcStatus
