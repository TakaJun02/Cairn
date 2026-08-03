"""conversation のスレッド状態を読み書きする唯一の SQLAlchemy 実装。"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db_models import Itinerary as ItineraryRow
from app.db_models import (
    Message,
    Profile,
    Spot,
    SpotRealtime,
    TagVocabulary,
    Thread,
    User,
)
from app.domains.conversation.state import (
    CandidateReference,
    ContextSnapshot,
    ConversationStateError,
    ItineraryState,
    MessageState,
    ProfileState,
    SpotFact,
    TurnState,
)
from app.domains.itinerary.types import Itinerary


class ConversationRepository:
    """呼び出し側の 1 session を Tool と N6 で共有し、N6 だけが commit する。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def load_snapshot(self, user_id: int) -> ContextSnapshot:
        row = (
            await self.session.execute(
                select(User, Thread, Profile)
                .join(Thread, Thread.user_id == User.id)
                .join(Profile, Profile.user_id == User.id)
                .where(User.id == user_id)
            )
        ).one_or_none()
        if row is None:
            raise ConversationStateError(f"ユーザー状態が見つかりません: {user_id}")
        _, thread, profile = row
        message_rows = (
            await self.session.scalars(
                select(Message)
                .where(Message.thread_id == thread.id)
                .order_by(Message.seq)
            )
        ).all()
        itinerary_row = await self.session.scalar(
            select(ItineraryRow).where(
                ItineraryRow.user_id == user_id,
                ItineraryRow.is_current.is_(True),
            )
        )
        spot_rows = (
            await self.session.execute(
                select(Spot, SpotRealtime)
                .outerjoin(SpotRealtime, SpotRealtime.spot_id == Spot.spot_id)
                .order_by(Spot.spot_id)
            )
        ).all()
        tag_vocabulary = list(
            (
                await self.session.scalars(
                    select(TagVocabulary.tag).order_by(TagVocabulary.tag)
                )
            ).all()
        )
        spots = {
            spot.spot_id: SpotFact(
                spot_id=spot.spot_id,
                name_ja=spot.name_ja,
                kind=spot.kind,
                aliases_ja=list(spot.aliases_ja),
                tags_ja=list(spot.tags_ja),
            )
            for spot, _ in spot_rows
        }
        return ContextSnapshot(
            thread_id=thread.id,
            profile=ProfileState(
                interests=dict(profile.interests),
                party=profile.party,
                mobility=profile.mobility,
                pace=profile.pace,
                avoid=list(profile.avoid),
                liked_spots=list(profile.liked_spots),
                rejected_spots=deepcopy(list(profile.rejected_spots)),
                notes=profile.notes,
            ),
            itinerary=_itinerary_state(itinerary_row),
            messages=[_message_state(value) for value in message_rows],
            last_candidates=_candidate_references(thread.last_candidates, spots),
            presented_spot_ids=list(thread.presented_spot_ids),
            asked_slots=list(thread.asked_slots),
            ask_streak=int(thread.ask_streak),
            pending_ask=(
                deepcopy(dict(thread.pending_ask))
                if thread.pending_ask
                else None
            ),
            resolved_ambiguities=deepcopy(list(thread.resolved_ambiguities)),
            pending_constraints=deepcopy(list(thread.pending_constraints)),
            realtime={
                spot.spot_id: {
                    "weather": value.weather if value is not None else None,
                    "congestion": value.congestion if value is not None else None,
                }
                for spot, value in spot_rows
            },
            spots=spots,
            tag_vocabulary=tag_vocabulary,
        )

    async def persist_turn(self, state: TurnState) -> int | None:
        """全 Tool の未コミット変更とメッセージを 1 回だけ commit する。"""

        try:
            thread = await self.session.scalar(
                select(Thread)
                .where(Thread.id == state.thread_id, Thread.user_id == state.user_id)
                .with_for_update()
            )
            profile = await self.session.scalar(
                select(Profile).where(Profile.user_id == state.user_id).with_for_update()
            )
            if thread is None or profile is None:
                raise ConversationStateError("persist 対象の thread/profile がありません")

            next_seq = int(
                await self.session.scalar(
                    select(func.coalesce(func.max(Message.seq), 0) + 1).where(
                        Message.thread_id == state.thread_id
                    )
                )
                or 1
            )
            user_message = Message(
                thread_id=state.thread_id,
                seq=next_seq,
                role="user",
                content=state.utterance,
                status="complete",
                meta={"turn_id": state.turn_id},
            )
            self.session.add(user_message)
            await self.session.flush()

            assistant_message: Message | None = None
            if not state.understand_failed:
                assistant_message = Message(
                    thread_id=state.thread_id,
                    seq=next_seq + 1,
                    role="assistant",
                    content=state.assistant_text,
                    status=state.respond_status,
                    meta=_assistant_meta(state),
                )
                self.session.add(assistant_message)
                await self.session.flush()

            self._persist_thread(thread, state, asked_at_message_id=user_message.id)
            _persist_profile(profile, state.profile)
            # 旅程 Tool は同じ session に新しい版を flush 済み。
            # 発話 ID をここで結ぶ。
            initial_version = state.log_fields.get("initial_itinerary_version")
            if isinstance(initial_version, int):
                await self.session.execute(
                    update(ItineraryRow)
                    .where(
                        ItineraryRow.user_id == state.user_id,
                        ItineraryRow.version > initial_version,
                        ItineraryRow.created_by_message_id.is_(None),
                    )
                    .values(created_by_message_id=user_message.id)
                )

            await self.session.commit()
            return assistant_message.id if assistant_message is not None else None
        except BaseException:
            await self.session.rollback()
            raise

    def _persist_thread(
        self,
        thread: Thread,
        state: TurnState,
        *,
        asked_at_message_id: int | None = None,
    ) -> None:
        thread.presented_spot_ids = list(dict.fromkeys(state.presented_spot_ids))
        thread.last_candidates = [
            value.model_dump(mode="json") for value in state.last_candidates
        ]
        if state.should_end_turn and state.pending_ask is not None:
            slot = state.pending_ask.get("slot")
            if state.pending_ask.get("kind") == "preference" and isinstance(slot, str):
                thread.asked_slots = list(dict.fromkeys([*state.asked_slots, slot]))
            else:
                thread.asked_slots = list(state.asked_slots)
            thread.ask_streak = state.ask_streak + 1
        else:
            thread.asked_slots = list(state.asked_slots)
            thread.ask_streak = 0

        if state.pending_ask is not None:
            thread.pending_ask = {
                **deepcopy(state.pending_ask),
                "original_utterance": state.utterance,
                "asked_at_message_id": asked_at_message_id,
            }
        else:
            # 前ターンの pending は、別の話題が来た場合もここで必ず消す。
            thread.pending_ask = None
        _record_resolved_asks(thread, state)

        initial_version = state.log_fields.get("initial_itinerary_version", 0)
        successful_plan = any(
            result.tool.value == "plan_itinerary" for result in state.step_results.values()
        )
        if initial_version == 0 and state.constraints and not successful_plan:
            existing = deepcopy(list(thread.pending_constraints))
            existing.extend(
                value.model_dump(mode="json", exclude_none=True)
                for value in state.constraints
            )
            thread.pending_constraints = _deduplicate_constraints(existing)


