"""Phase 2 — DCU operational portal: dashboard aggregates, SBU/ALC directories, activity and
verification filtering, normal and changed review decisions with preserved history, partner
directory, scoped reports/CSVs, draft privacy, and cross-DCU denial on every new endpoint.

Built on the ``hier`` fixture from ``test_dcu_hierarchy``: DCU Nashik owns SBU 4 / 6 / 7 with
the real 199-ALC master (71 / 58 / 70); DCU Pune North owns the test SBU ``TEST PN`` with
conftest's Centres A, B and C; the demo SBU 1 has no DCU.
"""
import csv
import io
import uuid

from sqlalchemy import func, select

from app.enums import AlcStatus
from app.models import Activity, ActivityEvidence, ActivityReview, Partner
from app.services import alc_import
from tests import test_dcu_hierarchy
from tests.conftest import login
from tests.test_dcu_hierarchy import (
    MASTER,
    NASHIK_ALC_SBU4,
    NASHIK_ALC_SBU7,
    PW,
    as_user,
    logout,
    payload,
    submit_activity,
)

# Re-export the Phase 1 ``hier`` fixture (DCU Nashik: SBU 4/6/7 with the 199-ALC master;
# DCU Pune North: TEST PN with Centres A/B/C) so this module's tests can request it.
hier = test_dcu_hierarchy.hier

ADMIN = ("admin", "StrongAdminPass!", "ADMIN")
PN_ALC = ("00010002", "StrongAlcPassB!")  # Centre B, DCU Pune North


def rows_of(text: str) -> list[dict]:
    return list(csv.DictReader(io.StringIO(text)))


async def decide(client, activity_id, action, remark=None):
    return await client.post(
        f"/api/portal/activities/{activity_id}/{action}", json={"remark": remark}
    )


async def change(client, activity_id, decision, remark="Re-reviewed", base="portal"):
    return await client.post(
        f"/api/{base}/activities/{activity_id}/change-decision",
        json={"decision": decision, "remark": remark},
    )


async def verified_activity(client, session, identifier=NASHIK_ALC_SBU4):
    """An activity submitted by a Nashik ALC and verified by the Nashik DCU."""
    activity, evidence, partner = await submit_activity(client, session, identifier)
    await as_user(client, "dcu-nashik")
    assert (await decide(client, activity["id"], "verify")).status_code == 200
    return activity, evidence, partner


# --------------------------------------------------------------------------- #
# A. Dashboard
# --------------------------------------------------------------------------- #
async def test_dcu_dashboard_identity_totals_and_sbu_breakdown(client, session, hier):
    a4, _, _ = await submit_activity(client, session, NASHIK_ALC_SBU4, with_partner=True)
    a7, _, _ = await submit_activity(client, session, NASHIK_ALC_SBU7)
    await submit_activity(client, session, *PN_ALC)  # another DCU: must not be counted
    hier["alc7_centre"].status = AlcStatus.INACTIVE
    await session.commit()

    await as_user(client, "dcu-nashik")
    assert (await decide(client, a7["id"], "verify")).status_code == 200
    dash = (await client.get("/api/portal/dashboard")).json()
    assert dash["role"] == "DCU"
    assert dash["unit"] == {
        "type": "DCU", "id": str(hier["dcus"]["DCU_NASHIK"].id),
        "code": "DCU_NASHIK", "name": "DCU Nashik",
    }
    assert (dash["sbus"], dash["assigned_alcs"]) == (3, 199)
    assert (dash["active_alcs"], dash["inactive_alcs"]) == (198, 1)
    assert dash["partners"] == 1
    assert (dash["activities"], dash["submitted"]) == (2, 1)
    assert (dash["pending"], dash["verified"]) == (1, 1)
    assert (dash["corrections"], dash["resubmitted"], dash["rejected"]) == (0, 0, 0)
    assert (dash["learners"], dash["leads"], dash["admissions"]) == (25, 10, 2)

    breakdown = {row["code"]: row for row in dash["sbu_breakdown"]}
    assert list(breakdown) == ["SBU 4", "SBU 6", "SBU 7"]
    assert [breakdown[c]["alcs"] for c in breakdown] == [71, 58, 70]
    assert sum(r["alcs"] for r in breakdown.values()) == 199
    assert (breakdown["SBU 4"]["pending"], breakdown["SBU 4"]["partners"]) == (1, 1)
    assert (breakdown["SBU 7"]["verified"], breakdown["SBU 7"]["active_alcs"]) == (1, 69)
    assert {r["alc"]["alc_code"] for r in dash["recent_activities"]} == {
        NASHIK_ALC_SBU4, NASHIK_ALC_SBU7,
    }
    assert {r["sbu_code"] for r in dash["recent_activities"]} == {"SBU 4", "SBU 7"}

    # The SBU dashboard keeps its own single-SBU view.
    await as_user(client, "sbu-4", "StrongSbuPass4!")
    sbu_dash = (await client.get("/api/portal/dashboard")).json()
    assert sbu_dash["unit"]["code"] == "SBU 4" and sbu_dash["assigned_alcs"] == 71
    assert [r["code"] for r in sbu_dash["sbu_breakdown"]] == ["SBU 4"]


