import asyncio
import os

from dotenv import load_dotenv

from sqlalchemy import select

from app.auth import hash_password
from app.database import SessionLocal
from app.enums import Role
from app.models import ALC, User


async def main() -> None:
    load_dotenv("../../.env")
    password = os.getenv("DEV_ALC_PASSWORD")
    if not password or len(password) < 12:
        raise SystemExit("Set DEV_ALC_PASSWORD to a 12+ character development password")
    async with SessionLocal() as db:
        alcs = (await db.scalars(select(ALC))).all()
        created = 0
        for alc in alcs:
            if not await db.scalar(
                select(User.id).where(User.alc_id == alc.id, User.role == Role.ALC)
            ):
                db.add(
                    User(
                        username=f"alc-{alc.alc_code}",
                        password_hash=hash_password(password),
                        role=Role.ALC,
                        alc_id=alc.id,
                        must_change_password=True,
                    )
                )
                created += 1
        await db.commit()
    print(f"Created {created} development ALC users")


if __name__ == "__main__":
    asyncio.run(main())
