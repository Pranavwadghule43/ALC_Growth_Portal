"""Activity CSV exports (Admin ``/api/admin/reports/activities.csv`` and the scoped portal
``/api/portal/reports/activities.csv``): content, filters, scope, CSV safety, and the
column-only / streamed query shape.

Everything here is self-contained: a small RCU → DCU → SBU → ALC hierarchy on top of
conftest's ``seeded`` data, with activities inserted directly so every exported value
(dates, statuses, metrics, awkward strings) is known exactly.
"""
import csv
import io
import math
import uuid
from datetime import date, datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import event, func, insert, select

from app.auth import hash_password
from app.database import get_db
from app.enums import ActivityStatus, ReviewAction, Role
from app.main import app
from app.models import (
    ALC,
    DCU,
    RCU,
    Activity,
    ActivityEvidence,
    ActivityReview,
    ActivityRevision,
    User,
)
from app.services import csv_export
from tests.conftest import login

PW = "StrongDcuPassword!"
ADMIN_HEADER = (
    "Activity Number,ALC Code,ALC Name,Date,Type,Ecosystem,Status,Learners,Leads,Admissions"
)
PORTAL_HEADER = (
    "Activity Number,ALC Code,ALC Name,SBU,Date,Type,Ecosystem,Status,Learners,Leads,Admissions"
)
COLLECTION_TABLES = ("activity_evidence", "activity_reviews", "activity_revisions")


def utc(day: int, hour: int = 10) -> datetime:
    return datetime(2025, 2, day, hour, tzinfo=timezone.utc)


# number, alc key, activity date, status, submitted_at, type, ecosystem, (learners, leads, adm)
ACTIVITIES = [
    ("ACT-T-001", "alc_a", date(2025, 1, 20), ActivityStatus.VERIFIED, utc(1),
     "Workshop", "College", (30, 12, 3)),
    ("ACT-T-002", "alc_a", date(2025, 1, 19), ActivityStatus.SUBMITTED, utc(3),
     '=HYPERLINK("http://evil.example")', 'School, "Rural"', (5, 1, 0)),
    ("ACT-T-003", "alc_c", date(2025, 1, 18), ActivityStatus.CORRECTION_REQUIRED, utc(5),
     "+cmd|' /C calc'!A0", "@SUM(1+1)", (0, 0, 0)),
    ("ACT-T-004", "alc_c", date(2025, 1, 17), ActivityStatus.DRAFT, None,
     "Draft visit", "College", (7, 7, 7)),
    ("ACT-T-005", "alc_b", date(2025, 1, 16), ActivityStatus.VERIFIED, utc(7),
     "Workshop", "Corporate", (40, 20, 4)),
    ("ACT-T-006", "alc_b", date(2025, 1, 15), ActivityStatus.REJECTED, utc(9),
     " -2+3", "College", (1, 2, 3)),
    ("ACT-T-007", "alc_d", date(2025, 1, 14), ActivityStatus.RESUBMITTED, utc(11),
     "Seminar", "Government", (9, 8, 7)),
]


