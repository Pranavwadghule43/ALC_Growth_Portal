"""Admin -> Users server-side search, role/status filters and pagination.

Builds on ``conftest.seeded``: admin, ALC logins ``alc-a`` / ``alc-b`` / ``alc-c`` (Centres
A/B/C, codes 00010001-3), SBU logins ``sbu-4`` / ``sbu-6``. Adds a DCU login under DCU Nashik
and an email on ``sbu-6``."""

import pytest
from sqlalchemy import func, select

from app.auth import hash_password
from app.enums import Role
from app.models import DCU, User
from app.services.hierarchy import ensure_hierarchy
from tests.conftest import login

DCU_PASSWORD = "StrongDcuPass123!"


@pytest.fixture
async def directory(client, session, seeded):
    await ensure_hierarchy(session)
    nashik = await session.scalar(select(DCU).where(DCU.code == "DCU_NASHIK"))
    session.add(
        User(
            username="dcu-nashik",
            password_hash=hash_password(DCU_PASSWORD),
            role=Role.DCU,
            dcu_id=nashik.id,
        )
    )
    seeded["sbu6_user"].email = "Ops.Six@Example.org"
    await session.commit()
    await login(client, "admin", "StrongAdminPass!", "ADMIN")
    return client


async def search(client, **params):
    response = await client.get("/api/admin/users", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def names(body) -> list[str]:
    return [u["username"] for u in body["items"]]


# 1. username ------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_search_by_username(directory):
    body = await search(directory, q="alc-b")
    assert names(body) == ["alc-b"] and body["total"] == 1


# 2. email ---------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_search_by_email(directory):
    assert names(await search(directory, q="ops.six@example")) == ["sbu-6"]


# 3. ALC Code (and ALC name) ---------------------------------------------- #
@pytest.mark.asyncio
async def test_search_by_alc_code_and_name(directory):
    body = await search(directory, q="00010002")
    assert names(body) == ["alc-b"]
    assert body["items"][0]["alc"]["alc_code"] == "00010002"
    assert body["items"][0]["alc"]["sbu"]["code"] == "SBU 6"  # nested serialization intact
    assert names(await search(directory, q="Centre C")) == ["alc-c"]


@pytest.mark.asyncio
async def test_search_by_sbu_and_dcu(directory):
    assert names(await search(directory, q="Strategic Business Unit 4")) == ["sbu-4"]
    assert names(await search(directory, q="SBU 6")) == ["sbu-6"]
    assert names(await search(directory, q="DCU Nashik")) == ["dcu-nashik"]
    assert names(await search(directory, q="dcu_nashik")) == ["dcu-nashik"]  # DCU code


# 4. case-insensitive ----------------------------------------------------- #
@pytest.mark.asyncio
async def test_search_is_case_insensitive(directory):
    assert names(await search(directory, q="ALC-A")) == ["alc-a"]
    assert names(await search(directory, q="centre a")) == ["alc-a"]
    assert names(await search(directory, q="OPS.SIX")) == ["sbu-6"]


# 5. partial match and trimming ------------------------------------------- #
@pytest.mark.asyncio
async def test_partial_and_trimmed_search(directory):
    assert names(await search(directory, q="0001000")) == ["alc-a", "alc-b", "alc-c"]
    assert names(await search(directory, q="  alc-c  ")) == ["alc-c"]


# 6. no match ------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_no_match_returns_empty_page(directory):
    body = await search(directory, q="zzz-nobody")
    assert body == {"items": [], "page": 1, "page_size": 25, "total": 0, "pages": 0}


# 7. search + role filter ------------------------------------------------- #
@pytest.mark.asyncio
async def test_search_with_role_filter(directory):
    assert names(await search(directory, q="sbu")) == ["sbu-4", "sbu-6"]
    assert names(await search(directory, q="sbu", role="SBU")) == ["sbu-4", "sbu-6"]
    assert names(await search(directory, q="sbu", role="ALC")) == []
    assert names(await search(directory, q="centre", role="ALC")) == ["alc-a", "alc-b", "alc-c"]
    assert names(await search(directory, role="DCU")) == ["dcu-nashik"]
    bad = await directory.get("/api/admin/users", params={"role": "ROOT"})
    assert bad.status_code == 422


# 8. search + active/status filter ---------------------------------------- #
@pytest.mark.asyncio
async def test_search_with_status_filter(directory, session, seeded):
    seeded["c"].is_active = False
    await session.commit()
    assert names(await search(directory, q="centre", status="inactive")) == ["alc-c"]
    assert names(await search(directory, q="centre", status="active")) == ["alc-a", "alc-b"]
    assert names(await search(directory, q="centre", role="ALC", status="inactive")) == ["alc-c"]
    bad = await directory.get("/api/admin/users", params={"status": "deleted"})
    assert bad.status_code == 422


# 9. pagination over the filtered set ------------------------------------- #
@pytest.mark.asyncio
async def test_pagination_counts_reflect_search(directory, session):
    session.add_all(
        User(username=f"pager-{n:02d}", password_hash="x", role=Role.ADMIN) for n in range(30)
    )
    await session.commit()
    all_users = await session.scalar(select(func.count(User.id)))

    first = await search(directory, q="PAGER", page=1, page_size=10)
    assert (first["total"], first["pages"], first["page"]) == (30, 3, 1)
    seen = []
    for page in (1, 2, 3):
        body = await search(directory, q="pager", page=page, page_size=10)
        assert len(body["items"]) == 10 and body["total"] == 30
        seen += names(body)
    assert seen == [f"pager-{n:02d}" for n in range(30)]  # ordered, no gaps or repeats
    beyond = await search(directory, q="pager", page=4, page_size=10)
    assert beyond["items"] == [] and beyond["total"] == 30

    unfiltered = await search(directory, page_size=10)
    assert unfiltered["total"] == all_users and unfiltered["pages"] == -(-all_users // 10)


@pytest.mark.asyncio
async def test_multi_field_matches_return_each_user_once(directory):
    # "a" hits usernames, ALC codes/names, SBU names and DCU names at once.
    body = await search(directory, q="a", page_size=100)
    ids = [u["id"] for u in body["items"]]
    assert len(ids) == len(set(ids)) == body["total"]


# 10. authorization -------------------------------------------------------- #
@pytest.mark.asyncio
async def test_non_admin_cannot_search_users(directory):
    for identifier, password, portal in (
        ("sbu-4", "StrongSbuPass4!", "SBU"),
        ("00010001", "StrongAlcPassA!", "ALC"),
        ("dcu-nashik", DCU_PASSWORD, "DCU"),
    ):
        directory.cookies.clear()
        assert (await login(directory, identifier, password, portal)).status_code == 200
        response = await directory.get("/api/admin/users", params={"q": "alc"})
        assert response.status_code == 403, identifier
    directory.cookies.clear()
    assert (await directory.get("/api/admin/users", params={"q": "alc"})).status_code == 401


# 11. special characters ---------------------------------------------------- #
@pytest.mark.asyncio
async def test_special_characters_are_literal_and_safe(directory, session):
    session.add(User(username="under_score", password_hash="x", role=Role.ADMIN))
    session.add(User(username="fifty%off", password_hash="x", role=Role.ADMIN))
    await session.commit()
    assert names(await search(directory, q="%")) == ["fifty%off"]  # not "match everything"
    # "_" is literal, not "any character": only the username and the DCU code DCU_NASHIK
    # actually contain an underscore.
    assert names(await search(directory, q="_")) == ["dcu-nashik", "under_score"]
    assert names(await search(directory, q="50%")) == []
    for q in ("'; DROP TABLE users; --", "\\", "\\%", '"quoted"', "a'b", "(", "*", "[a-z]"):
        body = await search(directory, q=q)
        assert body["total"] == len(body["items"])
    assert await session.scalar(select(func.count(User.id))) > 0  # table intact
    too_long = await directory.get("/api/admin/users", params={"q": "x" * 101})
    assert too_long.status_code == 422


# 12. blank / whitespace q -------------------------------------------------- #
@pytest.mark.asyncio
async def test_blank_query_behaves_like_no_search(directory):
    baseline = await search(directory, page_size=100)
    for q in ("", "   ", "\t"):
        assert await search(directory, q=q, page_size=100) == baseline
