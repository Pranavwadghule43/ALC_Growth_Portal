"""Regression tests: validation failures must always be a JSON 422, never a 500.

A Pydantic validator that raises ``ValueError`` puts the raw exception object into the
error's ``ctx`` (``{"error": ValueError(...)}``). Returning ``exc.errors()`` unchanged from
the central ``RequestValidationError`` handler made the JSON encoder fail, so the client
got a 500 instead of the 422. These tests pin the handler's output shape and its
serialisability.
"""
import json
from datetime import date, timedelta

import pytest
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel, Field, field_validator, model_validator

from app.main import app as real_app
from app.main import unhandled_error, validation_error
from tests.conftest import login

ACTIVITY = {
    "activity_type": "Partner meeting",
    "ecosystem": "College",
    "collaboration_type": "Pilot discussion",
    "activity_date": str(date.today()),
    "location": "Pune",
    "learners_reached": 25,
    "leads_generated": 10,
    "admissions_generated": 2,
    "description": "Discussed a structured learner outreach pilot.",
    "outcome": "Pilot agreed",
}


# --------------------------------------------------------------------------- #
# Smallest isolated reproduction: a throwaway app wired with the real handlers.
# --------------------------------------------------------------------------- #
class _FieldValueErrorIn(BaseModel):
    code: str = Field(min_length=2)

    @field_validator("code")
    @classmethod
    def no_reserved_code(cls, value: str) -> str:
        if value == "reserved":
            raise ValueError("Code is reserved")
        return value


class _ModelValueErrorIn(BaseModel):
    start: date
    end: date

    @model_validator(mode="after")
    def ordered(self):
        if self.end < self.start:
            raise ValueError("End must not be before start")
        return self


class _AssertionIn(BaseModel):
    value: int

    @field_validator("value")
    @classmethod
    def even(cls, value: int) -> int:
        assert value % 2 == 0, "Value must be even"
        return value


def _isolated_app() -> FastAPI:
    isolated = FastAPI()
    isolated.add_exception_handler(RequestValidationError, validation_error)
    isolated.add_exception_handler(Exception, unhandled_error)

    @isolated.post("/field")
    async def field(payload: _FieldValueErrorIn):
        return {"ok": payload.code}

    @isolated.post("/model")
    async def model(payload: _ModelValueErrorIn):
        return {"ok": True}

    @isolated.post("/assertion")
    async def assertion(payload: _AssertionIn):
        return {"ok": payload.value}

    @isolated.get("/query")
    async def query(limit: int = 10):
        return {"ok": limit}

    return isolated


