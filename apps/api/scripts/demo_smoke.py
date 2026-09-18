"""Exercise the live local demo workflow and leave one verified example activity."""

import asyncio
from datetime import date

import httpx
from dotenv import dotenv_values


def sample_pdf() -> bytes:
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length 56 >>\nstream\nBT /F1 18 Tf 72 760 Td (ALC demo evidence) Tj ET\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    pdf = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf.extend(f"{index} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = len(pdf)
    pdf.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        pdf.extend(f"{offset:010d} 00000 n \n".encode())
    pdf.extend(f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return bytes(pdf)


async def login(client: httpx.AsyncClient, identifier: str, password: str, portal: str) -> None:
    response = await client.post("/api/auth/login", json={"identifier": identifier, "password": password, "portal": portal})
    response.raise_for_status()
    client.headers["X-CSRF-Token"] = client.cookies["csrf_token"]


async def main() -> None:
    config = dotenv_values("../../.env")
    async with httpx.AsyncClient(base_url="http://localhost:8000", timeout=20) as alc, httpx.AsyncClient(base_url="http://localhost:8000", timeout=20) as admin, httpx.AsyncClient(base_url="http://localhost:8000", timeout=20) as other:
        await login(alc, "57210164", str(config["DEV_ALC_PASSWORD"]), "ALC")
        await login(admin, "admin", str(config["DEV_ADMIN_PASSWORD"]), "ADMIN")
        await login(other, "57210165", str(config["DEV_ALC_PASSWORD"]), "ALC")
        payload = {
            "activity_type": "Partner meeting",
            "ecosystem": "College",
            "activity_date": date.today().isoformat(),
            "location": "Local demo",
            "learners_reached": 24,
            "leads_generated": 8,
            "admissions_generated": 2,
            "description": "Demo collaboration meeting with evidence and an admin review cycle.",
            "outcome": "Pilot discussion agreed",
        }
        created = await alc.post("/api/alc/activities", json=payload)
        created.raise_for_status()
        activity = created.json()
        activity_id = activity["id"]
        upload = await alc.post(f"/api/alc/activities/{activity_id}/evidence", files={"files": ("demo-proof.pdf", sample_pdf(), "application/pdf")})
        upload.raise_for_status()
        evidence_id = upload.json()[0]["id"]
        own_access = await alc.get(f"/api/alc/evidence/{evidence_id}/access")
        own_access.raise_for_status()
        content = await alc.get(own_access.json()["url"])
        assert content.status_code == 200 and content.content.startswith(b"%PDF-")
        assert (await other.get(f"/api/alc/activities/{activity_id}")).status_code == 404
        assert (await other.get(f"/api/alc/evidence/{evidence_id}/content")).status_code == 404
        submitted = await alc.post(f"/api/alc/activities/{activity_id}/submit")
        submitted.raise_for_status()
        assert submitted.json()["status"] == "SUBMITTED"
        queue = await admin.get("/api/admin/verification-queue?queue_only=true")
        queue.raise_for_status()
        assert any(item["activity"]["id"] == activity_id for item in queue.json()["items"])
        correction = await admin.post(f"/api/admin/activities/{activity_id}/request-correction", json={"remark": "Please clarify the pilot outcome"})
        correction.raise_for_status()
        assert correction.json()["status"] == "CORRECTION_REQUIRED"
        edited = await alc.patch(f"/api/alc/activities/{activity_id}", json={**payload, "outcome": "Pilot scheduled and confirmed"})
        edited.raise_for_status()
        resubmitted = await alc.post(f"/api/alc/activities/{activity_id}/submit")
        resubmitted.raise_for_status()
        assert resubmitted.json()["status"] == "RESUBMITTED"
        verified = await admin.post(f"/api/admin/activities/{activity_id}/verify", json={"remark": "Evidence accepted"})
        verified.raise_for_status()
        dashboard = await alc.get("/api/alc/dashboard")
        dashboard.raise_for_status()
        assert dashboard.json()["learners"] >= 24
        print(f"Live demo verified: {activity['activity_number']} with private PDF, correction, resubmission, and verified metrics")


if __name__ == "__main__":
    asyncio.run(main())
