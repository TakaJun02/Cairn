"""研究参加者の登録・ログイン・状態復元 API。"""

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.auth import get_current_user, get_user_repository
from app.api.routers.chat import get_ask_registry
from app.api.routers.itinerary import get_itinerary_repository
from app.api.schemas.users import (
    CurrentItineraryResponse,
    LoginResponse,
    MeResponse,
    ProfileResponse,
    ThreadResponse,
    UserNameRequest,
)
from app.domains.conversation.ask_registry import AskUserRegistry
from app.domains.conversation.itinerary_digest import mask_concession_list
from app.domains.itinerary.repo import ItineraryRepository
from app.domains.users import (
    UserAlreadyExistsError,
    UserData,
    UserRepository,
    login_user,
    register_user,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["users"])


@router.post("/users", response_model=LoginResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    request: UserNameRequest,
    repository: Annotated[UserRepository, Depends(get_user_repository)],
) -> LoginResponse:
    try:
        user = await register_user(repository, request.user_name)
    except UserAlreadyExistsError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return _login_response(user)


@router.post("/login", response_model=LoginResponse)
async def login(
    request: UserNameRequest,
    repository: Annotated[UserRepository, Depends(get_user_repository)],
) -> LoginResponse:
    user = await login_user(repository, request.user_name)
    return _login_response(user)


@router.get("/me", response_model=MeResponse)
async def get_me(
    current_user: Annotated[UserData, Depends(get_current_user)],
    repository: Annotated[UserRepository, Depends(get_user_repository)],
) -> MeResponse:
    profile = await repository.get_profile(current_user.id)
    return MeResponse(
        user_id=current_user.id,
        user_name=current_user.user_name,
        lora_device_id=current_user.lora_device_id,
        created_at=current_user.created_at,
        updated_at=current_user.updated_at,
        profile=ProfileResponse.model_validate(profile),
    )


@router.get("/thread", response_model=ThreadResponse)
async def get_thread(
    current_user: Annotated[UserData, Depends(get_current_user)],
    repository: Annotated[UserRepository, Depends(get_user_repository)],
    ask_registry: Annotated[AskUserRegistry, Depends(get_ask_registry)],
    itinerary_repository: Annotated[ItineraryRepository, Depends(get_itinerary_repository)],
) -> ThreadResponse:
    thread = await repository.get_thread(current_user.id)
    itinerary = (
        CurrentItineraryResponse(
            version=thread.itinerary.version,
            itinerary=await _masked_itinerary_body(thread.itinerary.body, itinerary_repository),
        )
        if thread.itinerary is not None
        else None
    )
    pending = thread.pending
    if pending is not None and not ask_registry.is_waiting(current_user.id):
        # 生きた待機が無い(プロセス再起動等でターンが死んでいた)。
        # `pending` は返さず、DB 側も掃除する(§7・chat_sse.md §3.1)。
        pending = None
        clear = getattr(repository, "clear_pending_ask", None)
        if clear is not None:
            await clear(current_user.id)
    return ThreadResponse(
        messages=thread.messages,
        itinerary=itinerary,
        profile=ProfileResponse.model_validate(thread.profile),
        pending=pending,
    )


def _login_response(user: UserData) -> LoginResponse:
    return LoginResponse(user_id=user.id, user_name=user.user_name, token=user.api_token)


async def _masked_itinerary_body(
    body: Any, itinerary_repository: ItineraryRepository
) -> Any:
    """`body["concessions"][].message_ja` をマスクしてから返す。

    2026-08-04 追加([25 §1-7] レビュー是正): `GET /thread` は旧形式で
    永続化済みの旅程本文をそのまま返しており、`chat_sse.md §1.2` の
    サーバー契約(`concessions[].message_ja` は spot_id を含まない)から
    唯一漏れていた経路だった。`body` が dict でない・`concessions` キーが
    無い場合は防御的にスキップする。`concessions` が空なら名前ロードも
    スキップする。元の `body` は書き換えず、浅いコピーを返す。
    """

    if not isinstance(body, dict):
        return body
    concessions = body.get("concessions")
    if not isinstance(concessions, list) or not concessions:
        return body
    spot_names: dict[str, str] = {}
    try:
        spot_names = await itinerary_repository.load_spot_names()
    except Exception:  # noqa: BLE001 - 名前解決の失敗で GET /thread を落とさない
        logger.warning("spot_names のロードに失敗しました。中立表記で続行します", exc_info=True)
    masked = dict(body)
    masked["concessions"] = mask_concession_list(concessions, spot_names)
    return masked
