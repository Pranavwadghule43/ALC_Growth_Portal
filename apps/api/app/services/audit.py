import uuid

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog, User


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
            ip_address=request.client.host if request and request.client else None,
            user_agent=request.headers.get("user-agent") if request else None,
        )
    )
