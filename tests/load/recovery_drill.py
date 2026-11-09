"""Actual pg_dump/pg_restore and private S3 object recovery in guarded disposable targets.

Keeps binary backups, manifests and detailed results in ignored .local/master-recovery.
Never reads or restores alc_growth; never uses PostgreSQL 5432 or 55432.
"""

import argparse
import asyncio
import hashlib
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

import boto3
import httpx
from isolated_drill import LOCAL, ROOT, configure
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine

CONTAINER = "alc-master-isolated-20261104"
SOURCE = "alc_master_test"
TARGET = "alc_master_restore_test"


def normalized_constraint(definition):
    # PostgreSQL can move a lossless varchar[]→text[] cast onto each constant
    # when pg_restore reparses an IN check. Keep every value and operator.
    definition = re.sub(
        r"\('([A-Z_]+)'::character varying\)::text",
        r"'\1'::character varying",
        definition,
    )
    return re.sub(r"\((ARRAY\[[^\]]+\])\)::text\[\]", r"\1", definition)


async def snapshot(url):
    assert url.endswith(("/" + SOURCE, "/" + TARGET)) and ":55489/" in url
    engine = create_async_engine(url, hide_parameters=True)
    result = {"counts": {}, "row_hashes": {}}
    async with engine.begin() as conn:
        names = await conn.run_sync(
            lambda c: inspect(c).get_table_names(schema="public")
        )
        for table in sorted(names):
            assert table.replace("_", "").isalnum()
            result["counts"][table] = await conn.scalar(
                text(f'SELECT count(*) FROM "{table}"')
            )
            result["row_hashes"][table] = await conn.scalar(
                text(
                    f"SELECT md5(string_agg(row_to_json(t)::text, chr(10) "
                    f'ORDER BY row_to_json(t)::text)) FROM "{table}" t'
                )
            )
        result["revision"] = await conn.scalar(
            text("SELECT version_num FROM alembic_version")
        )
        result["constraints"] = [
            list(r)
            for r in await conn.execute(
                text(
                    "SELECT c.relname,con.conname,pg_get_constraintdef(con.oid) FROM pg_constraint con "
                    "JOIN pg_class c ON c.oid=con.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace "
                    "WHERE n.nspname='public' ORDER BY 1,2"
                )
            )
        ]
        result["normalized_constraints"] = [
            [table, name, normalized_constraint(definition)]
            for table, name, definition in result["constraints"]
        ]
        result["indexes"] = [
            list(r)
            for r in await conn.execute(
                text(
                    "SELECT tablename,indexname,indexdef FROM pg_indexes WHERE schemaname='public' "
                    "ORDER BY 1,2"
                )
            )
        ]
        result["size"] = await conn.scalar(
            text("SELECT pg_database_size(current_database())")
        )
    await engine.dispose()
    return result


def docker(*args):
    assert CONTAINER.startswith("alc-master-")
    return subprocess.run(["docker", *args], check=True, capture_output=True)


def login(client, user):
    route = "/auth/admin-login" if user["role"] == "ADMIN" else "/auth/login"
    response = client.post(
        "/api" + route, json={k: user[k] for k in ("role", "identifier", "password")}
    )
    assert response.status_code == 200, (user["role"], response.status_code)


async def supplementary_fixtures(url):
    from app.models import GrowthChallenge, Notification, User
    from sqlalchemy import func, select
    from sqlalchemy.ext.asyncio import async_sessionmaker

    engine = create_async_engine(url, hide_parameters=True)
    async with async_sessionmaker(engine, expire_on_commit=False)() as db:
        admin = await db.scalar(select(User).where(User.username == "drill-admin"))
        assert admin is not None
        if not await db.scalar(select(func.count()).select_from(GrowthChallenge)):
            today = datetime.now(timezone(timedelta(hours=5, minutes=30))).date()
            db.add(
                GrowthChallenge(
                    name="Synthetic recovery challenge",
                    start_date=today,
                    end_date=today + timedelta(days=2),
                    created_by=admin.id,
                )
            )
        if not await db.scalar(select(func.count()).select_from(Notification)):
            db.add(
                Notification(
                    user_id=admin.id,
                    title="Synthetic recovery notice",
                    message="Recovery fixture only",
                    type="system",
                )
            )
        await db.commit()
    await engine.dispose()


