"""Synthetic-only setup/server for the local production-readiness rehearsal.

Run with apps/api/.venv/Scripts/python.exe; output/credentials stay under ignored .local.
Hard guards deliberately exclude the source database, port 5432 and port 55432.
This is test tooling, never an application startup or production seed command.
"""

import asyncio
import base64
import hashlib
import json
import os
import secrets
import subprocess
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOCAL = ROOT / ".local"
CONFIG = LOCAL / "master-runtime.json"
API = ROOT / "apps/api"
sys.path.insert(0, str(API))


def configure(restore=False):
    config = json.loads(CONFIG.read_text(encoding="utf-8-sig"))
    if restore:
        config.update(
            DATABASE_URL=config["RESTORE_DATABASE_URL"],
            S3_BUCKET=config["RESTORE_BUCKET"],
        )
    assert config["DATABASE_URL"].startswith(
        "postgresql+asyncpg://postgres@127.0.0.1:55489/"
    )
    assert config["DATABASE_URL"].split("/")[-1] in {
        "alc_master_test",
        "alc_master_restore_test",
    }
    assert config["S3_ENDPOINT_URL"] == "http://127.0.0.1:19090"
    assert config["S3_BUCKET"].startswith("alc-master-")
    os.environ.update({k: str(v) for k, v in config.items()})
    os.environ.update(
        APP_ENV="test",
        COOKIE_SECURE="false",
        STORAGE_BACKEND="s3",
        LOG_LEVEL="INFO",
        REDIS_URL="redis://127.0.0.1:56389/0",
        CORS_ORIGINS="http://127.0.0.1:18080",
        LOGIN_IP_LIMIT="100",
        ALC_DB_PERF_DIAGNOSTICS="1",
    )
    spool = LOCAL / "master-spool"
    spool.mkdir(exist_ok=True)
    os.environ.update(TEMP=str(spool), TMP=str(spool))
    # Only this isolated run raises the shared-NAT login budget; production defaults stay intact.
    return config


async def check_database_identity():
    """Read-only preflight for the exact disposable database and migration head."""
    from sqlalchemy import text
    from sqlalchemy.engine import make_url
    from sqlalchemy.ext.asyncio import create_async_engine

    url = make_url(os.environ["DATABASE_URL"])
    assert url.host == "127.0.0.1" and url.port == 55489
    assert url.database == "alc_master_test"
    engine = create_async_engine(os.environ["DATABASE_URL"], hide_parameters=True)
    try:
        async with engine.connect() as conn:
            transaction = await conn.begin()
            try:
                await conn.exec_driver_sql("SET TRANSACTION READ ONLY")
                database, role, revision = (
                    await conn.execute(
                        text(
                            "SELECT current_database(), current_user, "
                            "(SELECT version_num FROM alembic_version)"
                        )
                    )
                ).one()
                assert database == "alc_master_test"
                assert role == "postgres"
                assert revision == "20261104_0010"
                print(
                    json.dumps(
                        {
                            "host_port": 55489,
                            "database": database,
                            "migration": revision,
                            "read_only": True,
                        }
                    )
                )
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()


async def refresh_sessions():
    """Renew real persistent sessions for the synthetic steady-state load accounts."""
    from app.auth import create_access_token, new_csrf_token
    from app.models import User
    from app.services.sessions import new_session
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    accounts_path = LOCAL / "master-accounts.json"
    accounts = json.loads(accounts_path.read_text(encoding="utf-8-sig"))
    engine = create_async_engine(os.environ["DATABASE_URL"], hide_parameters=True)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with maker() as db:
            for account in accounts:
                user = await db.scalar(select(User).where(User.username == account["identifier"]))
                assert user is not None and user.role.value == account["role"]
                family, _ = await new_session(db, user.id)
                account["access_token"] = create_access_token(user.id, family.id)
                account["csrf_token"] = new_csrf_token()
            await db.commit()
        accounts_path.write_text(json.dumps(accounts), encoding="utf-8")
        print(json.dumps({"refreshed_sessions": len(accounts), "credentials_printed": False}))
    finally:
        await engine.dispose()