# --------------------------------------------------------------------------- #
# B–C. SBU directory and detail; SBU → ALC mapping
# --------------------------------------------------------------------------- #
async def test_dcu_sbu_directory_and_detail_map_alcs_to_sbus(client, session, hier):
    await submit_activity(client, session, NASHIK_ALC_SBU4, with_partner=True)
    await as_user(client, "dcu-nashik")
    items = (await client.get("/api/portal/sbus")).json()["items"]
    assert [s["code"] for s in items] == ["SBU 4", "SBU 6", "SBU 7"]  # never SBU 1 / TEST PN
    sbu4 = items[0]
    assert sbu4["dcu"]["code"] == "DCU_NASHIK" and sbu4["is_active"] is True
    assert (sbu4["alcs"], sbu4["active_alcs"], sbu4["pending"], sbu4["partners"]) == (71, 71, 1, 1)
    assert sbu4["verified"] == 0

    master = {r.alc_code: r.sbu for r in alc_import.parse_source(MASTER.name, MASTER.read_bytes())}
    for sbu in items:
        detail = (await client.get(f"/api/portal/sbus/{sbu['id']}")).json()
        assert detail["sbu"]["dcu"]["name"] == "DCU Nashik"
        assert detail["stats"]["alcs"] == len(detail["alcs"]) == sbu["alcs"]
        # Every ALC listed under an SBU really belongs to it in the master file.
        assert {master[a["alc_code"]] for a in detail["alcs"]} == {sbu["code"]}
        paged = (await client.get(f"/api/portal/alcs?sbu_id={sbu['id']}&page_size=100")).json()
        assert paged["total"] == sbu["alcs"]
        assert {row["sbu_code"] for row in paged["items"]} == {sbu["code"]}
        assert {row["dcu_code"] for row in paged["items"]} == {"DCU_NASHIK"}
    assert [
        (await client.get(f"/api/portal/alcs?sbu_id={s['id']}")).json()["total"] for s in items
    ] == [71, 58, 70]
    assert (await client.get("/api/portal/alcs")).json()["total"] == 199
    inactive = (await client.get("/api/portal/alcs?status=INACTIVE")).json()
    assert inactive["total"] == 0
    by_code = (await client.get(f"/api/portal/alcs?search={NASHIK_ALC_SBU4}")).json()
    assert [r["alc_code"] for r in by_code["items"]] == [NASHIK_ALC_SBU4]
    assert by_code["items"][0]["pending"] == 1 and by_code["items"][0]["partners"] == 1
    by_name = (await client.get("/api/portal/alcs?search=jayesh")).json()
    assert NASHIK_ALC_SBU4 in {r["alc_code"] for r in by_name["items"]}

    # Pages never exceed the requested size (server-side paging, not a full dump).
    page = (await client.get("/api/portal/alcs?page=2&page_size=50")).json()
    assert len(page["items"]) == 50 and page["pages"] == 4

    options = (await client.get("/api/portal/lookups/alcs")).json()
    assert options["total"] == 199 and set(options["items"][0]) == {
        "id", "alc_code", "alc_name", "sbu_id", "sbu_code",
    }
    narrowed = (await client.get(f"/api/portal/lookups/alcs?sbu_id={sbu4['id']}")).json()
    assert narrowed["total"] == 71


