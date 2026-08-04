"""研究参加者の登録・ログイン・状態復元 API。"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.auth import get_current_user, get_user_repository
from app.api.routers.chat import get_ask_registry
from app.api.schemas.users import (
    CurrentItineraryResponse,
    LoginResponse,
    MeResponse,
    ProfileResponse,
    ThreadResponse,
    UserNameRequest,
)
from app.domains.conversation.ask_registry import AskUserRegistry
from app.domains.users import (
    UserAlreadyExistsError,
    UserData,
    UserRepository,
    login_user,
    register_user,
)

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
) -> ThreadResponse:
    thread = await repository.get_thread(current_user.id)
    itinerary = (
        CurrentItineraryResponse(
            version=thread.itinerary.version,
            itinerary=thread.itinerary.body,
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
