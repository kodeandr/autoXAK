"""Authentication shared by owner-scoped APIs; never guesses a user."""

import math
import time

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import decode_access_token
from app.models.db_models import User


bearer = HTTPBearer(auto_error=False)


async def require_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> User:
    denied = HTTPException(401, "A valid Bearer token is required", headers={"WWW-Authenticate": "Bearer"})
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise denied
    claims = decode_access_token(credentials.credentials)
    if not isinstance(claims, dict):
        raise denied
    subject, expires = claims.get("sub"), claims.get("exp")
    if not isinstance(subject, str) or not subject or len(subject) > 64:
        raise denied
    if isinstance(expires, bool) or not isinstance(expires, (int, float)):
        raise denied
    if not math.isfinite(expires) or expires <= time.time():
        raise denied
    user = await db.get(User, subject)
    if user is None:
        raise denied
    return user