@pytest.fixture
async def isolated_client():
    # raise_app_exceptions=False: observe the status a real client would get (500 before
    # the fix) instead of having the transport re-raise the handler's TypeError.
    transport = ASGITransport(app=_isolated_app(), raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _assert_clean_validation_body(response, *, expect_msg: str | None = None) -> list[dict]:
    assert response.status_code == 422, response.text
    body = response.json()
    # Existing envelope is preserved.
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert body["error"]["message"] == "Please check the submitted information"
    details = body["error"]["details"]
    assert isinstance(details, list) and details
    for item in details:
        assert {"type", "loc", "msg"} <= item.keys()
        assert isinstance(item["loc"], list)
    # Fully JSON round-trippable, and no Python exception representation leaks.
    json.dumps(body, allow_nan=False)
    assert "ValueError(" not in response.text
    assert "AssertionError(" not in response.text
    assert "Traceback" not in response.text
    if expect_msg is not None:
        assert any(expect_msg in item["msg"] for item in details), details
    return details


async def test_field_value_error_is_422_not_500(isolated_client):
    response = await isolated_client.post("/field", json={"code": "reserved"})
    details = _assert_clean_validation_body(response, expect_msg="Code is reserved")
    [item] = details
    assert item["type"] == "value_error"
    assert item["loc"] == ["body", "code"]
    # The exception object is replaced by its message, not dropped or repr()'d.
    assert item["ctx"] == {"error": "Code is reserved"}


async def test_model_value_error_is_422_not_500(isolated_client):
    response = await isolated_client.post(
        "/model", json={"start": "2026-01-10", "end": "2026-01-01"}
    )
    [item] = _assert_clean_validation_body(response, expect_msg="End must not be before start")
    assert item["ctx"] == {"error": "End must not be before start"}
    assert item["loc"] == ["body"]


async def test_assertion_validator_is_422_not_500(isolated_client):
    response = await isolated_client.post("/assertion", json={"value": 3})
    [item] = _assert_clean_validation_body(response, expect_msg="Value must be even")
    # (pytest's assertion rewriting appends its own explanation to the message here.)
    assert item["ctx"]["error"].startswith("Value must be even")


async def test_normal_pydantic_error_is_unchanged(isolated_client):
    response = await isolated_client.post("/field", json={"code": "x"})
    [item] = _assert_clean_validation_body(response)
    assert item["type"] == "string_too_short"
    assert item["loc"] == ["body", "code"]
    assert item["ctx"] == {"min_length": 2}
    assert item["input"] == "x"


async def test_query_param_error_is_422(isolated_client):
    response = await isolated_client.get("/query", params={"limit": "many"})
    [item] = _assert_clean_validation_body(response)
    assert item["loc"] == ["query", "limit"]
    assert item["type"] == "int_parsing"


async def test_non_finite_float_input_is_422_not_500(isolated_client):
    # Python's JSON parser accepts NaN; echoing it back must not break strict JSON output.
    response = await isolated_client.post(
        "/assertion", content=b'{"value": NaN}', headers={"Content-Type": "application/json"}
    )
    [item] = _assert_clean_validation_body(response)
    assert item["input"] == "nan"


async def test_valid_requests_are_unaffected(isolated_client):
    assert (await isolated_client.post("/field", json={"code": "ok"})).json() == {"ok": "ok"}
    model = await isolated_client.post("/model", json={"start": "2026-01-01", "end": "2026-01-10"})
    assert model.status_code == 200
    assert (await isolated_client.get("/query", params={"limit": 3})).json() == {"ok": 3}


async def test_handler_output_is_json_serialisable_for_raw_exception_ctx():
    exc = RequestValidationError(
        [
            {
                "type": "value_error",
                "loc": ("body",),
                "msg": "Value error, Broken",
                "input": {"when": date(2026, 1, 1), "raw": b"\xff\xfe"},
                "ctx": {"error": ValueError("Broken"), "limit": date(2026, 1, 2)},
            }
        ]
    )
    response = await validation_error(None, exc)
    assert response.status_code == 422
    body = json.loads(response.body)
    [item] = body["error"]["details"]
    assert item["ctx"] == {"error": "Broken", "limit": "2026-01-02"}
    assert item["input"]["when"] == "2026-01-01"
    assert isinstance(item["input"]["raw"], str)
    assert item["loc"] == ["body"]


# --------------------------------------------------------------------------- #
# The real application endpoint that exposed the bug.
# --------------------------------------------------------------------------- #
@pytest.fixture
async def strict_client(client):
    """The real app, but reporting server errors as responses (a 500 stays a 500)."""
    transport = ASGITransport(app=real_app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_future_activity_date_is_422_with_message(strict_client):
    assert (await login(strict_client, "00010001", "StrongAlcPassA!", "ALC")).status_code == 200
    future = {**ACTIVITY, "activity_date": str(date.today() + timedelta(days=1))}
    response = await strict_client.post("/api/portal/activities", json=future)
    [item] = _assert_clean_validation_body(
        response, expect_msg="Future activity dates are not allowed"
    )
    assert item["type"] == "value_error"
    assert item["ctx"] == {"error": "Future activity dates are not allowed"}


async def test_future_activity_date_update_is_422(strict_client):
    assert (await login(strict_client, "00010001", "StrongAlcPassA!", "ALC")).status_code == 200
    created = await strict_client.post("/api/portal/activities", json=ACTIVITY)
    assert created.status_code == 201
    future = {**ACTIVITY, "activity_date": str(date.today() + timedelta(days=30))}
    response = await strict_client.patch(
        f"/api/portal/activities/{created.json()['id']}", json=future
    )
    _assert_clean_validation_body(response, expect_msg="Future activity dates are not allowed")


async def test_real_app_ordinary_validation_error_is_422(strict_client):
    assert (await login(strict_client, "00010001", "StrongAlcPassA!", "ALC")).status_code == 200
    response = await strict_client.post(
        "/api/portal/activities", json={**ACTIVITY, "learners_reached": -1}
    )
    [item] = _assert_clean_validation_body(response)
    assert item["loc"] == ["body", "learners_reached"]
    assert item["type"] == "greater_than_equal"


async def test_real_app_valid_activity_still_created(strict_client):
    assert (await login(strict_client, "00010001", "StrongAlcPassA!", "ALC")).status_code == 200
    response = await strict_client.post("/api/portal/activities", json=ACTIVITY)
    assert response.status_code == 201
    assert response.json()["activity_date"] == str(date.today())