@pytest_asyncio.fixture
async def world(session, seeded):
    """DCU X owns SBU 4 (Centres A, C); DCU Y owns SBU 6 (Centre B); Centre D has no SBU.

    Centre D is an incomplete record: the Admin export still lists its activities, but under
    Phase 4E its ALC login is unavailable (no SBU -> no DCU -> no RCU)."""
    rcu = RCU(code="RCU_T", name="RCU Test")
    session.add(rcu)
    await session.flush()
    dcu_x = DCU(code="DCU_X", name="DCU X", rcu_id=rcu.id)
    dcu_y = DCU(code="DCU_Y", name="DCU Y", rcu_id=rcu.id)
    session.add_all([dcu_x, dcu_y])
    await session.flush()
    seeded["sbu4"].dcu_id = dcu_x.id
    seeded["sbu6"].dcu_id = dcu_y.id
    alc_d = ALC(alc_code="00010004", alc_name='@Centre "D", East', sbu_id=None)
    session.add(alc_d)
    await session.flush()
    session.add_all(
        [
            User(username="dcu-x", password_hash=hash_password(PW), role=Role.DCU,
                 dcu_id=dcu_x.id),
            User(username="dcu-y", password_hash=hash_password(PW), role=Role.DCU,
                 dcu_id=dcu_y.id),
            User(username="alc-d", password_hash=hash_password(PW), role=Role.ALC,
                 alc_id=alc_d.id),
        ]
    )
    alcs = {"alc_a": seeded["alc_a"], "alc_b": seeded["alc_b"], "alc_c": seeded["alc_c"],
            "alc_d": alc_d}
    activities = {}
    for number, alc_key, day, status, submitted, kind, eco, (learn, leads, adm) in ACTIVITIES:
        activity = Activity(
            activity_number=number, alc_id=alcs[alc_key].id, activity_type=kind,
            ecosystem=eco, activity_date=day, location="Pune", learners_reached=learn,
            leads_generated=leads, admissions_generated=adm,
            description="Structured outreach session.", outcome="Done", status=status,
            submitted_at=submitted,
        )
        session.add(activity)
        activities[number] = activity
    await session.flush()
    # Collections (incl. historical evidence and a revision snapshot) the CSV never needs.
    first = activities["ACT-T-001"]
    removed_id = uuid.uuid4()
    session.add_all(
        [
            ActivityEvidence(activity_id=first.id, storage_key=f"private/{uuid.uuid4()}.pdf",
                             original_filename="live.pdf", mime_type="application/pdf",
                             file_size=5),
            ActivityEvidence(id=removed_id, activity_id=first.id,
                             storage_key=f"private/{uuid.uuid4()}.pdf",
                             original_filename="removed.pdf", mime_type="application/pdf",
                             file_size=5, is_active=False),
            ActivityReview(activity_id=first.id, previous_status=ActivityStatus.SUBMITTED,
                           new_status=ActivityStatus.VERIFIED, action=ReviewAction.VERIFY,
                           remark="ok", reviewer_role="DCU"),
            ActivityRevision(activity_id=first.id, revision_number=1,
                             change_summary="Submitted",
                             snapshot={"learners_reached": 30,
                                       "evidence": [{"id": str(removed_id)}]}),
        ]
    )
    await session.commit()
    return {**seeded, "alc_d": alc_d, "dcu_x": dcu_x, "dcu_y": dcu_y, "acts": activities}


async def as_user(client, identifier, password, portal="PORTAL"):
    client.cookies.clear()
    client.headers.pop("X-CSRF-Token", None)
    response = await login(client, identifier, password, portal)
    assert response.status_code == 200, response.text


def rows_of(text: str) -> list[dict]:
    return list(csv.DictReader(io.StringIO(text)))


def numbers(text: str) -> list[str]:
    return [row["Activity Number"] for row in rows_of(text)]


async def admin_csv(client, **params):
    response = await client.get("/api/admin/reports/activities.csv", params=params)
    assert response.status_code == 200, response.text
    return response


async def portal_csv(client, **params):
    response = await client.get("/api/portal/reports/activities.csv", params=params)
    assert response.status_code == 200, response.text
    return response


class StatementLog:
    """Records every SQL statement executed on the test engine while active."""

    def __init__(self, session):
        self.engine = session.bind.sync_engine
        self.statements: list[str] = []

    def _record(self, conn, cursor, statement, parameters, context, executemany):
        self.statements.append(statement)

    def __enter__(self):
        event.listen(self.engine, "before_cursor_execute", self._record)
        return self

    def __exit__(self, *exc):
        event.remove(self.engine, "before_cursor_execute", self._record)

    def touching(self, table: str) -> list[str]:
        return [s for s in self.statements if f" {table}" in s or f'"{table}"' in s]


class ActivityLoads:
    """Counts ORM ``Activity`` instances hydrated from the database while active."""

    def __init__(self):
        self.count = 0

    def _loaded(self, target, context):
        self.count += 1

    def __enter__(self):
        event.listen(Activity, "load", self._loaded)
        return self

    def __exit__(self, *exc):
        event.remove(Activity, "load", self._loaded)