async def test_dcu_alc_detail_summary_is_read_only(client, session, hier):
    activity, _, _ = await submit_activity(client, session, NASHIK_ALC_SBU4, with_partner=True)
    await as_user(client, "dcu-nashik")
    assert (
        await decide(client, activity["id"], "request-correction", "Add attendance sheet")
    ).status_code == 200
    detail = (await client.get(f"/api/portal/alcs/{hier['alc4_centre'].id}")).json()
    assert detail["alc"]["sbu"]["code"] == "SBU 4" and detail["alc"]["dcu"]["code"] == "DCU_NASHIK"
    assert detail["summary"]["activities"] == 1 and detail["summary"]["corrections"] == 1
    assert detail["summary"]["partners"] == 1
    assert [a["id"] for a in detail["correction_required"]] == [activity["id"]]
    assert detail["partners"][0]["activity_count"] == 1
    # No ALC master edit from the portal (admin-only route).
    assert (
        await client.patch(f"/api/admin/alcs/{hier['alc4_centre'].id}", json={"status": "INACTIVE"})
    ).status_code == 403


# --------------------------------------------------------------------------- #
# F–G. Activity monitoring and verification queue
# --------------------------------------------------------------------------- #
async def test_dcu_activity_and_queue_filtering(client, session, hier):
    a4, _, _ = await submit_activity(client, session, NASHIK_ALC_SBU4)
    a7, _, _ = await submit_activity(client, session, NASHIK_ALC_SBU7)
    done, _, _ = await verified_activity(client, session)
    await submit_activity(client, session, *PN_ALC)

    await as_user(client, "dcu-nashik")
    everything = (await client.get("/api/portal/verification?page_size=100")).json()
    assert {r["activity"]["id"] for r in everything["items"]} == {a4["id"], a7["id"], done["id"]}
    assert all(r["sbu"]["code"] in {"SBU 4", "SBU 7"} for r in everything["items"])

    sbu4 = (await client.get(f"/api/portal/verification?sbu_id={hier['sbu4'].id}")).json()
    assert {r["activity"]["id"] for r in sbu4["items"]} == {a4["id"], done["id"]}
    sbu7 = (await client.get(f"/api/portal/activities?sbu_id={hier['sbu7'].id}")).json()
    assert [a["id"] for a in sbu7["items"]] == [a7["id"]]
    by_alc = (
        await client.get(f"/api/portal/verification?alc_id={hier['alc7_centre'].id}")
    ).json()
    assert [r["activity"]["id"] for r in by_alc["items"]] == [a7["id"]]
    verified = (await client.get("/api/portal/verification?status=VERIFIED")).json()
    assert [r["activity"]["id"] for r in verified["items"]] == [done["id"]]

    queue = (await client.get("/api/portal/verification?queue_only=true")).json()
    assert {r["activity"]["id"] for r in queue["items"]} == {a4["id"], a7["id"]}
    assert all(r["activity"]["status"] in {"SUBMITTED", "RESUBMITTED"} for r in queue["items"])
    typed = (
        await client.get("/api/portal/verification?queue_only=true&activity_type=Nope")
    ).json()
    assert typed["total"] == 0