async def verify_synthetic_authentication():
    """Check one fresh session per role against the isolated API; print no credentials."""
    import httpx

    accounts = json.loads(
        (LOCAL / "master-accounts.json").read_text(encoding="utf-8-sig")
    )
    selected = {}
    for account in accounts:
        selected.setdefault(account["role"], account)
    assert set(selected) == {"ADMIN", "DCU", "SBU", "ALC"}
    results = []
    async with httpx.AsyncClient(base_url="http://127.0.0.1:18080", timeout=20) as client:
        for role, account in selected.items():
            path = "/api/admin/dashboard" if role == "ADMIN" else "/api/portal/dashboard"
            response = await client.get(
                path,
                cookies={
                    "access_token": account["access_token"],
                    "csrf_token": account["csrf_token"],
                },
            )
            results.append({"role": role, "status": response.status_code})
    assert all(result["status"] == 200 for result in results), results
    print(json.dumps({"authenticated_roles": results, "credentials_printed": False}))


def make_config():
    assert not CONFIG.exists(), (
        "Refuse to overwrite an existing rehearsal configuration"
    )
    storage = json.loads(
        (LOCAL / "master-isolated-storage.json").read_text(encoding="utf-8-sig")
    )
    config = dict(
        storage,
        DATABASE_URL="postgresql+asyncpg://postgres@127.0.0.1:55489/alc_master_test",
        SECRET_KEY=secrets.token_urlsafe(48),
        S3_ENDPOINT_URL="http://127.0.0.1:19090",
        S3_BUCKET="alc-master-evidence-test",
        RESTORE_BUCKET="alc-master-restored-test",
        RESTORE_DATABASE_URL="postgresql+asyncpg://postgres@127.0.0.1:55489/alc_master_restore_test",
    )
    CONFIG.write_text(json.dumps(config), encoding="utf-8")