# --------------------------------------------------------------------------- #
# Content, headers and CSV format
# --------------------------------------------------------------------------- #
async def test_admin_export_rows_headers_and_exact_format(client, world):
    await as_user(client, "admin", "StrongAdminPass!", "ADMIN")
    response = await admin_csv(client)
    assert response.headers["content-type"].startswith("text/csv")
    assert (
        response.headers["content-disposition"] == "attachment; filename=activity-report.csv"
    )
    lines = response.text.split("\r\n")
    assert lines[0] == ADMIN_HEADER
    # Every non-draft activity, any ALC, newest activity date first.
    assert numbers(response.text) == [
        "ACT-T-001", "ACT-T-002", "ACT-T-003", "ACT-T-005", "ACT-T-006", "ACT-T-007",
    ]
    assert lines[1:] == [
        "ACT-T-001,00010001,Centre A,2025-01-20,Workshop,College,VERIFIED,30,12,3",
        'ACT-T-002,00010001,Centre A,2025-01-19,"\'=HYPERLINK(""http://evil.example"")",'
        '"School, ""Rural""",SUBMITTED,5,1,0',
        "ACT-T-003,00010003,Centre C,2025-01-18,'+cmd|' /C calc'!A0,'@SUM(1+1)"
        ",CORRECTION_REQUIRED,0,0,0",
        "ACT-T-005,00010002,Centre B,2025-01-16,Workshop,Corporate,VERIFIED,40,20,4",
        "ACT-T-006,00010002,Centre B,2025-01-15,' -2+3,College,REJECTED,1,2,3",
        'ACT-T-007,00010004,"\'@Centre ""D"", East",2025-01-14,Seminar,Government,'
        "RESUBMITTED,9,8,7",
        "",
    ]


async def test_dcu_export_rows_headers_and_exact_format(client, world):
    await as_user(client, "dcu-x", PW)
    response = await portal_csv(client)
    assert (
        response.headers["content-disposition"] == "attachment; filename=portal-activities.csv"
    )
    lines = response.text.split("\r\n")
    assert lines[0] == PORTAL_HEADER
    assert lines[1:] == [
        "ACT-T-001,00010001,Centre A,SBU 4,2025-01-20,Workshop,College,VERIFIED,30,12,3",
        'ACT-T-002,00010001,Centre A,SBU 4,2025-01-19,"\'=HYPERLINK(""http://evil.example"")",'
        '"School, ""Rural""",SUBMITTED,5,1,0',
        "ACT-T-003,00010003,Centre C,SBU 4,2025-01-18,'+cmd|' /C calc'!A0,'@SUM(1+1)"
        ",CORRECTION_REQUIRED,0,0,0",
        "",
    ]


async def test_commas_quotes_and_formula_guard_round_trip(client, world):
    await as_user(client, "admin", "StrongAdminPass!", "ADMIN")
    rows = {r["Activity Number"]: r for r in rows_of((await admin_csv(client)).text)}
    assert rows["ACT-T-002"]["Type"] == "'=HYPERLINK(\"http://evil.example\")"
    assert rows["ACT-T-002"]["Ecosystem"] == 'School, "Rural"'
    assert rows["ACT-T-003"]["Type"] == "'+cmd|' /C calc'!A0"
    assert rows["ACT-T-003"]["Ecosystem"] == "'@SUM(1+1)"
    assert rows["ACT-T-006"]["Type"] == "' -2+3"  # leading whitespace does not bypass it
    assert rows["ACT-T-007"]["ALC Name"] == "'@Centre \"D\", East"
    for row in rows.values():
        for column, value in row.items():
            assert not value.lstrip().startswith(("=", "+", "-", "@")), (column, value)


