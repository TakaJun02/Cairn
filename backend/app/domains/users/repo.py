"""app.users を頂点とするユーザー状態の永続化。"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db_models import Itinerary, Message, Profile, Thread, User


class TokenCollisionError(RuntimeError):
    """生成したトークンが別ユーザーのトークンと衝突した。"""


class UserContextError(RuntimeError):
    """ユーザーに 1:1 の thread/profile を用意できない。"""


@dataclass(frozen=True)
class UserData:
    id: int
    user_name: str
    api_token: str
    lora_device_id: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class ProfileData:
    user_id: int
    interests: dict[str, float]
    party: str | None
    mobility: str | None
    pace: str | None
    avoid: list[str]
    liked_spots: list[str]
    rejected_spots: list[dict[str, Any]]
    notes: str | None
    updated_at: datetime


@dataclass(frozen=True)
class MessageData:
    id: int
    seq: int
    role: str
    content: str
    meta: dict[str, Any]
    created_at: datetime


@dataclass(frozen=True)
class ItineraryData:
    version: int
    body: dict[str, Any]


@dataclass(frozen=True)
class ThreadData:
    messages: list[MessageData]
    itinerary: ItineraryData | None
    profile: ProfileData
    pending: dict[str, Any] | None


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def commit(self) -> None:
        """応答で公開するユーザー状態を現在のトランザクションで確定する。"""

        await self.session.commit()

    async def find_by_token(self, token: str) -> UserData | None:
        user = await self.session.scalar(select(User).where(User.api_token == token))
        return _user_data(user) if user is not None else None

    async def find_by_name(self, user_name: str) -> UserData | None:
        user = await self.session.scalar(select(User).where(User.user_name == user_name))
        return _user_data(user) if user is not None else None

    async def create_or_get(self, user_name: str, api_token: str) -> tuple[UserData, bool]:
        """ユーザーを競合安全に作り、既存なら永続トークンを保つ。"""

        user_id = await self.session.scalar(
            pg_insert(User)
            .values(user_name=user_name, api_token=api_token)
            .on_conflict_do_nothing()
            .returning(User.id)
        )
        if user_id is None:
            existing = await self.find_by_name(user_name)
            if existing is None:
                raise TokenCollisionError("api_token が既存ユーザーと衝突しました")
            await self.ensure_context(existing.id)
            return existing, False

        await self.ensure_context(int(user_id))
        created = await self.session.scalar(select(User).where(User.id == user_id))
        if created is None:  # pragma: no cover - INSERT ... RETURNING 後の防御
            raise UserContextError("作成したユーザーを読み戻せませんでした")
        return _user_data(created), True

    async def ensure_context(self, user_id: int) -> None:
        """1 ユーザー 1 thread/profile を冪等に用意する。"""

        await self.session.execute(
            pg_insert(Thread)
            .values(user_id=user_id)
            .on_conflict_do_nothing(index_elements=[Thread.user_id])
        )
        await self.session.execute(
            pg_insert(Profile)
            .values(user_id=user_id)
            .on_conflict_do_nothing(index_elements=[Profile.user_id])
        )

    async def get_profile(self, user_id: int) -> ProfileData:
        await self.ensure_context(user_id)
        return await self._read_profile(user_id)

    async def get_thread(self, user_id: int) -> ThreadData:
        await self.ensure_context(user_id)
        thread = await self.session.scalar(select(Thread).where(Thread.user_id == user_id))
        if thread is None:  # pragma: no cover - ensure_context 後の防御
            raise UserContextError("thread を読み戻せませんでした")

        message_rows = (
            await self.session.scalars(
                select(Message).where(Message.thread_id == thread.id).order_by(Message.seq)
            )
        ).all()
        current_itinerary = await self.session.scalar(
            select(Itinerary).where(
                Itinerary.user_id == user_id,
                Itinerary.is_current.is_(True),
            )
        )
        profile = await self._read_profile(user_id)
        pending = _public_pending(thread.pending_ask)
        return ThreadData(
            messages=[_message_data(message) for message in message_rows],
            itinerary=(
                ItineraryData(version=current_itinerary.version, body=current_itinerary.body)
                if current_itinerary is not None
                else None
            ),
            profile=profile,
            pending=pending,
        )

    async def _read_profile(self, user_id: int) -> ProfileData:
        profile = await self.session.scalar(select(Profile).where(Profile.user_id == user_id))
        if profile is None:  # pragma: no cover - ensure_context 後の防御
            raise UserContextError("profile を読み戻せませんでした")
        return _profile_data(profile)


def _user_data(user: User) -> UserData:
    return UserData(
        id=user.id,
        user_name=user.user_name,
        api_token=user.api_token,
        lora_device_id=user.lora_device_id,
        created_at=user.created_at,
        updated_at=user.updated_at,
    )


def _profile_data(profile: Profile) -> ProfileData:
    return ProfileData(
        user_id=profile.user_id,
        interests=dict(profile.interests),
        party=profile.party,
        mobility=profile.mobility,
        pace=profile.pace,
        avoid=list(profile.avoid),
        liked_spots=list(profile.liked_spots),
        rejected_spots=list(profile.rejected_spots),
        notes=profile.notes,
        updated_at=profile.updated_at,
    )


def _public_pending(value: dict[str, Any] | None) -> dict[str, Any] | None:
    """pending_ask を既存の SSE/UI kind と options 形へ写す。"""

    if not value:
        return None
    kind = value.get("kind")
    reason = value.get("reason")
    options = value.get("options")
    if not isinstance(options, list):
        return None
    if kind == "preference":
        slot = value.get("slot")
        if not isinstance(slot, str):
            return None
        labels = [
            option.get("label")
            for option in options
            if isinstance(option, dict) and isinstance(option.get("label"), str)
        ]
        return {
            "kind": "ask_user",
            "slot": slot,
            "reason": reason,
            "options": labels,
        }
    if kind == "clarify":
        surface = value.get("surface")
        if not isinstance(surface, str):
            return None
        public_options = [
            {"label": option.get("label"), "value": option.get("value")}
            for option in options
            if isinstance(option, dict)
            and isinstance(option.get("label"), str)
            and isinstance(option.get("value"), str)
        ]
        return {
            "kind": "clarify",
            "surface": surface,
            "reason": reason,
            "options": public_options,
        }
    return None


def _message_data(message: Message) -> MessageData:
    return MessageData(
        id=message.id,
        seq=message.seq,
        role=message.role,
        content=message.content,
        meta=dict(message.meta),
        created_at=message.created_at,
    )