async def prepare():
    from alembic import command
    from alembic.config import Config
    from app.auth import create_access_token, hash_password, new_csrf_token
    from app.models import (
        ALC,
        DCU,
        RCU,
        SBU,
        Activity,
        ActivityEvidence,
        Partner,
        User,
        YearlyActivityCompletion,
        YearlyActivityDCURun,
        YearlyActivityDefinition,
        YearlyActivityEvidence,
        YearlyActivityReview,
        YearlyActivitySubmission,
    )
    from app.services.sessions import new_session
    from app.storage import storage_service
    from sqlalchemy import func, select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(os.environ["DATABASE_URL"], hide_parameters=True)
    cfg = Config(str(API / "alembic.ini"))
    cfg.set_main_option("script_location", str(API / "alembic"))

    def migrate(conn):
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "head")

    async with engine.begin() as conn:
        await conn.run_sync(migrate)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db:
        assert await db.scalar(select(func.count()).select_from(User)) == 0, (
            "Empty synthetic DB required"
        )
        password = secrets.token_urlsafe(24)
        hashed = hash_password(password)
        # hash_password is synchronous in this repository; one valid test-only hash is reused.
        if asyncio.iscoroutine(hashed):
            hashed = await hashed
        rcu = RCU(code="DRILL", name="Synthetic rehearsal region")
        db.add(rcu)
        await db.flush()
        dcus = [
            DCU(code=f"DRILL-D{i}", name=f"Synthetic DCU {i}", rcu_id=rcu.id)
            for i in range(4)
        ]
        db.add_all(dcus)
        await db.flush()
        sbus = [
            SBU(code=f"DRILL-S{i}", name=f"Synthetic SBU {i}", dcu_id=dcus[i % 4].id)
            for i in range(16)
        ]
        db.add_all(sbus)
        await db.flush()
        today, now = (
            datetime.now(timezone(timedelta(hours=5, minutes=30))).date(),
            datetime.now(timezone.utc),
        )
        definition = YearlyActivityDefinition(
            rcu_id=rcu.id,
            calendar_year=today.year,
            activity_code="DRILL-PROOF",
            name="Synthetic evidence rehearsal",
            introduced_by_dcu_id=dcus[0].id,
        )
        db.add(definition)
        await db.flush()
        runs = [
            YearlyActivityDCURun(
                yearly_activity_id=definition.id,
                dcu_id=dcu.id,
                status="ACTIVE",
                scheduled_start_date=today - timedelta(days=1),
                scheduled_end_date=today + timedelta(days=2),
            )
            for dcu in dcus
        ]
        db.add_all(runs)
        await db.flush()
        accounts = []
        account_refs = []

        def account(user, index):
            db.add(user)
            item = {
                "identifier": user.username,
                "password": password,
                "role": user.role,
                "run_id": str(runs[index % 4].id),
            }
            accounts.append(item)
            account_refs.append((user, item))
            return item

        account(User(username="drill-admin", role="ADMIN", password_hash=hashed), 0)
        dcu_accounts = [
            account(
                User(
                    username=f"drill-dcu-{i}",
                    role="DCU",
                    dcu_id=dcu.id,
                    password_hash=hashed,
                ),
                i,
            )
            for i, dcu in enumerate(dcus)
        ]
        for i, sbu in enumerate(sbus):
            account(
                User(
                    username=f"drill-sbu-{i}",
                    role="SBU",
                    sbu_id=sbu.id,
                    password_hash=hashed,
                ),
                i,
            )
        proof_rows = []
        for i in range(800):
            alc = ALC(
                alc_code=f"DR{i:06}",
                alc_name=f"Synthetic ALC {i:03}",
                sbu_id=sbus[i % 16].id,
            )
            db.add(alc)
            await db.flush()
            user = User(
                username=alc.alc_code, role="ALC", alc_id=alc.id, password_hash=hashed
            )
            item = account(user, i)
            partner = Partner(
                alc_id=alc.id,
                partner_name="Synthetic partner",
                partner_type="School",
                ecosystem="Education",
                status="ACTIVE",
            )
            activity = Activity(
                activity_number=f"DRILL-{i:06}",
                alc_id=alc.id,
                activity_type="Workshop",
                ecosystem="Education",
                activity_date=today,
                location="Synthetic location",
                description="Synthetic recovery and load data",
                outcome="Synthetic outcome",
                status="VERIFIED",
                verified_at=now,
                submitted_at=now,
                learners_reached=i % 100,
                leads_generated=i % 20,
                admissions_generated=i % 5,
            )
            member = YearlyActivityCompletion(
                yearly_activity_id=definition.id,
                yearly_activity_dcu_run_id=runs[i % 4].id,
                alc_id=alc.id,
                snapshot_sbu_id=sbus[i % 16].id,
            )
            db.add_all([partner, activity, member])
            await db.flush()
            state = "SUBMITTED" if i < 4 else "DRAFT"
            sub = YearlyActivitySubmission(
                participation_id=member.id,
                status=state,
                revision=1 if i < 4 else 0,
                completion_date=today,
                remarks="Synthetic completion details for isolated rehearsal",
                submitted_at=now if i < 4 else None,
            )
            db.add(sub)
            await db.flush()
            item["submission_id"] = str(sub.id)
            if i < 4:
                dcu_accounts[i]["review_submission_id"] = str(sub.id)
            if i in (0, 1, 2, 3, 4):
                proof_rows.append((i, sub, activity, alc, item))
        await db.flush()
        client = storage_service.client
        client.create_bucket(Bucket=os.environ["S3_BUCKET"])
        png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a9e8AAAAASUVORK5CYII="
        )
        sys.path.insert(0, str(API / ".deps/drill"))
        import imageio_ffmpeg

        video = LOCAL / "master-synthetic.mp4"
        await asyncio.to_thread(
            subprocess.run,
            [
                imageio_ffmpeg.get_ffmpeg_exe(),
                "-y",
                "-f",
                "lavfi",
                "-i",
                "color=c=blue:s=64x64:d=1",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                str(video),
            ],
            check=True,
            capture_output=True,
        )
        for i, sub, activity, alc, item in proof_rows:
            body, mime, extension = (
                (video.read_bytes(), "video/mp4", "mp4")
                if i == 1
                else (png, "image/png", "png")
            )
            key = f"yearly-evidence/{alc.id}/{sub.id}/recovery.{extension}"
            client.put_object(
                Bucket=os.environ["S3_BUCKET"], Key=key, Body=body, ContentType=mime
            )
            evidence = YearlyActivityEvidence(
                submission_id=sub.id,
                storage_key=key,
                original_filename=f"synthetic.{extension}",
                mime_type=mime,
                file_size=len(body),
                sha256=hashlib.sha256(body).hexdigest(),
                submitted_at=now if i < 4 else None,
            )
            db.add(evidence)
            await db.flush()
            item["evidence_id"] = str(evidence.id)
            if i == 0:
                oldkey = f"activities/{activity.id}/recovery.png"
                client.put_object(
                    Bucket=os.environ["S3_BUCKET"],
                    Key=oldkey,
                    Body=png,
                    ContentType="image/png",
                )
                old = ActivityEvidence(
                    activity_id=activity.id,
                    storage_key=oldkey,
                    original_filename="synthetic.png",
                    mime_type="image/png",
                    file_size=len(png),
                )
                db.add(old)
                await db.flush()
                item["old_evidence_id"] = str(old.id)
            if i == 2:
                sub.status, sub.verified_at = "VERIFIED", now
                db.add(
                    YearlyActivityReview(
                        submission_id=sub.id,
                        revision=1,
                        previous_status="SUBMITTED",
                        decision="VERIFY",
                        remarks="Synthetic reviewed proof",
                        snapshot={"synthetic": True},
                    )
                )
        # Pre-authenticated synthetic sessions let the steady-state load test measure portal
        # traffic independently of Argon2/login throttling. These raw test credentials stay
        # only in ignored .local/master-accounts.json and are never committed.
        await db.flush()
        for synthetic_user, item in account_refs:
            family, _ = await new_session(db, synthetic_user.id)
            item["access_token"] = create_access_token(synthetic_user.id, family.id)
            item["csrf_token"] = new_csrf_token()
        await db.commit()
        # Include all four roles even in the smallest stage, with distinct accounts.
        ordered = []
        alcs = accounts[21:]
        supervisors = [accounts[0]]
        for index, dcu_account in enumerate(accounts[1:5]):
            supervisors.append(dcu_account)
            supervisors.extend(accounts[5 + index * 4 : 9 + index * 4])
        for i, item in enumerate(alcs):
            if i % 8 == 0 and supervisors:
                ordered.append(supervisors.pop(0))
            ordered.append(item)
        ordered.extend(supervisors)
        (LOCAL / "master-accounts.json").write_text(
            json.dumps(ordered), encoding="utf-8"
        )
        print(
            json.dumps(
                {
                    "users": len(accounts),
                    "alcs": 800,
                    "yearly_submissions": 800,
                    "objects": 6,
                    "revision": "20261104_0010",
                }
            )
        )
    await engine.dispose()


