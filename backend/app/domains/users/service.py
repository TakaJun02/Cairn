"""ユーザー登録と簡易ログインのユースケース。"""

import secrets

from app.domains.users.repo import TokenCollisionError, UserData, UserRepository

_TOKEN_BYTES = 32
_TOKEN_GENERATION_ATTEMPTS = 3


class UserAlreadyExistsError(RuntimeError):
    """明示的な登録で同名ユーザーがすでに存在する。"""


async def register_user(repository: UserRepository, user_name: str) -> UserData:
    user, created = await _create_with_unique_token(repository, user_name)
    if not created:
        raise UserAlreadyExistsError(f"user_name は登録済みです: {user_name}")
    return user


async def login_user(repository: UserRepository, user_name: str) -> UserData:
    """未登録なら作成し、登録済みなら同じ失効しないトークンを返す。"""

    existing = await repository.find_by_name(user_name)
    if existing is not None:
        await repository.ensure_context(existing.id)
        return existing
    user, _ = await _create_with_unique_token(repository, user_name)
    return user


async def _create_with_unique_token(
    repository: UserRepository,
    user_name: str,
) -> tuple[UserData, bool]:
    last_error: TokenCollisionError | None = None
    for _ in range(_TOKEN_GENERATION_ATTEMPTS):
        try:
            return await repository.create_or_get(
                user_name,
                secrets.token_urlsafe(_TOKEN_BYTES),
            )
        except TokenCollisionError as exc:
            last_error = exc
    raise RuntimeError("一意な api_token を生成できませんでした") from last_error
