"""Read-only HTTP requests release the shared auth/route transaction before encoding."""

import uuid

import pytest
from sqlalchemy import event
from starlette.responses import Response

from app.database import release_read_only_transaction
from app.routes import yearly_activities
from tests.conftest import login


async def assert_request_releases_connection(client, session, path):
    sync_session = session.sync_session
    engine = session.get_bind()
    pool = engine.pool
    state = {"committed": False, "ended": False, "sql_after_end": [], "checkins": 0}

    def after_commit(_session):
        state["committed"] = True

    def after_transaction_end(_session, transaction):
        if transaction.parent is None and state["committed"]:
            state["ended"] = True

    def after_begin(_session, _transaction, _connection):
        # Cached endpoints intentionally close the authentication transaction, then open
        # a short builder transaction on a cache miss. Only SQL outside an active phase is
        # a regression; SQL in the next explicitly bounded phase is expected.
        state["ended"] = False

    def before_cursor_execute(_connection, _cursor, statement, _parameters, _context, _many):
        if state["ended"]:
            state["sql_after_end"].append(statement)

    def on_checkin(_dbapi_connection, _connection_record):
        state["checkins"] += 1

    starting_checkins = state["checkins"]
    event.listen(sync_session, "after_commit", after_commit)
    event.listen(sync_session, "after_begin", after_begin)
    event.listen(sync_session, "after_transaction_end", after_transaction_end)
    event.listen(engine, "before_cursor_execute", before_cursor_execute)
    event.listen(pool, "checkin", on_checkin)
    try:
        response = await client.get(path)
    finally:
        event.remove(sync_session, "after_commit", after_commit)
        event.remove(sync_session, "after_begin", after_begin)
        event.remove(sync_session, "after_transaction_end", after_transaction_end)
        event.remove(engine, "before_cursor_execute", before_cursor_execute)
        event.remove(pool, "checkin", on_checkin)

    assert response.status_code == 200
    assert state["committed"] and state["ended"]
    assert state["checkins"] > starting_checkins
    assert state["sql_after_end"] == []
    assert not session.in_transaction()


async def test_admin_read_endpoints_release_connection_before_encoding(client, session, seeded):
    assert (await login(client, "admin", "StrongAdminPass!", "ADMIN")).status_code == 200
    for path in (
        "/api/admin/dashboard",
        "/api/yearly-activities/definitions",
        "/api/yearly-activities/submissions",
        "/api/leaderboard/region-scores",
        "/api/leaderboard/region-top10",
    ):
        await assert_request_releases_connection(client, session, path)


async def test_portal_dashboards_release_connection_for_alc_and_sbu(client, session, seeded):
    assert (await login(client, "00010001", "StrongAlcPassA!", "PORTAL")).status_code == 200
    await assert_request_releases_connection(client, session, "/api/portal/dashboard")

    client.cookies.clear()
    client.headers.pop("X-CSRF-Token", None)
    assert (await login(client, "sbu-4", "StrongSbuPass4!", "PORTAL")).status_code == 200
    await assert_request_releases_connection(client, session, "/api/portal/dashboard")


async def test_remaining_yearly_reads_release_before_response_or_storage(
    client, session, seeded, monkeypatch
):
    assert (await login(client, "admin", "StrongAdminPass!", "ADMIN")).status_code == 200
    run_id, submission_id, evidence_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    async def materialized_payload(*_args, **_kwargs):
        return {"items": [], "total": 0}

    async def scoped_evidence(*_args, **_kwargs):
        return object()

    async def evidence_response(_evidence, range_header=None):
        assert not session.in_transaction()
        return Response(content=b"materialized")

    monkeypatch.setattr(yearly_activities.service, "list_completions", materialized_payload)
    monkeypatch.setattr(yearly_activities.submissions, "detail", materialized_payload)
    monkeypatch.setattr(yearly_activities.submissions, "scoped_evidence", scoped_evidence)
    monkeypatch.setattr(yearly_activities, "evidence_response", evidence_response)

    for path in (
        f"/api/yearly-activities/runs/{run_id}/completions",
        f"/api/yearly-activities/submissions/{submission_id}",
        f"/api/yearly-activities/evidence/{evidence_id}/access",
        f"/api/yearly-activities/evidence/{evidence_id}/content",
    ):
        await assert_request_releases_connection(client, session, path)


async def test_read_only_boundary_refuses_pending_changes(session):
    from app.models import User

    session.add(User(username="pending", password_hash="not-used", role="ADMIN"))
    with pytest.raises(RuntimeError, match="pending ORM changes"):
        await release_read_only_transaction(session)
    await session.rollback()
