import jwt
from fastapi import Cookie, Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth import decode_access_token
from app.database import get_db
from app.enums import Role
from app.models import ALC, User

# Eager-load every relationship the ``UserOut`` response schema serializes, including
# the nested ``User.alc -> ALC.sbu`` chain. Without loading ``ALC.sbu`` here, Pydantic
# would trigger an async lazy load while building the response and fail with
# ``MissingGreenlet``. Any endpoint that returns a ``User`` as ``UserOut`` must load
# these (directly, or by re-querying through ``load_user_for_response`` after a commit).
USER_RESPONSE_LOADERS = (
    selectinload(User.alc).selectinload(ALC.sbu),
    selectinload(User.sbu),
)


async def load_user_for_response(db: AsyncSession, user_id) -> User | None:
    """Re-query a user with every relationship the response schema needs eagerly loaded.

    Use after a ``commit()`` on create/update endpoints so the returned ORM object never
    lazy-loads ``alc`` / ``alc.sbu`` / ``sbu`` during serialization.
    """
    return await db.scalar(
        select(User).options(*USER_RESPONSE_LOADERS).where(User.id == user_id)
    )


async def get_current_user(
    access_token: str | None = Cookie(default=None),
    db: AsyncSession = Depends(get_db),
) -> User:
    if not access_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required"
        )
    try:
        user_id = decode_access_token(access_token)
    except (jwt.InvalidTokenError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired"
        ) from None
    user = await db.scalar(
        select(User).options(*USER_RESPONSE_LOADERS).where(User.id == user_id)
    )
    if not user or not user.is_active or (user.alc and user.alc.status.value != "ACTIVE"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Account unavailable")
    return user


async def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != Role.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Administrator access required"
        )
    if user.must_change_password:
        raise HTTPException(status_code=403, detail="Password change required")
    return user


async def require_portal_user(user: User = Depends(get_current_user)) -> User:
    """Allow only operational-portal roles (SBU or ALC), never ADMIN."""
    if user.role == Role.ALC and user.alc_id is not None:
        pass
    elif user.role == Role.SBU and user.sbu_id is not None:
        pass
    else:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Portal access required")
    if user.must_change_password:
        raise HTTPException(status_code=403, detail="Password change required")
    return user


async def require_sbu(user: User = Depends(get_current_user)) -> User:
    if user.role != Role.SBU or user.sbu_id is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="SBU access required")
    if user.must_change_password:
        raise HTTPException(status_code=403, detail="Password change required")
    return user


async def require_alc(user: User = Depends(get_current_user)) -> User:
    if user.role != Role.ALC or user.alc_id is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="ALC access required")
    if user.must_change_password:
        raise HTTPException(status_code=403, detail="Password change required")
    return user


async def require_csrf(
    request: Request,
    csrf_cookie: str | None = Cookie(default=None, alias="csrf_token"),
    csrf_header: str | None = Header(default=None, alias="X-CSRF-Token"),
) -> None:
    if request.method not in {"GET", "HEAD", "OPTIONS"} and (
        not csrf_cookie or not csrf_header or csrf_cookie != csrf_header
    ):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")


def pagination(page: int = 1, page_size: int = 25) -> tuple[int, int]:
    if page < 1 or page_size < 1 or page_size > 100:
        raise HTTPException(status_code=422, detail="Invalid pagination parameters")
    return page, page_size
