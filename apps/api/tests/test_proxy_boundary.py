"""Phase 4G-B: trusted-proxy boundary (who decides which forwarding headers to believe).

``login_limiter.client_ip`` is the ONLY trusted-proxy implementation, driven solely by
TRUSTED_PROXY_CIDRS. Every guarantee below assumes ASGI ``scope["client"]`` is still the
direct TCP peer when the application runs, i.e. Uvicorn has NOT already rewritten it.
Uvicorn does rewrite it by default (proxy headers on, 127.0.0.1 trusted), so it must run
with ``--no-proxy-headers``. These tests pin:

* Cases A / B / C of the boundary review, directly and through the real login route
  (rate-limit key and audit-log IP).
* What goes wrong when Uvicorn's own proxy-header processing is left on.
* That the Uvicorn commands documented in the README really disable it (parsed by
  Uvicorn's own CLI) and that no project file re-enables it.
"""
import os
import re
import shlex
from pathlib import Path

import pytest
import uvicorn
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from starlette.requests import Request
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app.main import app
from app.models import AuditLog
from app.services import login_limiter
from app.services.login_limiter import client_ip, ip_key

REPO_ROOT = Path(__file__).resolve().parents[3]
LOCAL_PROXY_CIDRS = "127.0.0.1/32"
RULE = (
    "When application-level TRUSTED_PROXY_CIDRS is used, Uvicorn proxy-header processing "
    "must be disabled with --no-proxy-headers."
)

# (case, direct peer, X-Forwarded-For header line(s), effective client IP)
BOUNDARY_CASES = [
    ("A-untrusted-direct-peer", "203.0.113.10", ["1.2.3.4"], "203.0.113.10"),
    ("B-trusted-local-proxy", "127.0.0.1", ["203.0.113.10"], "203.0.113.10"),
    ("C-spoofed-invalid-left-value", "127.0.0.1", ["attacker-value, 203.0.113.10"],
     "203.0.113.10"),
    ("C-spoofed-valid-left-value", "127.0.0.1", ["1.2.3.4, 203.0.113.10"], "203.0.113.10"),
    ("C-spoofed-extra-header-line", "127.0.0.1", ["1.2.3.4", "203.0.113.10"], "203.0.113.10"),
]
CASE_IDS = [case[0] for case in BOUNDARY_CASES]


@pytest.fixture
def trusted_cidrs(monkeypatch):
    def apply(cidrs: str):
        monkeypatch.setattr(login_limiter.settings, "trusted_proxy_cidrs", cidrs)

    return apply


def http_scope(peer: str, xff_lines: list[str]) -> dict:
    return {
        "type": "http", "method": "POST", "path": "/api/auth/login", "scheme": "http",
        "client": (peer, 5000), "server": ("127.0.0.1", 8000),
        "headers": [(b"x-forwarded-for", line.encode()) for line in xff_lines],
    }


def ip_keys() -> set[str]:
    return {key for key in login_limiter._local._entries if ":ip:" in key}


async def failed_login(asgi_app, peer: str, xff_lines: list[str]) -> int:
    headers = [("X-Forwarded-For", line) for line in xff_lines]
    transport = ASGITransport(app=asgi_app, client=(peer, 5000))
    async with AsyncClient(transport=transport, base_url="http://t") as http:
        response = await http.post(
            "/api/auth/login",
            json={"identifier": "no-such-user", "password": "WrongPassword123!"},
            headers=headers,
        )
    return response.status_code


# --------------------------------------------------------------------------- #
# Cases A / B / C with the direct peer intact (Uvicorn --no-proxy-headers)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(("case", "peer", "xff", "expected"), BOUNDARY_CASES, ids=CASE_IDS)
def test_boundary_cases_client_ip(trusted_cidrs, case, peer, xff, expected):
    trusted_cidrs(LOCAL_PROXY_CIDRS)
    assert client_ip(Request(http_scope(peer, xff))) == expected


@pytest.mark.parametrize(("case", "peer", "xff", "expected"), BOUNDARY_CASES, ids=CASE_IDS)
async def test_boundary_cases_through_login_route(
    client, session, trusted_cidrs, case, peer, xff, expected
):
    trusted_cidrs(LOCAL_PROXY_CIDRS)
    assert await failed_login(app, peer, xff) == 401
    # The rate-limit counter and the audit log both use the effective client IP only.
    assert ip_keys() == {ip_key("portal", expected)}
    audit_ips = (
        await session.scalars(select(AuditLog.ip_address).where(AuditLog.action == "login_failed"))
    ).all()
    assert audit_ips == [expected]


def test_localhost_is_not_trusted_without_configuration(trusted_cidrs):
    trusted_cidrs("")
    assert client_ip(Request(http_scope("127.0.0.1", ["203.0.113.10"]))) == "127.0.0.1"


# --------------------------------------------------------------------------- #
# Why --no-proxy-headers: Uvicorn's own processing would become a second authority
# --------------------------------------------------------------------------- #
async def test_uvicorn_default_proxy_headers_override_trusted_proxy_cidrs(
    client, trusted_cidrs
):
    trusted_cidrs("")  # the application trusts NO proxy ...
    uvicorn_default = ProxyHeadersMiddleware(app)  # ... but Uvicorn's default trusts 127.0.0.1
    assert await failed_login(uvicorn_default, "127.0.0.1", ["1.2.3.4"]) == 401
    assert ip_keys() == {ip_key("portal", "1.2.3.4")}  # Uvicorn decided, not the app

    login_limiter._local._entries.clear()
    assert await failed_login(app, "127.0.0.1", ["1.2.3.4"]) == 401  # --no-proxy-headers
    assert ip_keys() == {ip_key("portal", "127.0.0.1")}  # the app's rule applies