# --------------------------------------------------------------------------- #
# I. Normal decisions
# --------------------------------------------------------------------------- #
async def test_dcu_normal_decisions_and_remark_rules(client, session, hier):
    a1, _, _ = await submit_activity(client, session, NASHIK_ALC_SBU4)
    a2, _, _ = await submit_activity(client, session, NASHIK_ALC_SBU4)
    a3, _, _ = await submit_activity(client, session, NASHIK_ALC_SBU7)
    await as_user(client, "dcu-nashik")

    detail = (await client.get(f"/api/portal/verification/{a1['id']}")).json()
    assert detail["can_review"] is True and detail["can_change_decision"] is False
    assert detail["sbu"]["code"] == "SBU 4" and detail["dcu"]["code"] == "DCU_NASHIK"

    assert (await decide(client, a1["id"], "verify")).json()["status"] == "VERIFIED"  # optional
    assert (await decide(client, a2["id"], "request-correction")).status_code == 422
    assert (await decide(client, a2["id"], "request-correction", "  ")).status_code == 422
    corrected = await decide(client, a2["id"], "request-correction", "Photos missing")
    assert corrected.json()["status"] == "CORRECTION_REQUIRED"
    assert (await decide(client, a3["id"], "reject")).status_code == 422
    assert (await decide(client, a3["id"], "reject", "Duplicate")).json()["status"] == "REJECTED"
    # A decided activity is no longer in the normal review flow.
    assert (await decide(client, a1["id"], "verify")).status_code == 409

    review = (await client.get(f"/api/portal/verification/{a2['id']}")).json()["activity"]
    assert review["reviews"][-1]["reviewer_role"] == "DCU"
    assert review["reviews"][-1]["is_decision_change"] is False
    assert review["reviews"][-1]["remark"] == "Photos missing"


# --------------------------------------------------------------------------- #
# J–K. Change decision: history preserved, metrics follow current status
# --------------------------------------------------------------------------- #
async def test_dcu_changes_verified_to_rejected_and_back(client, session, hier):
    activity, _, _ = await submit_activity(client, session, NASHIK_ALC_SBU4)
    aid = activity["id"]
    await as_user(client, "sbu-4", "StrongSbuPass4!")
    assert (await decide(client, aid, "verify", "SBU ok")).status_code == 200

    await as_user(client, "dcu-nashik")
    before = (await client.get("/api/portal/dashboard")).json()
    assert (before["verified"], before["rejected"], before["learners"]) == (1, 0, 25)
    detail = (await client.get(f"/api/portal/verification/{aid}")).json()
    assert detail["can_change_decision"] is True and detail["can_review"] is False

    # A reason is always required, and the target must differ from the current decision.
    assert (await change(client, aid, "REJECT", remark="")).status_code == 422
    assert (await change(client, aid, "REJECT", remark="   ")).status_code == 422
    assert (await change(client, aid, "VERIFY", remark="   ")).status_code == 422
    assert (await change(client, aid, "VERIFY")).status_code == 409

    rejected = await change(client, aid, "REJECT", "Evidence belongs to another event")
    assert rejected.status_code == 200 and rejected.json()["status"] == "REJECTED"
    after = (await client.get("/api/portal/dashboard")).json()
    assert (after["verified"], after["rejected"], after["learners"]) == (0, 1, 0)

    reverified = await change(client, aid, "VERIFY", "Confirmed with the partner")
    assert reverified.json()["status"] == "VERIFIED"
    again = (await client.get("/api/portal/dashboard")).json()
    assert (again["verified"], again["rejected"], again["learners"]) == (1, 0, 25)

    history = reverified.json()["reviews"]
    assert [(r["reviewer_role"], r["previous_status"], r["new_status"], r["is_decision_change"])
            for r in history] == [
        ("SBU", "SUBMITTED", "VERIFIED", False),
        ("DCU", "VERIFIED", "REJECTED", True),
        ("DCU", "REJECTED", "VERIFIED", True),
    ]
    assert [r["remark"] for r in history] == [
        "SBU ok", "Evidence belongs to another event", "Confirmed with the partner",
    ]
    stored = await session.scalar(select(Activity).where(Activity.id == uuid.UUID(aid)))
    await session.refresh(stored)
    assert stored.verified_by == hier["dcu_nashik"].id

    # The SBU was told its decision changed; the ALC was notified too.
    await as_user(client, "sbu-4", "StrongSbuPass4!")
    notes = (await client.get("/api/portal/notifications")).json()
    assert sum(n["type"] == "DECISION_CHANGED" and n["entity_id"] == aid for n in notes) == 2


