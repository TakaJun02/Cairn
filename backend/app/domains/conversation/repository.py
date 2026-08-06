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
from app.domains.conversation.history_summary import HistorySummaryState
from app.domains.conversation.itinerary_digest import UNNAMED_SPOT_JA
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
from app.domains.conversation.types import Slot
from app.domains.itinerary.types import Itinerary

# A1 の永続範囲(2026-08-06 レビュー是正 M-3、ADR-0024): `asked_slots` に
# スレッド生涯で永続するのは選好スロットだけ。`dates`/`origin` は旅程ごとに
# 変わる情報なので、次ターンへ持ち越すと「別の日程でもう一本」で日付を
# 二度と聞けなくなる(本 ADR が禁じた仮定進行に逆戻りする)。ターン内での
# 同一ターン再質問防止(A1)は `state.asked_slots`(メモリ上)がそのまま
# 担うため、`evaluate_ask_user` 側の変更は不要。
_PERSISTENT_ASK_SLOTS = frozenset(
    {
        Slot.ONBOARDING.value,
        Slot.PARTY.value,
        Slot.MOBILITY.value,
        Slot.PACE.value,
        Slot.INTERESTS.value,
    }
)


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
            history_summary=thread.history_summary,
            summarized_until_message_id=thread.summarized_until_message_id,
        )

    async def load_history_summary_state(self, thread_id: int) -> HistorySummaryState:
        """要約更新（history_summary.py）が読む、永続化済みの現在状態。"""

        thread = await self.session.scalar(select(Thread).where(Thread.id == thread_id))
        if thread is None:
            raise ConversationStateError(f"thread が見つかりません: {thread_id}")
        message_rows = (
            await self.session.scalars(
                select(Message).where(Message.thread_id == thread_id).order_by(Message.seq)
            )
        ).all()
        return HistorySummaryState(
            history_summary=thread.history_summary,
            summarized_until_message_id=thread.summarized_until_message_id,
            messages=[_message_state(value) for value in message_rows],
        )

    async def commit_history_summary(
        self,
        thread_id: int,
        *,
        summary: str,
        summarized_until_message_id: int,
    ) -> None:
        """本体トランザクションとは別に、要約列だけを更新する。"""

        await self.session.execute(
            update(Thread)
            .where(Thread.id == thread_id)
            .values(
                history_summary=summary,
                summarized_until_message_id=summarized_until_message_id,
            )
        )
        await self.session.commit()

    async def persist_turn(self, state: TurnState) -> int | None:
        """全 Tool の未コミット変更とメッセージを 1 回だけ commit する。"""

        try:
            # `Thread` 行は FOR UPDATE しない(2026-08-04、レビュー是正: Critical)。
            # ターン中に `ask_user` が実行されると `write_pending_ask_now` が
            # 別セッションで同じ `threads` 行を UPDATE する(§7)。ここで行
            # ロックを取ると、ターン自身が後で待つ相手(pending_ask の書き込み)
            # を自分でブロックする自己デッドロックになりうる。同時実行は
            # `ActiveTurnRegistry` の 409 が既に防いでいるため、ターンの
            # 主セッションが `threads` 行を占有し続ける理由はない。
            thread = await self.session.scalar(
                select(Thread).where(
                    Thread.id == state.thread_id, Thread.user_id == state.user_id
                )
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

            # `ask_user` への回答(§7)。書き込みはこの一括 persist でよい
            # (data_model.md §4.4: user 行として質問とペアの meta を持つ)。
            seq_cursor = next_seq
            for qa in state.qa_answers:
                seq_cursor += 1
                self.session.add(
                    Message(
                        thread_id=state.thread_id,
                        seq=seq_cursor,
                        role="user",
                        content=str(qa.get("answer", "")),
                        status="complete",
                        meta={"turn_id": state.turn_id, **qa.get("meta", {})},
                    )
                )
            if state.qa_answers:
                await self.session.flush()

            assistant_message: Message | None = None
            if state.responded:
                seq_cursor += 1
                assistant_message = Message(
                    thread_id=state.thread_id,
                    seq=seq_cursor,
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
        # 未使用: pending_ask は ask_registry がターン内で即時反映/クリア済み。
        del asked_at_message_id
        thread.presented_spot_ids = list(dict.fromkeys(state.presented_spot_ids))
        thread.last_candidates = [
            value.model_dump(mode="json") for value in state.last_candidates
        ]
        # A1: 質問済み slot。永続するのは選好スロットのみ(2026-08-06
        # レビュー是正 M-3、ADR-0024)。`dates`/`origin` はこのターンの
        # `state.asked_slots`(メモリ上)でだけ照合され、次ターンには
        # 持ち越さない。A5: 同じ曖昧さを 2 回聞かない。
        thread.asked_slots = list(
            dict.fromkeys(
                slot for slot in state.asked_slots if slot in _PERSISTENT_ASK_SLOTS
            )
        )
        thread.resolved_ambiguities = list(state.resolved_ambiguities)
        # `pending_ask` はターンの処理が回答を待っている間だけの表示状態
        # (§7)。`ToolAdapters.ask_user` が別トランザクションで即時
        # 書き込み/クリア済みなので、ここでは念のため NULL を保証するだけ。
        thread.pending_ask = None
        # `pending_constraints`(旅程がまだ無いターンの制約の一時保持)は、
        # ReAct 化により constraints が常に plan_itinerary/edit_itinerary の
        # 引数として Tool 呼び出しと一緒に来るようになったため、
        # 会話状態側で明示的に積む経路が無くなった。`plan_itinerary` 成功時に
        # Tool 側(`ItineraryService.clear_pending_constraints`)が同一
        # session 内で既に消し込み済みなので、ここでは触らない。


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
    """data_model.md §4.4 の assistant `meta` 契約を組み立てる。

    2026-08-04 レビュー是正(High・裁定14): 旧実装は `mode="plan"/"edit"` を
    使い、`candidate_spot_ids`/`candidate_names` は `recommend` を実行して
    いないターンでもロード済み `state.last_candidates` を、
    `itinerary_version` も Tool 結果が無ければ現在旅程を、それぞれ
    フォールバックとして書いていた。これにより過去に提示した候補・旅程が
    後続の全 assistant 発話へ「今回提示した」ものとして再記録され続け、
    直近3リストの機械要約(§8)や序数照応を汚染していた。
    `presented`/`itinerary_version` は**このターンで実際に Tool を実行した
    ときだけ**書く(フォールバック廃止)。`ask_user` も `step_results` へ
    登録されるため(`ask_execution.execute_ask_user`)、`tools`/`mode` に
    反映される。
    """

    presented = _presented_candidates(state)
    itinerary = _result_itinerary(state)
    itinerary_ids = _itinerary_ids(itinerary) if itinerary is not None else []
    qa_spot_id = _qa_spot_id(state)
    tools = [result.tool.value for result in state.step_results.values()]
    return {
        "turn_id": state.turn_id,
        # mode: recommend | itinerary | qa | clarify | ask_user | chitchat
        #     | error(data_model.md §4.4)。
        "mode": _derive_mode(state, tools),
        "tools": tools,
        "presented": [value.model_dump(mode="json") for value in presented],
        # 旧フィールド名との互換(フロント chat.js の復元・history.py の
        # 機械要約が読む)。中身は `presented` と常に同期させる。
        "candidate_spot_ids": [value.spot_id for value in presented],
        "candidate_names": [value.name_ja for value in presented],
        "itinerary_version": itinerary.version if itinerary is not None else None,
        "itinerary_spot_ids": itinerary_ids,
        "itinerary_spot_names": [
            state.spot_names.get(spot_id, UNNAMED_SPOT_JA) for spot_id in itinerary_ids
        ],
        "qa_spot_id": qa_spot_id,
        "qa_spot_name": state.spot_names.get(qa_spot_id or "") if qa_spot_id else None,
        "degraded": [value.code for value in state.degraded],
    }


def _presented_candidates(state: TurnState) -> list[CandidateReference]:
    """このターンで `recommend` が実行されたときだけ、順序つき候補を返す。

    `state.last_candidates` フォールバックは廃止した(裁定14)。
    """

    if not any(result.tool.value == "recommend" for result in state.step_results.values()):
        return []
    return list(state.last_candidates)


def _derive_mode(state: TurnState, tools: list[str]) -> str:
    """実行した Tool 列から assistant `meta.mode` を導出する(§4.4)。"""

    if state.main_agent_failed:
        return "error"
    if "plan_itinerary" in tools or "edit_itinerary" in tools:
        return "itinerary"
    if "recommend" in tools:
        return "recommend"
    if "search_knowledge" in tools:
        return "qa"
    if "ask_user" in tools:
        return _ask_user_mode(state)
    return "chitchat"


def _ask_user_mode(state: TurnState) -> str:
    """直近の `ask_user` 結果の `kind` から `ask_user`/`clarify` を選ぶ。"""

    for result in reversed(list(state.step_results.values())):
        if result.tool.value == "ask_user":
            if result.data.get("surface") is not None:
                return "clarify"
            return "ask_user"
    return "ask_user"  # pragma: no cover - tools に ask_user がある前提の防御


def _result_itinerary(state: TurnState) -> Itinerary | None:
    """このターンで `plan_itinerary`/`edit_itinerary` が作った版だけを返す。

    `state.itinerary`(現在旅程)へのフォールバックは廃止した(裁定14)。
    """

    for result in reversed(list(state.step_results.values())):
        raw = result.data.get("itinerary")
        if isinstance(raw, dict):
            return Itinerary.model_validate(raw)
    return None


def _itinerary_ids(itinerary: Itinerary) -> list[str]:
    return [item.spot_id for day in itinerary.days for item in day.items]


def _qa_spot_id(state: TurnState) -> str | None:
    for result in state.step_results.values():
        if result.tool.value == "search_knowledge":
            value = result.data.get("spot_id")
            if isinstance(value, str):
                return value
    return None