if __name__ == "__main__":
    action = sys.argv[1]
    if action == "prepare":
        make_config()
    configure(action == "restore-serve")
    if action in {"prepare", "seed"}:
        asyncio.run(prepare())
    elif action == "check":
        asyncio.run(check_database_identity())
    elif action == "refresh-sessions":
        asyncio.run(refresh_sessions())
    elif action == "verify-sessions":
        asyncio.run(verify_synthetic_authentication())
    elif action in {"serve", "restore-serve"}:
        import uvicorn

        sys.path.insert(0, str(API / ".deps/drill"))
        import psutil
        from app.database import engine
        from app.main import app
        from app.performance_diagnostics import snapshot as performance_snapshot

        original = app.router.lifespan_context

        async def sample():
            process = psutil.Process()
            path = LOCAL / (
                "master-restore-samples.jsonl"
                if action == "restore-serve"
                else "master-server-samples-phase1b.jsonl"
                if os.environ.get("ALC_DB_PERF_DIAGNOSTICS") == "1"
                else "master-server-samples.jsonl"
            )
            while True:
                spool_bytes = 0
                for spool_file in (LOCAL / "master-spool").glob("*"):
                    try:
                        spool_bytes += spool_file.stat().st_size
                    except FileNotFoundError:
                        continue
                record = {
                    "time": datetime.now(timezone.utc).isoformat(),
                    "rss": process.memory_info().rss,
                    "cpu_seconds": sum(process.cpu_times()[:2]),
                    "pool_checkedout": engine.pool.checkedout(),
                    "pool_size": engine.pool.size(),
                    "pool_overflow": engine.pool.overflow(),
                    "db_lifecycle": performance_snapshot(engine),
                    "spool_bytes": spool_bytes,
                }
                with path.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(record) + "\n")
                await asyncio.sleep(0.5)

        @asynccontextmanager
        async def monitored(app):
            async with original(app):
                task = asyncio.create_task(sample())
                try:
                    yield
                finally:
                    task.cancel()

        app.router.lifespan_context = monitored
        uvicorn.run(
            app,
            host="127.0.0.1",
            port=18081 if action == "restore-serve" else 18080,
            workers=1,
            log_level="warning",
            access_log=False,
        )
    else:
        raise SystemExit("Use prepare, serve or restore-serve")