async def test_change_to_correction_then_resubmit_then_verify_keeps_full_history(
    client, session, hier
):
    activity, _, _ = await submit_activity(client, session, NASHIK_ALC_SBU4)
    aid = activity["id"]
    await as_user(client, "sbu-4", "StrongSbuPass4!")
    await decide(client, aid, "verify")

    await as_user(client, "dcu-nashik")
    changed = await change(client, aid, "REQUEST_CORRECTION", "Learner count unclear")
    assert changed.json()["status"] == "CORRECTION_REQUIRED"
    dash = (await client.get("/api/portal/dashboard")).json()
    assert (dash["verified"], dash["corrections"], dash["learners"]) == (0, 1, 0)
    # A correction is not a final decision; only the ALC can move it on.
    assert (await change(client, aid, "VERIFY")).status_code == 409

    await as_user(client, NASHIK_ALC_SBU4)
    assert (await client.post(f"/api/portal/activities/{aid}/submit")).json()["status"] == (
        "RESUBMITTED"
    )
    await as_user(client, "dcu-nashik")
    assert (await client.get("/api/portal/dashboard")).json()["pending"] == 1
    queue = (await client.get("/api/portal/verification?queue_only=true")).json()
    assert [r["activity"]["id"] for r in queue["items"]] == [aid]
    final = (await decide(client, aid, "verify", "Now complete")).json()
    assert final["status"] == "VERIFIED"
    trail = [(r["reviewer_role"], r["action"], r["is_decision_change"]) for r in final["reviews"]]
    assert trail == [
        ("SBU", "VERIFY", False),
        ("DCU", "REQUEST_CORRECTION", True),
        ("DCU", "VERIFY", False),
    ]
    assert [r["revision_number"] for r in final["revisions"]] == [1, 2]
    assert (await client.get("/api/portal/dashboard")).json()["verified"] == 1
    # All three decisions are separate persisted rows — nothing was overwritten.
    stored = await session.scalar(
        select(func.count(ActivityReview.id)).where(ActivityReview.activity_id == uuid.UUID(aid))
    )
    assert stored == 3


async def test_only_dcu_and_admin_may_change_decisions(client, session, hier):
    activity, _, _ = await verified_activity(client, session)
    aid = activity["id"]

    await as_user(client, "sbu-4", "StrongSbuPass4!")  # the owning SBU still cannot override
    assert (await change(client, aid, "REJECT")).status_code == 403
    detail = (await client.get(f"/api/portal/verification/{aid}")).json()
    assert detail["can_change_decision"] is False
    await as_user(client, NASHIK_ALC_SBU4)
    assert (await change(client, aid, "REJECT")).status_code == 403

    await as_user(client, "dcu-pn")  # another DCU: out of scope
    assert (await change(client, aid, "REJECT")).status_code == 404

    await as_user(client, *ADMIN)
    assert (await client.get(f"/api/admin/activities/{aid}")).json()["can_change_decision"] is True
    assert (await change(client, aid, "REJECT", remark="", base="admin")).status_code == 422
    resp = await change(client, aid, "REJECT", "Admin audit finding", base="admin")
    assert resp.status_code == 200 and resp.json()["status"] == "REJECTED"
    assert resp.json()["reviews"][-1]["reviewer_role"] == "ADMIN"
    assert resp.json()["reviews"][-1]["is_decision_change"] is True


