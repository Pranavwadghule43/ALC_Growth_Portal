from enum import StrEnum


class Role(StrEnum):
    ADMIN = "ADMIN"
    SBU = "SBU"
    ALC = "ALC"


class AlcStatus(StrEnum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


class ActivityStatus(StrEnum):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    UNDER_REVIEW = "UNDER_REVIEW"
    CORRECTION_REQUIRED = "CORRECTION_REQUIRED"
    RESUBMITTED = "RESUBMITTED"
    VERIFIED = "VERIFIED"
    REJECTED = "REJECTED"


class ReviewAction(StrEnum):
    VERIFY = "VERIFY"
    REQUEST_CORRECTION = "REQUEST_CORRECTION"
    REJECT = "REJECT"


class TaskStatus(StrEnum):
    OPEN = "OPEN"
    COMPLETED = "COMPLETED"
