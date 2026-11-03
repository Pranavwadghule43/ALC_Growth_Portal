"""Scheduled expiry maintenance. Never prints tokens, identifiers or connection URLs.

Run hourly using the normal API environment, after migration0009:
    python -m scripts.cleanup_auth_sessions --batch-size 1000 --max-batches 100
The operator schedules it; API startup/requests do not run destructive maintenance.
"""
import argparse
import asyncio
import json

from app.database import SessionLocal, engine
from app.services.sessions import cleanup_expired_sessions


async def cleanup(batch_size: int, max_batches: int) -> dict:
    total = {"sessions_deleted": 0, "refresh_rows_deleted": 0, "legacy_rows_deleted": 0}
    try:
        for _ in range(max_batches):
            async with SessionLocal() as db:
                result = await cleanup_expired_sessions(db, batch_size=batch_size)
                await db.commit()
            for key, count in result.items():
                total[key] += count
            if (result["sessions_deleted"] < batch_size
                    and result["legacy_rows_deleted"] < batch_size):
                break
        return total
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-size", type=int, default=1000, choices=range(1, 10_001),
                        metavar="1..10000")
    parser.add_argument("--max-batches", type=int, default=100, choices=range(1, 1001),
                        metavar="1..1000")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(cleanup(args.batch_size, args.max_batches))))