def main(artifact_name):
    config = configure()
    assert SOURCE != TARGET and "test" in TARGET and "alc_growth" not in TARGET
    assert re.fullmatch(r"master-recovery-[a-z0-9-]+", artifact_name)
    artifacts = LOCAL / artifact_name
    artifacts.mkdir(exist_ok=False)
    objects = artifacts / "objects"
    objects.mkdir()
    asyncio.run(supplementary_fixtures(config["DATABASE_URL"]))
    users = json.loads((LOCAL / "master-accounts.json").read_text(encoding="utf-8"))
    owner = next(u for u in users if u["identifier"] == "DR000000")
    with httpx.Client(base_url="http://127.0.0.1:18080", timeout=20) as client:
        login(client, owner)
        copied_access = client.cookies.get("access_token")
    before = asyncio.run(snapshot(config["DATABASE_URL"]))
    (artifacts / "source-snapshot.json").write_text(json.dumps(before, indent=2))
    report = {
        "started": datetime.now(timezone.utc).isoformat(),
        "source": SOURCE,
        "target": TARGET,
        "source_snapshot": before,
        "checks": {},
    }
    begin = time.perf_counter()
    backup_time = datetime.now(timezone.utc)
    docker(
        "exec",
        CONTAINER,
        "pg_dump",
        "-U",
        "postgres",
        "-Fc",
        "-d",
        SOURCE,
        "-f",
        "/tmp/alc-master.dump",
    )
    docker("cp", f"{CONTAINER}:/tmp/alc-master.dump", str(artifacts / "database.dump"))
    archive = docker("exec", CONTAINER, "pg_restore", "-l", "/tmp/alc-master.dump")
    (artifacts / "archive-list.txt").write_bytes(archive.stdout)
    report["postgres_backup_seconds"] = time.perf_counter() - begin
    body = (artifacts / "database.dump").read_bytes()
    report.update(
        postgres_backup_bytes=len(body),
        postgres_sha256=hashlib.sha256(body).hexdigest(),
        backup_timestamp=backup_time.isoformat(),
    )
    storage = boto3.client(
        "s3",
        endpoint_url=config["S3_ENDPOINT_URL"],
        aws_access_key_id=config["S3_ACCESS_KEY"],
        aws_secret_access_key=config["S3_SECRET_KEY"],
        region_name="us-east-1",
    )
    begin = time.perf_counter()
    manifest = []
    for page in storage.get_paginator("list_objects_v2").paginate(
        Bucket=config["S3_BUCKET"]
    ):
        for index, item in enumerate(page.get("Contents", []), start=len(manifest)):
            obj = storage.get_object(Bucket=config["S3_BUCKET"], Key=item["Key"])
            content = obj["Body"].read()
            filename = f"{index:05}.bin"
            (objects / filename).write_bytes(content)
            manifest.append(
                {
                    "key": item["Key"],
                    "file": filename,
                    "size": len(content),
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "content_type": obj["ContentType"],
                }
            )
    (artifacts / "objects-manifest.json").write_text(json.dumps(manifest, indent=2))
    report["object_backup_seconds"] = time.perf_counter() - begin
    report["object_count"] = len(manifest)
    report["object_bytes"] = sum(o["size"] for o in manifest)
    recovery_start = time.perf_counter()
    docker("exec", CONTAINER, "createdb", "-U", "postgres", TARGET)
    begin = time.perf_counter()
    docker(
        "exec",
        CONTAINER,
        "pg_restore",
        "-U",
        "postgres",
        "--exit-on-error",
        "--no-owner",
        "-d",
        TARGET,
        "/tmp/alc-master.dump",
    )
    report["postgres_restore_seconds"] = time.perf_counter() - begin
    after = asyncio.run(snapshot(config["RESTORE_DATABASE_URL"]))
    (artifacts / "restored-snapshot.json").write_text(json.dumps(after, indent=2))
    report["restored_snapshot"] = after
    report["raw_constraint_text_identical"] = (
        before["constraints"] == after["constraints"]
    )
    for key in (
        "counts",
        "row_hashes",
        "revision",
        "normalized_constraints",
        "indexes",
    ):
        report["checks"]["postgres_" + key] = before[key] == after[key]
        assert before[key] == after[key], key
    storage.create_bucket(Bucket=config["RESTORE_BUCKET"])
    begin = time.perf_counter()
    for obj in manifest:
        storage.put_object(
            Bucket=config["RESTORE_BUCKET"],
            Key=obj["key"],
            Body=(objects / obj["file"]).read_bytes(),
            ContentType=obj["content_type"],
        )
        restored = storage.get_object(Bucket=config["RESTORE_BUCKET"], Key=obj["key"])[
            "Body"
        ].read()
        assert hashlib.sha256(restored).hexdigest() == obj["sha256"]
    report["object_restore_seconds"] = time.perf_counter() - begin
    report["checks"]["object_checksums"] = True

    async def revoke_restored():
        engine = create_async_engine(
            config["RESTORE_DATABASE_URL"], hide_parameters=True
        )
        async with engine.begin() as conn:
            result = await conn.execute(
                text(
                    "UPDATE auth_sessions SET revoked_at=now() WHERE revoked_at IS NULL"
                )
            )
        await engine.dispose()
        return result.rowcount

    report["restored_sessions_revoked_before_start"] = asyncio.run(revoke_restored())
    stdout = (LOCAL / "master-restored-out.txt").open("w")
    stderr = (LOCAL / "master-restored-err.txt").open("w")
    process = subprocess.Popen(
        [sys.executable, str(ROOT / "tests/load/isolated_drill.py"), "restore-serve"],
        stdout=stdout,
        stderr=stderr,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    (LOCAL / "master-restored.pid").write_text(str(process.pid))
    try:
        base = "http://127.0.0.1:18081"
        for _ in range(60):
            try:
                response = httpx.get(base + "/api/auth/me", timeout=2)
                if response.status_code == 401:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(1)
        else:
            raise AssertionError("Restored API did not start")
        checks = report["checks"]
        with httpx.Client(base_url=base, timeout=20) as client:
            checks["copied_prebackup_access_revoked"] = (
                client.get(
                    "/api/auth/me", cookies={"access_token": copied_access}
                ).status_code
                == 401
            )
            login(client, owner)
            image = f"/api/yearly-activities/evidence/{owner['evidence_id']}/content"
            checks["authenticated_image"] = client.get(image).status_code == 200
            old = f"/api/portal/evidence/{owner['old_evidence_id']}/content"
            checks["authenticated_existing_activity"] = (
                client.get(old).status_code == 200
            )
            item = next(
                o
                for o in manifest
                if o["key"].endswith("/recovery.png")
                and owner["submission_id"] in o["key"]
            )
            checks["anonymous_api_denied"] = httpx.get(base + image).status_code == 401
            checks["anonymous_s3_denied"] = (
                httpx.get(
                    config["S3_ENDPOINT_URL"]
                    + "/"
                    + config["RESTORE_BUCKET"]
                    + "/"
                    + item["key"]
                ).status_code
                == 403
            )
            other = next(u for u in users if u["identifier"] == "DR000010")
            with httpx.Client(base_url=base) as unauthorized:
                login(unauthorized, other)
                checks["cross_alc_denied"] = unauthorized.get(image).status_code == 404
            storage.delete_object(Bucket=config["RESTORE_BUCKET"], Key=item["key"])
            checks["missing_object_controlled"] = client.get(image).status_code == 404
            storage.put_object(
                Bucket=config["RESTORE_BUCKET"],
                Key=item["key"],
                Body=(objects / item["file"]).read_bytes(),
                ContentType=item["content_type"],
            )
        video_owner = next(u for u in users if u["identifier"] == "DR000001")
        with httpx.Client(base_url=base, timeout=20) as client:
            login(client, video_owner)
            url = (
                f"/api/yearly-activities/evidence/{video_owner['evidence_id']}/content"
            )
            video = client.get(url)
            ranged = client.get(url, headers={"Range": "bytes=0-99"})
            checks["video_bytes_and_mime"] = (
                video.status_code == 200
                and video.headers["content-type"] == "video/mp4"
            )
            checks["video_range"] = (
                ranged.status_code == 206 and ranged.content == video.content[:100]
            )
        report["measured_recovery_seconds"] = time.perf_counter() - recovery_start
        report["backup_snapshot_age_at_recovery_seconds"] = (
            datetime.now(timezone.utc) - backup_time
        ).total_seconds()
        report["finished"] = datetime.now(timezone.utc).isoformat()
        report["all_checks_passed"] = all(checks.values())
        (artifacts / "results.json").write_text(json.dumps(report, indent=2))
        print(
            json.dumps(
                {
                    k: v
                    for k, v in report.items()
                    if k not in {"source_snapshot", "restored_snapshot"}
                }
            )
        )
        assert report["all_checks_passed"], checks
    finally:
        if process.poll() is None:
            # The Windows venv launcher can own another Python process. Stop the
            # exact child tree created above, including that launcher descendant.
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                check=False,
                capture_output=True,
            )
        process.wait(timeout=15)
        stdout.close()
        stderr.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifacts", default="master-recovery-final")
    main(parser.parse_args().artifacts)
