import uuid

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog, User
from app.services.login_limiter import client_ip


async def record_audit(
    db: AsyncSession,
    action: str,
    entity_type: str,
    entity_id: str | uuid.UUID | None = None,
    actor: User | None = None,
    request: Request | None = None,
    metadata: dict | None = None,
) -> None:
    db.add(
        AuditLog(
            actor_user_id=actor.id if actor else None,
            actor_role=actor.role.value if actor else None,
            alc_id=actor.alc_id if actor else None,
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id) if entity_id else None,
            audit_metadata=metadata or {},
            # Same trusted-proxy rules as login rate limiting (TRUSTED_PROXY_CIDRS); Uvicorn
            # runs with --no-proxy-headers, so request.client is the direct peer.
            ip_address=client_ip(request) if request and request.client else None,
            user_agent=request.headers.get("user-agent") if request else None,
        )
    )
