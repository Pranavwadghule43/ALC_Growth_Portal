"""Published Growth Challenge hierarchy tracking and direct-request isolation."""
import uuid
from datetime import date, timedelta

import pytest

from app.enums import ActivityStatus
from app.models import Activity, ActivityEvidence, GrowthChallenge
from tests.conftest import login
from tests.test_dcu_hierarchy import NASHIK_ALC_SBU4, PW, hier  # noqa: F401

TODAY = date.today()


def logout(client):
    client.cookies.clear()
    client.headers.pop("X-CSRF-Token", None)


async def as_user(client, identifier, password=PW, role="DCU"):
    logout(client)
    response = await login(client, identifier, password, role)
    assert response.status_code == 200, response.text


async def configured(session, *, published=True, targets=1):
    challenge = GrowthChallenge(
        name="Hierarchy Challenge",
        description="Verified local outreach",
        is_published=published,
        start_date=TODAY - timedelta(days=5),
        end_date=TODAY + timedelta(days=5),
        prospects_target=targets,
        meetings_target=targets,
        pilots_target=targets,
        partnerships_target=targets,
    )
    session.add(challenge)
    await session.commit()
    return challenge


@pytest.mark.asyncio
async def test_only_published_challenges_are_visible(client, session, hier):  # noqa: F811
    visible = await configured(session, published=True)
    await configured(session, published=False)
    await as_user(client, "dcu-nashik")
    response = await client.get("/api/portal/growth-challenges")
    assert response.status_code == 200
    assert [row["id"] for row in response.json()["items"]] == [str(visible.id)]


@pytest.mark.asyncio
async def test_dcu_counts_and_sbu_breakdown_are_limited_to_current_hierarchy(
    client, session, hier  # noqa: F811
):
    challenge = await configured(session, targets=1)
    nashik_alc = hier["alc4_centre"]
    session.add(
        Activity(
            activity_number="GC-HIER-1",
            alc_id=nashik_alc.id,
            activity_type="Partnership meeting pilot",
            ecosystem="College",
            activity_date=TODAY,
            location="Nashik",
            learners_reached=1,
            leads_generated=1,
            admissions_generated=0,
            description="Challenge work",
            outcome="Completed",
            status=ActivityStatus.VERIFIED,
            partner_id=None,
        )
    )
    await session.commit()

    await as_user(client, "dcu-nashik")
    nashik = (await client.get(
        f"/api/portal/growth-challenges/{challenge.id}/progress?page_size=100"
    )).json()
    assert nashik["summary"]["eligible"] == sum(
        row["eligible"] for row in nashik["sbu_breakdown"]
    )
    assert {row["dcu_code"] for row in nashik["items"]} == {"DCU_NASHIK"}
    assert {row["sbu_code"] for row in nashik["sbu_breakdown"]} == {"SBU 4", "SBU 6", "SBU 7"}

    await as_user(client, "dcu-pn")
    pune = (await client.get(
        f"/api/portal/growth-challenges/{challenge.id}/progress?page_size=100"
    )).json()
    assert {row["dcu_code"] for row in pune["items"]} == {"DCU_PUNE_NORTH"}
    assert {row["sbu_code"] for row in pune["sbu_breakdown"]} == {"TEST PN"}
    bypass = await client.get(
        f"/api/portal/growth-challenges/{challenge.id}/progress",
        params={"sbu_id": str(hier["sbu7"].id)},
    )
    assert bypass.status_code == 404


@pytest.mark.asyncio
async def test_sbu_and_alc_direct_filters_cannot_cross_scope(client, session, hier):  # noqa: F811
    challenge = await configured(session)
    await as_user(client, "sbu-7", role="SBU")
    own = (await client.get(
        f"/api/portal/growth-challenges/{challenge.id}/progress?page_size=100"
    )).json()
    assert {row["sbu_code"] for row in own["items"]} <= {"SBU 7"}
    outside = await client.get(
        f"/api/portal/growth-challenges/{challenge.id}/progress",
        params={"alc_id": str(hier["alc4_centre"].id)},
    )
    assert outside.status_code == 404

    await as_user(client, NASHIK_ALC_SBU4, role="ALC")
    mine = (await client.get(
        f"/api/portal/growth-challenges/{challenge.id}/progress?page_size=100"
    )).json()
    assert [row["id"] for row in mine["items"]] == [str(hier["alc4_centre"].id)]
    other = await client.get(
        f"/api/portal/growth-challenges/{challenge.id}/alcs/{hier['alc7_centre'].id}/evidence"
    )
    assert other.status_code == 404


@pytest.mark.asyncio
async def test_evidence_listing_is_verified_windowed_and_scope_checked(
    client, session, hier  # noqa: F811
):
    challenge = await configured(session)
    activity = Activity(
        activity_number="GC-EVIDENCE-1",
        alc_id=hier["alc4_centre"].id,
        activity_type="Meeting",
        ecosystem="College",
        activity_date=TODAY,
        location="Nashik",
        learners_reached=1,
        leads_generated=1,
        admissions_generated=0,
        description="Challenge evidence",
        outcome="Verified",
        status=ActivityStatus.VERIFIED,
    )
    session.add(activity)
    await session.flush()
    evidence = ActivityEvidence(
        activity_id=activity.id,
        storage_key=f"test/{uuid.uuid4()}.pdf",
        original_filename="proof.pdf",
        mime_type="application/pdf",
        file_size=20,
    )
    session.add(evidence)
    await session.commit()

    await as_user(client, "dcu-nashik")
    response = await client.get(
        f"/api/portal/growth-challenges/{challenge.id}/alcs/{hier['alc4_centre'].id}/evidence"
    )
    assert response.status_code == 200
    [item] = response.json()["items"]
    assert item["activity_number"] == "GC-EVIDENCE-1"
    assert item["evidence"][0]["id"] == str(evidence.id)

    await as_user(client, "dcu-pn")
    assert (await client.get(
        f"/api/portal/growth-challenges/{challenge.id}/alcs/{hier['alc4_centre'].id}/evidence"
    )).status_code == 404


@pytest.mark.asyncio
async def test_admin_overview_still_sees_all_alcs(client, session, seeded):
    challenge = await configured(session)
    logout(client)
    assert (await login(client, "admin", "StrongAdminPass!", "ADMIN")).status_code == 200
    response = await client.get("/api/admin/challenge?page_size=100")
    assert response.status_code == 200
    assert response.json()["total"] == 3
    tracking = await client.get(f"/api/admin/growth-challenges/{challenge.id}/progress")
    assert tracking.status_code == 200
    assert tracking.json()["summary"]["eligible"] == 3
