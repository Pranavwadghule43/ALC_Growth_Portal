"""Bounded real HTTP uploads into the guarded private test S3 service."""

import concurrent.futures
import json
import subprocess
import time
from datetime import datetime, timezone

import boto3
import httpx
from botocore.exceptions import BotoCoreError, ClientError
from isolated_drill import LOCAL, configure


def main():
    config = configure()
    accounts = json.loads((LOCAL / "master-accounts.json").read_text(encoding="utf-8"))
    users = [
        next(u for u in accounts if u["identifier"] == f"DR{i:06}")
        for i in range(8, 12)
    ]
    clients = []
    for user in users:
        client = httpx.Client(base_url="http://127.0.0.1:18080", timeout=60)
        login = client.post(
            "/api/auth/login",
            json={k: user[k] for k in ("role", "identifier", "password")},
        )
        assert login.status_code == 200
        client.headers["X-CSRF-Token"] = client.cookies.get("csrf_token")
        clients.append(client)
    video = (LOCAL / "master-synthetic.mp4").read_bytes()
    results = []

    def authenticate(index):
        response = clients[index].post(
            "/api/auth/login",
            json={key: users[index][key] for key in ("role", "identifier", "password")},
        )
        assert response.status_code == 200
        clients[index].headers["X-CSRF-Token"] = clients[index].cookies.get(
            "csrf_token"
        )

    def upload(index, body, label):
        begin = time.perf_counter()
        response = clients[index].post(
            f"/api/yearly-activities/submissions/{users[index]['submission_id']}/evidence",
            files={"files": (f"{label}.mp4", body, "video/mp4")},
        )
        return {
            "status": response.status_code,
            "seconds": time.perf_counter() - begin,
            "bytes": len(body),
        }

    for concurrency, megabytes in ((1, 1), (4, 5), (4, 10)):
        for index in range(concurrency):
            authenticate(index)
        size = megabytes * 1024 * 1024
        padding = size - len(video)
        # A legitimate ISO BMFF free box retains a playable one-second synthetic video.
        body = video + padding.to_bytes(4, "big") + b"free" + b"\0" * (padding - 8)
        started = datetime.now(timezone.utc).isoformat()
        begin = time.perf_counter()
        with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
            responses = list(
                pool.map(
                    lambda index, body=body, megabytes=megabytes: upload(
                        index, body, f"capacity-{megabytes}"
                    ),
                    range(concurrency),
                )
            )
        results.append(
            {
                "concurrency": concurrency,
                "mib": megabytes,
                "started": started,
                "ended": datetime.now(timezone.utc).isoformat(),
                "wall_seconds": time.perf_counter() - begin,
                "responses": responses,
            }
        )
        print(json.dumps(results[-1]), flush=True)
        (LOCAL / "master-upload-partial.json").write_text(json.dumps(results, indent=2))
    authenticate(0)
    over_limit = upload(
        0, video + b"\0" * (10 * 1024 * 1024 + 1 - len(video)), "too-large"
    )
    assert over_limit["status"] == 413
    authenticate(0)
    outage_start = datetime.now(timezone.utc).isoformat()
    subprocess.run(
        ["docker", "stop", "--time", "5", "alc-master-s3-20261104"],
        check=True,
        capture_output=True,
    )
    try:
        outage = upload(0, video, "outage")
    finally:
        subprocess.run(
            ["docker", "start", "alc-master-s3-20261104"],
            check=True,
            capture_output=True,
        )
    storage = boto3.client(
        "s3",
        endpoint_url=config["S3_ENDPOINT_URL"],
        aws_access_key_id=config["S3_ACCESS_KEY"],
        aws_secret_access_key=config["S3_SECRET_KEY"],
    )
    for _ in range(30):
        try:
            storage.head_bucket(Bucket=config["S3_BUCKET"])
            break
        except (BotoCoreError, ClientError):
            time.sleep(1)
    else:
        raise AssertionError("Disposable storage did not recover")
    for client in clients:
        client.close()
    report = {
        "stages": results,
        "over_limit": over_limit,
        "outage": outage,
        "outage_started": outage_start,
        "all_uploads_passed": all(
            r["status"] == 201 for stage in results for r in stage["responses"]
        ),
        "outage_controlled": outage["status"] == 503,
    }
    (LOCAL / "master-upload-results.json").write_text(json.dumps(report, indent=2))
    print(json.dumps({k: v for k, v in report.items() if k != "stages"}), flush=True)
    assert report["all_uploads_passed"] and report["outage_controlled"]


if __name__ == "__main__":
    main()
