import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("SECRET_KEY", "test-secret-key-that-is-long-enough-123456")

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.auth import hash_password
from app.database import Base, get_db
from app.enums import Role
from app.main import app
from app.models import ALC, User


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine(
        "sqlite+aiosqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db:
        yield db
    await engine.dispose()


@pytest_asyncio.fixture
async def seeded(session):
    alc_a = ALC(alc_code="00010001", alc_name="Centre A")
    alc_b = ALC(alc_code="00010002", alc_name="Centre B")
    session.add_all([alc_a, alc_b])
    await session.flush()
    admin = User(username="admin", password_hash=hash_password("StrongAdminPass!"), role=Role.ADMIN)
    user_a = User(
        username="alc-a",
        password_hash=hash_password("StrongAlcPassA!"),
        role=Role.ALC,
        alc_id=alc_a.id,
    )
    user_b = User(
        username="alc-b",
        password_hash=hash_password("StrongAlcPassB!"),
        role=Role.ALC,
        alc_id=alc_b.id,
    )
    session.add_all([admin, user_a, user_b])
    await session.commit()
    return {"admin": admin, "a": user_a, "b": user_b, "alc_a": alc_a, "alc_b": alc_b}


@pytest_asyncio.fixture
async def client(session, seeded):
    async def override_db():
        yield session

    app.dependency_overrides[get_db] = override_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


async def login(client, identifier, password, portal):
    response = await client.post(
        "/api/auth/login", json={"identifier": identifier, "password": password, "portal": portal}
    )
    if response.status_code == 200:
        csrf = client.cookies.get("csrf_token")
        client.headers["X-CSRF-Token"] = csrf
    return response