async def test_alc_exports_only_its_own_activities_including_drafts(client, world):
    # Centre C sits in a fully active chain: SBU 4 -> DCU X -> RCU_T.
    await as_user(client, "00010003", "StrongAlcPassC!")
    text = (await portal_csv(client)).text
    rows = rows_of(text)
    # The owning ALC still exports its own draft (unchanged behaviour), and only its own rows.
    assert [(r["Activity Number"], r["Status"]) for r in rows] == [
        ("ACT-T-003", "CORRECTION_REQUIRED"), ("ACT-T-004", "DRAFT"),
    ]
    assert {(r["ALC Code"], r["SBU"]) for r in rows} == {("00010003", "SBU 4")}
    for other in ("ACT-T-001", "ACT-T-002", "ACT-T-005", "ACT-T-006", "ACT-T-007"):
        assert other not in text


async def test_alc_without_sbu_cannot_export(client, session, world):
    """Phase 4E: an ALC login needs its whole chain (ALC -> SBU -> DCU -> RCU) active, and a
    missing link fails closed. An ALC with no SBU therefore never reaches the portal export,
    so the old "blank SBU column for an ALC's own export" case is intentionally obsolete
    (the Admin export, which has no SBU column, still includes such records)."""
    alc_d = world["alc_d"]

    # No SBU: the login itself is refused and no session is created.
    client.cookies.clear()
    client.headers.pop("X-CSRF-Token", None)
    refused = await login(client, "00010004", PW, "PORTAL")
    assert refused.status_code == 401
    assert refused.json() == {"detail": "Account unavailable"}
    assert "refresh_token" not in refused.cookies

    # A session opened while the centre was placed under SBU 4 can export...
    alc_d.sbu_id = world["sbu4"].id
    await session.commit()
    session.expire(alc_d, ["sbu"])
    await as_user(client, "00010004", PW)
    [row] = rows_of((await portal_csv(client)).text)
    assert (row["Activity Number"], row["SBU"]) == ("ACT-T-007", "SBU 4")

    # ...but is refused on the next request once the centre has no SBU: no CSV at all.
    alc_d.sbu_id = None
    await session.commit()
    session.expire(alc_d, ["sbu"])
    response = await client.get("/api/portal/reports/activities.csv")
    assert response.status_code == 401
    assert response.json() == {"detail": "Account unavailable"}
    assert not response.headers["content-type"].startswith("text/csv")
    assert "content-disposition" not in response.headers
    assert "Activity Number" not in response.text and "ACT-T-007" not in response.text


async def test_empty_exports_are_header_only(client, world):
    await as_user(client, "admin", "StrongAdminPass!", "ADMIN")
    assert (await admin_csv(client, activity_type="Nothing")).text == ADMIN_HEADER + "\r\n"
    await as_user(client, "dcu-x", PW)
    assert (await portal_csv(client, ecosystem="Nothing")).text == PORTAL_HEADER + "\r\n"


# --------------------------------------------------------------------------- #
# Scope and authorization
# --------------------------------------------------------------------------- #
async def test_dcu_export_contains_only_its_own_scope(client, world):
    await as_user(client, "dcu-x", PW)
    text = (await portal_csv(client)).text
    assert numbers(text) == ["ACT-T-001", "ACT-T-002", "ACT-T-003"]
    assert {r["SBU"] for r in rows_of(text)} == {"SBU 4"}
    # Another DCU's rows, un-placed ALCs and drafts are absent.
    for absent in ("ACT-T-004", "ACT-T-005", "ACT-T-006", "ACT-T-007", "Centre B", "SBU 6"):
        assert absent not in text

    await as_user(client, "dcu-y", PW)
    text = (await portal_csv(client)).text
    assert numbers(text) == ["ACT-T-005", "ACT-T-006"]
    assert "Centre A" not in text and "Centre C" not in text


async def test_dcu_cannot_widen_scope_with_filters(client, world):
    await as_user(client, "dcu-x", PW)
    for params in ({"sbu_id": world["sbu6"].id}, {"alc_id": world["alc_b"].id},
                   {"alc_id": world["alc_d"].id}):
        response = await client.get("/api/portal/reports/activities.csv", params=params)
        assert response.status_code == 404, params


