"""Measured k6 stages on the guarded synthetic environment, never a source database."""

import asyncio
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone

from isolated_drill import LOCAL, ROOT, configure


def main():
    configure()
    output = LOCAL / "load"
    output.mkdir(exist_ok=True)
    stress = "--stress" in sys.argv
    requested = os.environ.get("ALC_LOAD_STAGES")
    normal_stages = tuple(int(value) for value in requested.split(",")) if requested else (25, 50, 100, 200)
    if any(value not in (25, 50, 100, 200) for value in normal_stages):
        raise ValueError("ALC_LOAD_STAGES may contain only 25, 50, 100, and 200")
    if stress:
        for normal in (25, 50, 100, 200):
            prior = json.loads((output / f"stage-{normal}-resources.json").read_text(encoding="utf-8"))
            assert prior["exit_code"] == 0, (
                "Normal stages must meet all targets before stress"
            )
    for vus in (400, 800) if stress else normal_stages:
        if not stress and vus != 25:
            preceding = {50: 25, 100: 50, 200: 100}[vus]
            evidence_path = output / f"stage-{preceding}-resources.json"
            assert evidence_path.exists(), (
                f"Stage {preceding} must pass before {vus} VUs can start"
            )
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            assert evidence.get("endpoint_thresholds") is True and evidence["exit_code"] == 0, (
                f"Stage {preceding} must pass aggregate and endpoint thresholds before {vus} VUs"
            )
        refresh = subprocess.run(
            [sys.executable, str(ROOT / "tests/load/isolated_drill.py"), "refresh-sessions"],
            capture_output=True,
            text=True,
            check=False,
        )
        if refresh.returncode:
            raise RuntimeError("Synthetic session refresh failed; load stage not started")
        print(refresh.stdout.strip(), flush=True)
        authentication = subprocess.run(
            [sys.executable, str(ROOT / "tests/load/isolated_drill.py"), "verify-sessions"],
            capture_output=True,
            text=True,
            check=False,
        )
        if authentication.returncode:
            raise RuntimeError("Synthetic authentication preflight failed; load stage not started")
        print(authentication.stdout.strip(), flush=True)
        name = f"alc-master-k6-{vus}"
        assert name.startswith("alc-master-k6-")
        samples = []
        stop = threading.Event()
        api_listener = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                (
                    "(Get-NetTCPConnection -LocalPort 18080 -State Listen | "
                    "Where-Object LocalAddress -eq '127.0.0.1' | "
                    "Select-Object -First 1 -ExpandProperty OwningProcess)"
                ),
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        api_pid = int(api_listener.stdout.strip()) if api_listener.returncode == 0 else None

        def monitor(stop=stop, samples=samples, api_pid=api_pid):
            import asyncpg

            async def sample():
                conn = await asyncpg.connect(
                    os.environ["DATABASE_URL"].replace(
                        "postgresql+asyncpg://", "postgresql://"
                    )
                )
                try:
                    while not stop.is_set():
                        states = await conn.fetch(
                            "SELECT state, count(*) AS count, "
                            "max(extract(epoch FROM now() - xact_start)) AS oldest_xact_seconds "
                            "FROM pg_stat_activity WHERE datname='alc_master_test' GROUP BY state"
                        )
                        stats = await asyncio.to_thread(
                            subprocess.run,
                            [
                                "docker",
                                "stats",
                                "--no-stream",
                                "--format",
                                "{{json .}}",
                                "alc-master-postgres",
                                "alc-master-redis",
                                "alc-master-s3",
                            ],
                            capture_output=True,
                            text=True,
                            timeout=15,
                            check=False,
                        )
                        ping = await asyncio.to_thread(
                            subprocess.run,
                            [
                                "docker",
                                "exec",
                                "alc-master-redis",
                                "redis-cli",
                                "ping",
                            ],
                            capture_output=True,
                            text=True,
                            timeout=5,
                            check=False,
                        )
                        api_process = None
                        if api_pid is not None:
                            process = await asyncio.to_thread(
                                subprocess.run,
                                [
                                    "powershell",
                                    "-NoProfile",
                                    "-Command",
                                    (
                                        f"Get-Process -Id {api_pid} | "
                                        "Select-Object CPU,WorkingSet64,PrivateMemorySize64 | "
                                        "ConvertTo-Json -Compress"
                                    ),
                                ],
                                capture_output=True,
                                text=True,
                                timeout=10,
                                check=False,
                            )
                            if process.returncode == 0 and process.stdout.strip():
                                api_process = json.loads(process.stdout)
                        server_sample = None
                        sample_path = LOCAL / "master-server-samples-phase1b.jsonl"
                        if sample_path.exists():
                            try:
                                last_line = sample_path.read_text(encoding="utf-8").splitlines()[-1]
                                server_sample = json.loads(last_line)
                            except (IndexError, json.JSONDecodeError, OSError):
                                server_sample = None
                        samples.append(
                            {
                                "time": datetime.now(timezone.utc).isoformat(),
                                "postgres_states": {
                                    r["state"]: r["count"] for r in states
                                },
                                "oldest_transaction_seconds": max(
                                    (float(r["oldest_xact_seconds"] or 0) for r in states),
                                    default=0.0,
                                ),
                                "redis_ok": ping.stdout.strip() == "PONG",
                                "docker_stats": stats.stdout.splitlines(),
                                "api_process": api_process,
                                "server_pool_sample": server_sample,
                            }
                        )
                        await asyncio.sleep(3)
                finally:
                    await conn.close()

            asyncio.run(sample())

        thread = threading.Thread(target=monitor, daemon=True)
        started = datetime.now(timezone.utc).isoformat()
        thread.start()
        command = [
            "docker",
            "run",
            "--rm",
            "--name",
            name,
            "--mount",
            f"type=bind,source={ROOT / 'tests/load'},target=/scripts,readonly",
            "--mount",
            f"type=bind,source={LOCAL / 'master-accounts.json'},target=/accounts.json,readonly",
            "--mount",
            f"type=bind,source={output},target=/results",
        ]
        settings = {
            "ALC_LOAD_ISOLATED": "1",
            "ALC_LOAD_BASE_URL": "http://host.docker.internal:18080",
            "ALC_LOAD_CONFIRM_HOST": "host.docker.internal",
            "ALC_LOAD_ACCOUNTS_FILE": "/accounts.json",
            "ALC_LOAD_VUS": str(vus),
            "ALC_LOAD_DURATION": "30s",
            "ALC_LOAD_RAMP": "30s",
            "ALC_LOAD_DOWN": "15s",
            "ALC_LOAD_SYNTHETIC_WRITES": os.environ.get(
            "ALC_LOAD_SYNTHETIC_WRITES", "0"),
            "ALC_LOAD_STEADY_STATE": "1",
        }
        if stress:
            settings.update(
                ALC_LOAD_ALLOW_STRESS="1", ALC_LOAD_RAMP="60s", ALC_LOAD_DOWN="30s"
            )
        for key, value in settings.items():
            command.extend(["-e", f"{key}={value}"])
        command.extend(
            [
                "grafana/k6:latest",
                "run",
                "--summary-export",
                f"/results/stage-{vus}.json",
                "/scripts/portal.k6.js",
            ]
        )
        begin = time.perf_counter()
        with (output / f"stage-{vus}.txt").open("w", encoding="utf-8") as log:
            result = subprocess.run(
                command, stdout=log, stderr=subprocess.STDOUT, check=False
            )
        stop.set()
        thread.join(timeout=20)
        report = {
            "vus": vus,
            "endpoint_thresholds": True,
            "started": started,
            "ended": datetime.now(timezone.utc).isoformat(),
            "seconds": time.perf_counter() - begin,
            "exit_code": result.returncode,
            "samples": samples,
        }
        (output / f"stage-{vus}-resources.json").write_text(
            json.dumps(report, indent=2), encoding="utf-8"
        )
        print(
            json.dumps({k: v for k, v in report.items() if k != "samples"}), flush=True
        )
        if result.returncode != 0:
            print(f"Stopping before the next stage because {vus} VUs missed a k6 threshold.", flush=True)
            break
        time.sleep(5)


if __name__ == "__main__":
    main()

