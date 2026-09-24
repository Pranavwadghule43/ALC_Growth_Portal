import uuid
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

from app.enums import ActivityStatus, AlcStatus, ReviewAction, Role, TaskStatus
from app.models import Activity
from app.services.evidence_history import removed_evidence


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class DcuBrief(ORMModel):
    id: uuid.UUID
    code: str
    name: str
    rcu_id: uuid.UUID
    is_active: bool


class SbuBrief(ORMModel):
    id: uuid.UUID
    code: str
    name: str
    is_active: bool
    dcu_id: uuid.UUID | None = None


class AlcBrief(ORMModel):
    id: uuid.UUID
    alc_code: str
    alc_name: str
    status: AlcStatus
    sbu_id: uuid.UUID | None = None
    sbu: SbuBrief | None = None


class UserOut(ORMModel):
    id: uuid.UUID
    username: str
    email: EmailStr | None
    role: Role
    alc_id: uuid.UUID | None
    sbu_id: uuid.UUID | None = None
    dcu_id: uuid.UUID | None = None
    alc: AlcBrief | None = None
    sbu: SbuBrief | None = None
    dcu: DcuBrief | None = None
    is_active: bool
    must_change_password: bool


class LoginIn(BaseModel):
    identifier: str = Field(min_length=2, max_length=255)
    password: str = Field(min_length=8, max_length=256)


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


class RemovedEvidenceOut(EvidenceOut):
    """Evidence removed by the ALC after a reviewer saw it; kept for the review record."""

    submitted_in: list[int] = []  # revision numbers whose submission included this file
    recorded: bool = True  # False: inferred for submissions made before evidence was recorded

class ReviewOut(ORMModel):
    id: uuid.UUID
    previous_status: ActivityStatus
    new_status: ActivityStatus
    action: str
    remark: str | None
    reviewed_at: datetime
    reviewer_role: str | None = None
    is_decision_change: bool = False


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
    removed_evidence: list[RemovedEvidenceOut] = []

    @model_validator(mode="before")
    @classmethod
    def with_removed_evidence(cls, value):
        # Built from an ORM Activity: add the historical (removed) evidence alongside it.
        if isinstance(value, Activity):
            return _ActivityWithHistory(value, removed_evidence(value))
        return value

    @field_validator("evidence", mode="before")
    @classmethod
    def active_evidence_only(cls, value):
        return [item for item in value if getattr(item, "is_active", True)]


class _ActivityWithHistory:
    """Read-only view of an Activity plus its removed evidence, for ActivityOut."""

    def __init__(self, activity: Activity, removed: list[dict[str, Any]]):
        self._activity = activity
        self.removed_evidence = removed

    def __getattr__(self, name: str) -> Any:
        return getattr(self._activity, name)


class ReviewDecisionIn(BaseModel):
    remark: str | None = Field(default=None, max_length=4000)


class DecisionChangeIn(BaseModel):
    """Override a final decision. The reason is mandatory for every target decision."""

    decision: ReviewAction
    remark: str = Field(min_length=1, max_length=4000)


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
    sbu_id: uuid.UUID | None = None
    dcu_id: uuid.UUID | None = None
    password: str = Field(min_length=12, max_length=256)
    must_change_password: bool = True


class UserPatch(BaseModel):
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=12, max_length=256)
    must_change_password: bool | None = None


class AlcStatusPatch(BaseModel):
    status: AlcStatus | None = None
    sbu_id: uuid.UUID | None = None


class SbuIn(BaseModel):
    code: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=2, max_length=255)
    is_active: bool = True
    dcu_id: uuid.UUID | None = None


class SbuPatch(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=255)
    is_active: bool | None = None
    # Reassign the SBU to another DCU. Only applied when the field is present in the request;
    # an explicit ``null`` detaches the SBU from any DCU.
    dcu_id: uuid.UUID | None = None


class SbuOut(ORMModel):
    id: uuid.UUID
    code: str
    name: str
    is_active: bool
    dcu_id: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime


class DcuOut(ORMModel):
    id: uuid.UUID
    code: str
    name: str
    rcu_id: uuid.UUID
    is_active: bool
    created_at: datetime
    updated_at: datetime


class PasswordResetIn(BaseModel):
    password: str = Field(min_length=12, max_length=256)
