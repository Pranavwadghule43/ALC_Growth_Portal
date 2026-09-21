import jwt
from fastapi import Cookie, Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth import decode_access_token
from app.database import get_db
from app.enums import Role
from app.models import User


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
        select(User)
        .options(selectinload(User.alc), selectinload(User.sbu))
        .where(User.id == user_id)
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
