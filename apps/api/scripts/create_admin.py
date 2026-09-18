import argparse
import asyncio
import getpass
import os

from dotenv import load_dotenv

from sqlalchemy import select

from app.auth import hash_password
from app.database import SessionLocal
from app.enums import Role
from app.models import User


async def create_admin(username: str, email: str | None, password: str) -> None:
    async with SessionLocal() as db:
        if await db.scalar(select(User.id).where(User.username == username)):
            raise SystemExit(f"User {username!r} already exists")
        db.add(
            User(
                username=username,
                email=email,
                password_hash=hash_password(password),
                role=Role.ADMIN,
                is_active=True,
                must_change_password=False,
            )
        )
        await db.commit()
    print(f"Admin {username!r} created")


def main() -> None:
    load_dotenv("../../.env")
    parser = argparse.ArgumentParser()
    parser.add_argument("--username", default="admin")
    parser.add_argument("--email")
    args = parser.parse_args()
    password = os.getenv("DEV_ADMIN_PASSWORD") or getpass.getpass(
        "Admin password (12+ characters): "
    )
    if len(password) < 12:
        raise SystemExit("Password must contain at least 12 characters")
    asyncio.run(create_admin(args.username, args.email, password))


if __name__ == "__main__":
    main()