# --------------------------------------------------------------------------- #
# L. Partners
# --------------------------------------------------------------------------- #
async def test_dcu_partner_directory_scoped_filtered_and_paginated(client, session, hier):
    _, _, p4 = await submit_activity(client, session, NASHIK_ALC_SBU4, with_partner=True)
    _, _, pn = await submit_activity(client, session, *PN_ALC, with_partner=True)
    session.add(Partner(alc_id=hier["alc7_centre"].id, partner_name="Sai Industries",
                        partner_type="Industry", ecosystem="Industry"))
    await session.commit()

    await as_user(client, "dcu-nashik")
    directory = (await client.get("/api/portal/partner-directory")).json()
    assert {p["partner_name"] for p in directory["items"]} == {"Nashik College", "Sai Industries"}
    assert pn["id"] not in {p["id"] for p in directory["items"]}
    assert directory["partner_types"] == ["College", "Industry"]
    row = next(p for p in directory["items"] if p["id"] == p4["id"])
    assert (row["sbu_code"], row["alc_code"], row["activity_count"]) == (
        "SBU 4", NASHIK_ALC_SBU4, 1,
    )
    for query, expected in (
        (f"sbu_id={hier['sbu7'].id}", {"Sai Industries"}),
        (f"alc_id={hier['alc4_centre'].id}", {"Nashik College"}),
        ("search=sai", {"Sai Industries"}),
        ("partner_type=College", {"Nashik College"}),
    ):
        got = (await client.get(f"/api/portal/partner-directory?{query}")).json()
        assert {p["partner_name"] for p in got["items"]} == expected, query
    one = (await client.get("/api/portal/partner-directory?page_size=1")).json()
    assert len(one["items"]) == 1 and one["total"] == 2 and one["pages"] == 2
    for query in (f"sbu_id={hier['test_pn'].id}", f"alc_id={hier['alc_b'].id}"):
        assert (await client.get(f"/api/portal/partner-directory?{query}")).status_code == 404


# --------------------------------------------------------------------------- #
# M. Reports and CSV exports
# --------------------------------------------------------------------------- #
async def test_dcu_reports_and_csv_exports_are_scoped(client, session, hier):
    a4, _, _ = await verified_activity(client, session)
    a7, _, _ = await submit_activity(client, session, NASHIK_ALC_SBU7, with_partner=True)
    pn, _, _ = await submit_activity(client, session, *PN_ALC, with_partner=True)

    await as_user(client, "dcu-nashik")
    summary = (await client.get("/api/portal/reports/sbu-summary")).json()
    assert [r["sbu_code"] for r in summary["items"]] == ["SBU 4", "SBU 6", "SBU 7"]
    assert summary["totals"]["alcs"] == 199
    assert (summary["totals"]["verified"], summary["totals"]["pending"]) == (1, 1)
    assert summary["totals"]["learners"] == 25
    only7 = (await client.get(f"/api/portal/reports/sbu-summary?sbu_id={hier['sbu7'].id}")).json()
    assert [r["sbu_code"] for r in only7["items"]] == ["SBU 7"]
    past = (await client.get("/api/portal/reports/sbu-summary?date_to=2000-01-01")).json()
    assert past["totals"]["activities"] == 0 and past["totals"]["alcs"] == 199

    nashik = {"SBU 4", "SBU 6", "SBU 7"}
    perf = rows_of((await client.get("/api/portal/reports/sbu-performance.csv")).text)
    assert [r["SBU Code"] for r in perf] == ["SBU 4", "SBU 6", "SBU 7"]
    assert perf[0]["Verified Learners"] == "25"
    acts = rows_of((await client.get("/api/portal/reports/activities.csv")).text)
    assert {r["Activity Number"] for r in acts} == {a4["activity_number"], a7["activity_number"]}
    assert {r["SBU"] for r in acts} <= nashik
    status = rows_of((await client.get("/api/portal/reports/verification-status.csv")).text)
    assert len(status) == 199 and {r["SBU"] for r in status} == nashik
    partners = rows_of((await client.get("/api/portal/reports/partners.csv")).text)
    assert {r["SBU"] for r in partners} == {"SBU 7"}
    by_sbu = rows_of(
        (await client.get(f"/api/portal/reports/activities.csv?sbu_id={hier['sbu4'].id}")).text
    )
    assert [r["Activity Number"] for r in by_sbu] == [a4["activity_number"]]
    for path in ("activities.csv", "partners.csv", "verification-status.csv",
                 "sbu-performance.csv"):
        text = (await client.get(f"/api/portal/reports/{path}")).text
        assert "TEST PN" not in text and "Centre B" not in text, path
        assert pn["activity_number"] not in text, path

    other = hier["test_pn"].id
    for path in ("reports/sbu-summary", "reports/sbu-performance.csv", "reports/activities.csv",
                 "reports/partners.csv", "reports/verification-status.csv"):
        assert (await client.get(f"/api/portal/{path}?sbu_id={other}")).status_code == 404, path
    assert (
        await client.get(f"/api/portal/reports/sbu-summary?alc_id={hier['alc_b'].id}")
    ).status_code == 404


