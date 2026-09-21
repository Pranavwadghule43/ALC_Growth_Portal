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
from app.models import ALC, SBU, User


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
    sbu4 = SBU(code="SBU 4", name="Strategic Business Unit 4")
    sbu6 = SBU(code="SBU 6", name="Strategic Business Unit 6")
    session.add_all([sbu4, sbu6])
    await session.flush()
    # sbu4 owns Centre A and Centre C; sbu6 owns Centre B.
    alc_a = ALC(alc_code="00010001", alc_name="Centre A", sbu_id=sbu4.id)
    alc_b = ALC(alc_code="00010002", alc_name="Centre B", sbu_id=sbu6.id)
    alc_c = ALC(alc_code="00010003", alc_name="Centre C", sbu_id=sbu4.id)
    session.add_all([alc_a, alc_b, alc_c])
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
    user_c = User(
        username="alc-c",
        password_hash=hash_password("StrongAlcPassC!"),
        role=Role.ALC,
        alc_id=alc_c.id,
    )
    sbu_user_4 = User(
        username="sbu-4",
        password_hash=hash_password("StrongSbuPass4!"),
        role=Role.SBU,
        sbu_id=sbu4.id,
    )
    sbu_user_6 = User(
        username="sbu-6",
        password_hash=hash_password("StrongSbuPass6!"),
        role=Role.SBU,
        sbu_id=sbu6.id,
    )
    session.add_all([admin, user_a, user_b, user_c, sbu_user_4, sbu_user_6])
    await session.commit()
    return {
        "admin": admin,
        "a": user_a,
        "b": user_b,
        "c": user_c,
        "sbu4_user": sbu_user_4,
        "sbu6_user": sbu_user_6,
        "alc_a": alc_a,
        "alc_b": alc_b,
        "alc_c": alc_c,
        "sbu4": sbu4,
        "sbu6": sbu6,
    }


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