def _message_state(value: Message) -> MessageState:
    return MessageState(
        id=value.id,
        seq=value.seq,
        role=value.role,
        content=value.content,
        status=value.status,
        meta=deepcopy(dict(value.meta)),
        created_at=value.created_at,
    )


def _itinerary_state(value: ItineraryRow | None) -> ItineraryState | None:
    if value is None:
        return None
    body = deepcopy(dict(value.body))
    body["version"] = value.version
    return ItineraryState(
        itinerary=Itinerary.model_validate(body),
        constraints=deepcopy(list(value.constraints)),
        parent_version=value.parent_version,
    )


def _candidate_references(
    values: list[Any], spots: dict[str, SpotFact]
) -> list[CandidateReference]:
    result: list[CandidateReference] = []
    for index, raw in enumerate(values, 1):
        if isinstance(raw, str):
            spot_id = raw
            rank = index
            name = spots.get(spot_id).name_ja if spot_id in spots else spot_id
        elif isinstance(raw, dict):
            spot_id = raw.get("spot_id")
            if not isinstance(spot_id, str):
                continue
            rank = raw.get("rank", index)
            name = raw.get("name_ja")
            if not isinstance(name, str):
                name = spots.get(spot_id).name_ja if spot_id in spots else spot_id
        else:
            continue
        if spot_id in spots and not any(value.spot_id == spot_id for value in result):
            result.append(CandidateReference(spot_id=spot_id, name_ja=name, rank=int(rank)))
    return result