async def test_uvicorn_trust_all_breaks_case_a(trusted_cidrs):
    """FORWARDED_ALLOW_IPS="*": a direct, untrusted client could pick its own IP."""
    trusted_cidrs(LOCAL_PROXY_CIDRS)
    seen: list[str] = []

    async def probe(scope, receive, send):
        seen.append(client_ip(Request(scope)))

    await ProxyHeadersMiddleware(probe, trusted_hosts="*")(
        http_scope("203.0.113.10", ["1.2.3.4"]), None, None
    )
    await probe(http_scope("203.0.113.10", ["1.2.3.4"]), None, None)  # --no-proxy-headers
    assert seen == ["1.2.3.4", "203.0.113.10"]


def test_uvicorn_enables_proxy_headers_by_default(monkeypatch):
    """Not a safe default for us: the flag is mandatory, not optional."""
    monkeypatch.delenv("FORWARDED_ALLOW_IPS", raising=False)
    defaults = uvicorn.main.make_context("uvicorn", ["app.main:app"]).params
    assert defaults["proxy_headers"] is True
    config = uvicorn.Config("app.main:app", log_config=None, ws="none")
    config.load()
    assert isinstance(config.loaded_app, ProxyHeadersMiddleware)
    # Loopback is trusted by default: "127.0.0.1" (e.g. 0.46) or "127.0.0.1,::1" (newer).
    allowed = config.forwarded_allow_ips
    allowed = allowed.split(",") if isinstance(allowed, str) else list(allowed)
    assert "127.0.0.1" in [host.strip() for host in allowed]


# --------------------------------------------------------------------------- #
# Documentation / configuration guard
# --------------------------------------------------------------------------- #
def repo_file(name: str) -> str:
    path = REPO_ROOT / name
    if not path.is_file():
        pytest.skip(f"{name} not available outside the repository checkout")
    return path.read_text(encoding="utf-8")


def documented_uvicorn_commands(markdown: str) -> list[list[str]]:
    """Every ``uvicorn`` command inside README code blocks, continuation lines joined."""
    commands = []
    for block in re.findall(r"```[^\n]*\n(.*?)```", markdown, flags=re.S):
        joined = re.sub(r"[\\`]\s*\n", " ", block)  # bash "\" or PowerShell "`" continuation
        for line in joined.splitlines():
            if "uvicorn" not in line:
                continue
            tokens = shlex.split(line, comments=True)
            if "uvicorn" in tokens:
                commands.append(tokens[tokens.index("uvicorn") + 1:])
    return commands


def test_documented_uvicorn_commands_disable_proxy_headers(monkeypatch):
    monkeypatch.delenv("FORWARDED_ALLOW_IPS", raising=False)
    commands = documented_uvicorn_commands(repo_file("README.md"))
    assert len(commands) >= 2  # local development + production
    for args in commands:
        params = uvicorn.main.make_context("uvicorn", list(args)).params
        assert params["proxy_headers"] is False, args
        assert params["forwarded_allow_ips"] is None, args
        config = uvicorn.Config(
            params["app"], proxy_headers=params["proxy_headers"], log_config=None, ws="none"
        )
        config.load()
        assert not isinstance(config.loaded_app, ProxyHeadersMiddleware), args
    production = [args for args in commands if "--host" in args]
    assert production, "README must document the production Uvicorn command"
    for args in production:
        params = uvicorn.main.make_context("uvicorn", list(args)).params
        assert (params["host"], params["port"]) == ("127.0.0.1", 8000)


def test_rule_is_documented():
    def normalize(text: str) -> str:
        return " ".join(text.replace("`", "").replace("*", "").split())

    assert RULE in normalize(repo_file("README.md"))
    assert "TRUSTED_PROXY_CIDRS=127.0.0.1/32" in repo_file("README.md")
    env_example = normalize(repo_file(".env.example").replace("#", " "))
    assert "--no-proxy-headers" in env_example
    assert re.search(r"(?m)^TRUSTED_PROXY_CIDRS=$", repo_file(".env.example"))  # empty


SKIP_DIRS = {".git", "node_modules", "__pycache__", "dist", "build", ".pytest_cache",
             ".ruff_cache", ".mypy_cache"}
CONFIG_SUFFIXES = {".md", ".yml", ".yaml", ".toml", ".sh", ".ps1", ".bat", ".cmd", ".cfg",
                   ".ini", ".conf", ".service", ".example", ".json"}
CONFIG_NAMES = {"Dockerfile", "Procfile", "Makefile"}
ENABLES_UVICORN_PROXY = re.compile(
    r"(?m)^\s*(export\s+|set\s+|\$env:)?FORWARDED_ALLOW_IPS\s*[=:]"  # env assignment
    r"|^.*\buvicorn\b.*(--proxy-headers|--forwarded-allow-ips)"      # CLI option
    r"|\b(proxy_headers|forwarded_allow_ips)\s*=\s*(True|['\"])"     # uvicorn.run(...)
)


def test_no_project_file_enables_uvicorn_proxy_headers():
    if not (REPO_ROOT / "README.md").is_file():
        pytest.skip("repository checkout not available")
    offenders = []
    for folder, dirs, files in os.walk(REPO_ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith((".venv", "venv"))]
        for name in files:
            path = Path(folder, name)
            if name == ".env" or not (path.suffix in CONFIG_SUFFIXES or name in CONFIG_NAMES):
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            text = re.sub(r"(\\| `)[ \t]*\r?\n", " ", text)  # join continued command lines
            offenders += [
                f"{path.relative_to(REPO_ROOT)}: {match.group(0).strip()}"
                for match in ENABLES_UVICORN_PROXY.finditer(text)
            ]
    assert offenders == []