async def test_scope_follows_live_hierarchy(client, session, world):
    await as_user(client, "dcu-x", PW)
    assert "ACT-T-005" not in (await portal_csv(client)).text
    world["sbu6"].dcu_id = world["dcu_x"].id  # SBU 6 moves under DCU X
    await session.commit()
    assert numbers((await portal_csv(client)).text) == [
        "ACT-T-001", "ACT-T-002", "ACT-T-003", "ACT-T-005", "ACT-T-006",
    ]
    await as_user(client, "dcu-y", PW)
    assert numbers((await portal_csv(client)).text) == []


async def test_export_authorization_unchanged(client, world):
    await as_user(client, "dcu-x", PW)
    assert (await client.get("/api/admin/reports/activities.csv")).status_code == 403
    await as_user(client, "00010001", "StrongAlcPassA!")
    assert (await client.get("/api/admin/reports/activities.csv")).status_code == 403
    await as_user(client, "admin", "StrongAdminPass!", "ADMIN")
    assert (await client.get("/api/portal/reports/activities.csv")).status_code == 403
    client.cookies.clear()
    assert (await client.get("/api/admin/reports/activities.csv")).status_code == 401
    assert (await client.get("/api/portal/reports/activities.csv")).status_code == 401


# --------------------------------------------------------------------------- #
# Filters
# --------------------------------------------------------------------------- #
async def test_admin_export_filters(client, world):
    await as_user(client, "admin", "StrongAdminPass!", "ADMIN")
    cases = [
        ({"status": "VERIFIED"}, ["ACT-T-001", "ACT-T-005"]),
        ({"status": "DRAFT"}, []),  # drafts never leave the ALC
        ({"activity_type": "Workshop"}, ["ACT-T-001", "ACT-T-005"]),
        ({"ecosystem": "College"}, ["ACT-T-001", "ACT-T-006"]),
        ({"alc_id": world["alc_c"].id}, ["ACT-T-003"]),
        ({"alc_id": world["alc_b"].id, "status": "REJECTED"}, ["ACT-T-006"]),
        # Admin date filters apply to submission time: from inclusive, to exclusive.
        ({"date_from": "2025-02-05"}, ["ACT-T-003", "ACT-T-005", "ACT-T-006", "ACT-T-007"]),
        ({"date_to": "2025-02-05"}, ["ACT-T-001", "ACT-T-002"]),
        ({"date_from": "2025-02-03", "date_to": "2025-02-08"},
         ["ACT-T-002", "ACT-T-003", "ACT-T-005"]),
    ]
    for params, expected in cases:
        assert numbers((await admin_csv(client, **params)).text) == expected, params


async def test_portal_export_filters(client, world):
    await as_user(client, "dcu-x", PW)
    cases = [
        ({"status": "SUBMITTED"}, ["ACT-T-002"]),
        ({"status": "DRAFT"}, []),
        ({"activity_type": "Workshop"}, ["ACT-T-001"]),
        ({"ecosystem": "@SUM(1+1)"}, ["ACT-T-003"]),
        ({"alc_id": world["alc_a"].id}, ["ACT-T-001", "ACT-T-002"]),
        ({"sbu_id": world["sbu4"].id}, ["ACT-T-001", "ACT-T-002", "ACT-T-003"]),
        # Portal date filters apply to the activity date, both ends inclusive.
        ({"date_from": "2025-01-19"}, ["ACT-T-001", "ACT-T-002"]),
        ({"date_to": "2025-01-19"}, ["ACT-T-002", "ACT-T-003"]),
        ({"date_from": "2025-01-19", "date_to": "2025-01-19"}, ["ACT-T-002"]),
    ]
    for params, expected in cases:
        assert numbers((await portal_csv(client, **params)).text) == expected, params


async def test_search_is_not_an_export_filter(client, world):
    # Neither export has ever supported free-text search: an unknown parameter is ignored.
    await as_user(client, "admin", "StrongAdminPass!", "ADMIN")
    assert len(numbers((await admin_csv(client, search="Centre B")).text)) == 6
    await as_user(client, "dcu-x", PW)
    assert len(numbers((await portal_csv(client, search="ACT-T-001")).text)) == 3


async def test_invalid_filter_is_422(client, world):
    await as_user(client, "admin", "StrongAdminPass!", "ADMIN")
    response = await client.get("/api/admin/reports/activities.csv?date_from=yesterday")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