# --------------------------------------------------------------------------- #
# Q. Draft privacy
# --------------------------------------------------------------------------- #
async def test_drafts_are_hidden_from_sbu_dcu_and_admin(client, session, hier):
    await as_user(client, NASHIK_ALC_SBU4)
    draft = (await client.post("/api/portal/activities", json=payload)).json()
    assert draft["status"] == "DRAFT"
    mine = (await client.get("/api/portal/activities")).json()
    assert [a["id"] for a in mine["items"]] == [draft["id"]]  # the ALC still sees its draft
    evidence_id = (
        await submit_activity(client, session, NASHIK_ALC_SBU4)  # plus one real submission
    )[1].id
    draft_evidence = ActivityEvidence(
        activity_id=uuid.UUID(draft["id"]), storage_key=f"private/{uuid.uuid4()}.pdf",
        original_filename="draft.pdf", mime_type="application/pdf", file_size=5,
    )
    session.add(draft_evidence)
    await session.commit()

    for who in (("dcu-nashik", PW), ("sbu-4", "StrongSbuPass4!")):
        await as_user(client, *who)
        lists = [
            (await client.get("/api/portal/activities?page_size=100")).json()["items"],
            [r["activity"] for r in
             (await client.get("/api/portal/verification?page_size=100")).json()["items"]],
            (await client.get(f"/api/portal/alcs/{hier['alc4_centre'].id}")).json()["activities"],
        ]
        for items in lists:
            assert draft["id"] not in {a["id"] for a in items}, who
            assert all(a["status"] != "DRAFT" for a in items), who
        assert (await client.get(f"/api/portal/activities/{draft['id']}")).status_code == 404
        assert (await client.get(f"/api/portal/verification/{draft['id']}")).status_code == 404
        assert (
            await client.get(f"/api/portal/evidence/{draft_evidence.id}/access")
        ).status_code == 404
        assert (await client.get(f"/api/portal/evidence/{evidence_id}/access")).status_code == 200
        dash = (await client.get("/api/portal/dashboard")).json()
        assert dash["activities"] == 1, who
        report = (await client.get("/api/portal/reports/activities.csv")).text
        assert draft["activity_number"] not in report and "DRAFT" not in report
        assert rows_of(
            (await client.get("/api/portal/reports/verification-status.csv")).text
        )  # still exports every ALC row
        directory = (await client.get(f"/api/portal/alcs?search={NASHIK_ALC_SBU4}")).json()
        assert directory["items"][0]["activities"] == 1

    await as_user(client, *ADMIN)
    assert (await client.get(f"/api/admin/activities/{draft['id']}")).status_code == 404
    admin_list = (await client.get("/api/admin/activities?page_size=100")).json()
    assert draft["id"] not in {r["activity"]["id"] for r in admin_list["items"]}
    assert draft["activity_number"] not in (
        await client.get("/api/admin/reports/activities.csv")
    ).text
    assert (
        await client.get(f"/api/admin/evidence/{draft_evidence.id}/access")
    ).status_code == 404


