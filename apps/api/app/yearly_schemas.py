"""Bounded inputs for the regional yearly activity workflow."""
import re
import unicodedata
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class DefinitionIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    calendar_year: int = Field(ge=2000, le=2100)
    activity_code: str = Field(min_length=2, max_length=64)
    name: str = Field(min_length=2, max_length=180)
    description: str | None = Field(default=None, max_length=10000)

    @field_validator("activity_code")
    @classmethod
    def canonical_code(cls, value: str) -> str:
        value = unicodedata.normalize("NFKC", value).strip().upper()
        value = re.sub(r"[\s_]+", "-", value)
        if not re.fullmatch(r"[A-Z0-9][A-Z0-9-]{1,63}", value):
            raise ValueError("Use 2–64 letters, numbers or hyphens for the activity code")
        return value


class RunIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    scheduled_start_date: date | None = None
    scheduled_end_date: date | None = None
    instructions: str | None = Field(default=None, max_length=10000)
    status: Literal["SCHEDULED", "ACTIVE", "MARKING"] | None = None

    @model_validator(mode="after")
    def dates_together(self):
        fields = self.model_fields_set
        if ("scheduled_start_date" in fields) != ("scheduled_end_date" in fields):
            raise ValueError("Supply both schedule dates together")
        if self.scheduled_start_date and self.scheduled_end_date:
            if self.scheduled_end_date < self.scheduled_start_date:
                raise ValueError("End date must be on or after start date")
        if not fields:
            raise ValueError("Supply a schedule, instructions or status")
        return self


class CompletionIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    completed: bool | None