def _persist_profile(row: Profile, value: ProfileState) -> None:
    row.interests = dict(value.interests)
    row.party = value.party
    row.mobility = value.mobility
    row.pace = value.pace
    row.avoid = list(value.avoid)
    row.liked_spots = list(value.liked_spots)
    row.rejected_spots = deepcopy(value.rejected_spots)
    row.notes = value.notes


def _assistant_meta(state: TurnState) -> dict[str, Any]:
    candidate_ids = [value.spot_id for value in state.last_candidates]
    candidate_names = [value.name_ja for value in state.last_candidates]
    itinerary = _result_itinerary(state)
    itinerary_ids = _itinerary_ids(itinerary) if itinerary is not None else []
    qa_spot_id = _qa_spot_id(state)
    return {
        "turn_id": state.turn_id,
        "intent": state.intent.value if state.intent is not None else None,
        "tools": [result.tool.value for result in state.step_results.values()],
        "candidate_spot_ids": candidate_ids,
        "candidate_names": candidate_names,
        "itinerary_version": itinerary.version if itinerary is not None else None,
        "itinerary_spot_ids": itinerary_ids,
        "itinerary_spot_names": [
            state.spot_names.get(spot_id, spot_id) for spot_id in itinerary_ids
        ],
        "qa_spot_id": qa_spot_id,
        "qa_spot_name": state.spot_names.get(qa_spot_id or "") if qa_spot_id else None,
        "ask_slot": (
            state.pending_ask.get("slot")
            if state.pending_ask is not None
            and state.pending_ask.get("kind") == "preference"
            else None
        ),
        "clarify_surface": (
            state.pending_ask.get("surface")
            if state.pending_ask is not None
            and state.pending_ask.get("kind") == "clarify"
            else None
        ),
    }


def _result_itinerary(state: TurnState) -> Itinerary | None:
    for result in reversed(list(state.step_results.values())):
        raw = result.data.get("itinerary")
        if isinstance(raw, dict):
            return Itinerary.model_validate(raw)
    return state.itinerary.itinerary if state.itinerary is not None else None


def _itinerary_ids(itinerary: Itinerary) -> list[str]:
    return [item.spot_id for day in itinerary.days for item in day.items]


def _qa_spot_id(state: TurnState) -> str | None:
    for result in state.step_results.values():
        if result.tool.value == "search_knowledge":
            value = result.data.get("spot_id")
            if isinstance(value, str):
                return value
    return None


def _record_resolved_asks(thread: Thread, state: TurnState) -> None:
    existing = deepcopy(list(thread.resolved_ambiguities))
    known_surfaces = {
        str(value.get("surface"))
        for value in existing
        if isinstance(value, dict) and isinstance(value.get("surface"), str)
    }
    for result in state.tool_results:
        tool_input = result.get("input")
        output = result.get("output")
        if not isinstance(tool_input, dict) or not isinstance(output, dict):
            continue
        surface = tool_input.get("surface")
        answer = output.get("answer")
        if (
            tool_input.get("kind") != "clarify"
            or not isinstance(surface, str)
            or not surface
            or not isinstance(answer, str)
            or surface in known_surfaces
        ):
            continue
        existing.append({"surface": surface, "resolved_to": answer})
        known_surfaces.add(surface)
    thread.resolved_ambiguities = existing


def _deduplicate_constraints(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for value in values:
        key = (
            str(value.get("pred", "")),
            repr(value.get("args", {})),
            str(value.get("source_text", "")),
        )
        if key not in seen:
            seen.add(key)
            result.append(value)
    return result