# --------------------------------------------------------------------------- #
# O. Admin DCU dropdown data
# --------------------------------------------------------------------------- #
async def test_admin_dcu_options_feed_the_create_user_form(client, hier):
    await as_user(client, *ADMIN)
    dcus = (await client.get("/api/admin/dcus")).json()["items"]
    assert [d["name"] for d in dcus] == [
        "DCU Ahilya Nagar", "DCU Nashik", "DCU Pune North", "DCU Pune South",
    ]
    assert all({"id", "code", "name"} <= set(d) for d in dcus)
    pune_south = next(d for d in dcus if d["code"] == "DCU_PUNE_SOUTH")
    created = await client.post(
        "/api/admin/users",
        json={"username": "dcu-ps", "role": "DCU", "dcu_id": pune_south["id"],
              "password": "BrandNewDcuPass123!"},
    )
    assert created.status_code == 201
    body = created.json()
    assert body["dcu"]["name"] == "DCU Pune South"
    assert (body["sbu_id"], body["alc_id"]) == (None, None)
    logout(client)
    assert (await login(client, "dcu-ps", "BrandNewDcuPass123!", "PORTAL")).status_code == 200


# --------------------------------------------------------------------------- #
# R. Cross-DCU denial on every new surface; SBU rules unchanged
# --------------------------------------------------------------------------- #
async def test_cross_dcu_denied_on_new_endpoints_and_sbu_rules_unchanged(client, session, hier):
    pn_activity, _, _ = await submit_activity(client, session, *PN_ALC)
    await as_user(client, "dcu-nashik")
    other_sbu, other_alc = hier["test_pn"].id, hier["alc_b"].id
    for path in (
        f"/api/portal/sbus/{other_sbu}",
        f"/api/portal/alcs/{other_alc}",
        f"/api/portal/alcs?sbu_id={other_sbu}",
        f"/api/portal/lookups/alcs?sbu_id={other_sbu}",
        f"/api/portal/partner-directory?sbu_id={other_sbu}",
        f"/api/portal/verification/{pn_activity['id']}",
        f"/api/portal/reports/sbu-summary?sbu_id={other_sbu}",
    ):
        assert (await client.get(path)).status_code == 404, path
    assert (await change(client, pn_activity["id"], "REJECT")).status_code == 404

    await as_user(client, "sbu-4", "StrongSbuPass4!")
    assert [s["code"] for s in (await client.get("/api/portal/sbus")).json()["items"]] == ["SBU 4"]
    assert (await client.get("/api/portal/lookups/alcs")).json()["total"] == 71
    assert (await client.get(f"/api/portal/sbus/{hier['sbu6'].id}")).status_code == 404
    assert (
        await client.get(f"/api/portal/partner-directory?sbu_id={hier['sbu6'].id}")
    ).status_code == 404
    assert (
        await client.get(f"/api/portal/reports/sbu-summary?sbu_id={hier['sbu7'].id}")
    ).status_code == 404

    await as_user(client, NASHIK_ALC_SBU4)
    for path in ("/api/portal/sbus", "/api/portal/lookups/alcs", "/api/portal/partner-directory",
                 "/api/portal/reports/sbu-summary"):
        assert (await client.get(path)).status_code == 403, path