# --------------------------------------------------------------------------- #
# Query shape: column-only, no collection loading, streamed
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("who", ["admin", "dcu"])
async def test_export_selects_columns_only_and_loads_no_collections(
    client, session, world, who
):
    if who == "admin":
        await as_user(client, "admin", "StrongAdminPass!", "ADMIN")
        path, expected = "/api/admin/reports/activities.csv", 6
    else:
        await as_user(client, "dcu-x", PW)
        path, expected = "/api/portal/reports/activities.csv", 3
    with StatementLog(session) as log, ActivityLoads() as loads:
        response = await client.get(path)
    assert response.status_code == 200
    assert len(rows_of(response.text)) == expected
    assert loads.count == 0  # no Activity ORM object was hydrated
    for table in COLLECTION_TABLES:
        assert log.touching(table) == [], table
    [export_query] = log.touching("activities")  # one statement, whatever the row count
    assert "activities.description" not in export_query
    assert "activities.outcome" not in export_query


async def test_export_statement_count_is_constant_in_dataset_size(client, session, world):
    await as_user(client, "admin", "StrongAdminPass!", "ADMIN")
    with StatementLog(session) as small:
        await admin_csv(client)
    await session.execute(
        insert(Activity),
        [
            {
                "activity_number": f"ACT-BULK-{i:05d}", "alc_id": world["alc_b"].id,
                "activity_type": "Bulk", "ecosystem": "College",
                "activity_date": date(2024, 1 + i % 12, 1 + i % 28), "location": "Pune",
                "description": "Bulk row", "outcome": "Done",
                "status": ActivityStatus.SUBMITTED, "submitted_at": utc(1),
            }
            for i in range(300)
        ],
    )
    await session.commit()
    with StatementLog(session) as large:
        assert len(rows_of((await admin_csv(client)).text)) == 306
    assert len(large.statements) == len(small.statements)


async def test_export_streams_in_bounded_batches(client, session, world, monkeypatch):
    """The endpoint reads through ``AsyncSession.stream`` with ``yield_per``; with a batch
    of 2 rows, 6 rows arrive as 3 separate CSV chunks after the header."""
    monkeypatch.setattr(csv_export, "EXPORT_BATCH_SIZE", 2)
    calls = []
    original_stream = session.stream

    async def spy_stream(statement, *args, **kwargs):
        calls.append(statement.get_execution_options().get("yield_per"))
        return await original_stream(statement, *args, **kwargs)

    monkeypatch.setattr(session, "stream", spy_stream)
    chunks = []
    original_lines = csv_export.csv_lines

    def spy_lines(rows, **kwargs):
        text = original_lines(rows, **kwargs)
        chunks.append(text)
        return text

    monkeypatch.setattr(csv_export, "csv_lines", spy_lines)
    await as_user(client, "admin", "StrongAdminPass!", "ADMIN")
    chunks.clear()
    text = (await admin_csv(client)).text
    assert calls == [2]
    assert chunks[0] == ADMIN_HEADER + "\r\n"
    assert [c.count("\r\n") for c in chunks[1:]] == [2, 2, 2]
    assert "".join(chunks) == text
    assert numbers(text) == [
        "ACT-T-001", "ACT-T-002", "ACT-T-003", "ACT-T-005", "ACT-T-006", "ACT-T-007",
    ]


async def test_stream_yields_before_all_rows_are_read(session, world):
    processed = []

    def to_row(row):
        processed.append(row.activity_number)
        return list(row)

    query = select(Activity.activity_number).order_by(Activity.activity_number)
    total = await session.scalar(select(func.count(Activity.id)))
    stream = csv_export.stream_csv(session, ["Activity Number"], query, to_row, batch_size=2)
    assert await anext(stream) == "Activity Number\r\n"
    assert await anext(stream) == "ACT-T-001\r\nACT-T-002\r\n"
    assert len(processed) == 2 < total  # first chunk out while later rows are still unread
    rest = [chunk async for chunk in stream]
    assert len(rest) == math.ceil(total / 2) - 1
    assert len(processed) == total


