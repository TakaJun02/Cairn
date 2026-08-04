"""Bearer トークンから現在の研究参加者を特定する共通依存。"""

from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db_session
from app.domains.users import UserData, UserRepository

_bearer = HTTPBearer(auto_error=False)


def get_user_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> UserRepository:
    return UserRepository(session)


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    repository: Annotated[UserRepository, Depends(get_user_repository)],
) -> UserData:
    """有効な `Authorization: Bearer` だけを受理する。"""

    if credentials is None:
        raise _unauthorized()
    user = await repository.find_by_token(credentials.credentials)
    if user is None:
        raise _unauthorized()
    return user


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="有効な Bearer トークンが必要です",
        headers={"WWW-Authenticate": "Bearer"},
    )