async def test_request_session_stays_open_while_streaming(client, session, world, monkeypatch):
    """The DB session (a ``yield`` dependency) must not be torn down before the streamed body
    has been read from it."""
    state = {"closed": False, "open_when_streamed": None}

    async def tracked_db():
        state["closed"] = False
        try:
            yield session
        finally:
            state["closed"] = True

    original_stream = session.stream

    async def spy_stream(statement, *args, **kwargs):
        state["open_when_streamed"] = not state["closed"]
        return await original_stream(statement, *args, **kwargs)

    app.dependency_overrides[get_db] = tracked_db
    monkeypatch.setattr(session, "stream", spy_stream)
    await as_user(client, "dcu-x", PW)
    assert len(rows_of((await portal_csv(client)).text)) == 3
    assert state["open_when_streamed"] is True
    assert state["closed"] is True


async def test_large_export_succeeds(client, session, world):
    alc_ids = [world["alc_a"].id, world["alc_b"].id, world["alc_c"].id]
    await session.execute(
        insert(Activity),
        [
            {
                "activity_number": f"ACT-LARGE-{i:05d}", "alc_id": alc_ids[i % 3],
                "activity_type": "Bulk", "ecosystem": "College",
                "activity_date": date(2024, 1 + i % 12, 1 + i % 28), "location": "Pune",
                "learners_reached": i % 50, "description": "Bulk row", "outcome": "Done",
                "status": ActivityStatus.VERIFIED if i % 2 else ActivityStatus.DRAFT,
                "submitted_at": None if i % 2 == 0 else utc(1 + i % 20),
            }
            for i in range(4500)
        ],
    )
    await session.commit()
    await as_user(client, "admin", "StrongAdminPass!", "ADMIN")
    with ActivityLoads() as loads:
        rows = rows_of((await admin_csv(client)).text)
    assert loads.count == 0
    assert len(rows) == 6 + 2250  # drafts excluded
    dates = [r["Date"] for r in rows]
    assert dates == sorted(dates, reverse=True)

    await as_user(client, "dcu-x", PW)
    rows = rows_of((await portal_csv(client)).text)
    # DCU X: Centres A and C only; half of the bulk rows are drafts.
    bulk = [r for r in rows if r["Activity Number"].startswith("ACT-LARGE-")]
    assert len(bulk) == sum(1 for i in range(4500) if i % 2 and i % 3 != 1)
    assert {r["ALC Code"] for r in rows} == {"00010001", "00010003"}


async def test_export_leaves_historical_records_untouched(client, session, world):
    first = world["acts"]["ACT-T-001"]

    async def snapshot():
        evidence = (
            await session.execute(
                select(ActivityEvidence.original_filename, ActivityEvidence.is_active)
                .where(ActivityEvidence.activity_id == first.id)
                .order_by(ActivityEvidence.original_filename)
            )
        ).all()
        reviews = await session.scalar(
            select(func.count(ActivityReview.id)).where(ActivityReview.activity_id == first.id)
        )
        revisions = (
            await session.execute(
                select(ActivityRevision.revision_number, ActivityRevision.snapshot)
                .where(ActivityRevision.activity_id == first.id)
            )
        ).all()
        activity = (
            await session.execute(
                select(Activity.status, Activity.updated_at).where(Activity.id == first.id)
            )
        ).one()
        return evidence, reviews, revisions, activity

    before = await snapshot()
    await as_user(client, "admin", "StrongAdminPass!", "ADMIN")
    await admin_csv(client)
    await as_user(client, "dcu-x", PW)
    await portal_csv(client)
    assert await snapshot() == before
    assert before[0] == [("live.pdf", True), ("removed.pdf", False)]
    # The full activity view still carries its evidence / review / revision history.
    detail = (await client.get(f"/api/portal/activities/{first.id}")).json()
    assert [e["original_filename"] for e in detail["evidence"]] == ["live.pdf"]
    assert [e["original_filename"] for e in detail["removed_evidence"]] == ["removed.pdf"]
    assert len(detail["reviews"]) == 1 and len(detail["revisions"]) == 1
