"""スレッド永続状態(`_persist_thread`)・assistant `meta`(§4.4)の層1仕様。

`ask_user` はメインループ(段5)からもレコメンド SA・知識検索 SA からも呼ばれ
うる。assistant `meta` は data_model.md §4.4 の契約
(`mode`/`presented`/`tools`/`itinerary_version`/`degraded`)に沿うことを検査
する(2026-08-04、レビュー是正・裁定14)。
"""

from typing import cast

from sqlalchemy.ext.asyncio import AsyncSession

from app.db_models import Thread
from app.domains.conversation.repository import (
    ConversationRepository,
    _assistant_meta,
    _derive_mode,
    _presented_candidates,
    _result_itinerary,
)
from app.domains.conversation.state import (
    CandidateReference,
    DegradedState,
    ItineraryState,
    ProfileState,
    SpotFact,
    TurnState,
)
from app.domains.conversation.types import ToolName, ToolResult
from app.domains.itinerary.types import Itinerary


def _thread(*, asked_slots: list[str] | None = None) -> Thread:
    return Thread(
        user_id=1,
        presented_spot_ids=[],
        last_candidates=[],
        asked_slots=asked_slots or ["pace"],
        pending_ask={
            "kind": "clarify",
            "surface": "2番目",
            "reason": "候補が複数あります",
            "options": [],
        },
        resolved_ambiguities=[],
        pending_constraints=[],
    )


def _state(**overrides: object) -> TurnState:
    base: dict[str, object] = dict(
        turn_id="turn",
        thread_id=1,
        user_id=1,
        utterance="鶴間池",
        profile=ProfileState(),
    )
    base.update(overrides)
    return TurnState(**base)


def test_persist_thread_always_clears_pending_ask() -> None:
    """段2は ask_user を呼ばないため、質問中の状態を持ち越さない。

    `ask_streak`(旧 A2 用のカウンタ)は 2026-08-06、ADR-0024 で廃止した。
    """

    row = _thread()
    state = _state()
    state.asked_slots = ["pace"]  # load_context で DB から読み込んだ値を模す。
    repository = ConversationRepository(cast(AsyncSession, None))

    repository._persist_thread(row, state, asked_at_message_id=87)

    assert row.pending_ask is None
    # asked_slots(段5で使う)は選好スロットのみ永続する(M-3。"pace" は
    # 選好スロットなのでそのまま残る)。
    assert row.asked_slots == ["pace"]


def test_persist_thread_asked_slots_persists_preference_slots_only() -> None:
    """M-3(2026-08-06 レビュー是正、ADR-0024): `asked_slots` に永続するのは

    選好スロット(onboarding/party/mobility/pace/interests)だけ。
    `dates`/`origin` はターン内だけの照合で、次ターンには持ち越さない
    (旅程ごとに変わる情報をスレッド生涯で封じると、「別の日程でもう一本」
    で日付を聞けなくなる)。
    """

    row = _thread(asked_slots=[])
    state = _state()
    state.asked_slots = ["dates", "party", "origin", "mobility", "onboarding"]
    repository = ConversationRepository(cast(AsyncSession, None))

    repository._persist_thread(row, state)

    assert sorted(row.asked_slots) == sorted(["party", "mobility", "onboarding"])
    assert "dates" not in row.asked_slots
    assert "origin" not in row.asked_slots


def test_persist_thread_updates_presented_spot_ids_and_last_candidates() -> None:
    row = _thread()
    state = _state()
    state.presented_spot_ids = ["spot_001", "spot_001", "spot_002"]
    repository = ConversationRepository(cast(AsyncSession, None))

    repository._persist_thread(row, state)

    assert row.presented_spot_ids == ["spot_001", "spot_002"]


def _ask_user_result(*, surface: str | None = None, slot: str | None = None) -> ToolResult:
    data: dict[str, object] = {"answer": "はい", "answered_by": "chip"}
    if surface is not None:
        data["surface"] = surface
    if slot is not None:
        data["slot"] = slot
    return ToolResult(step_id=1, tool=ToolName.ASK_USER, data=data)


def test_derive_mode_maps_plan_and_edit_to_itinerary() -> None:
    """裁定14: data_model.md §4.4 の契約(plan/edit→itinerary)に合わせる。"""

    state = _state()
    assert _derive_mode(state, ["plan_itinerary"]) == "itinerary"
    assert _derive_mode(state, ["recommend", "edit_itinerary"]) == "itinerary"
    assert _derive_mode(state, ["recommend"]) == "recommend"
    assert _derive_mode(state, ["search_knowledge"]) == "qa"
    assert _derive_mode(state, []) == "chitchat"


def test_derive_mode_distinguishes_ask_user_and_clarify_by_kind() -> None:
    preference_state = _state()
    preference_state.step_results[1] = _ask_user_result(slot="mobility")
    assert _derive_mode(preference_state, ["ask_user"]) == "ask_user"

    clarify_state = _state()
    clarify_state.step_results[1] = _ask_user_result(surface="2番目のやつ")
    assert _derive_mode(clarify_state, ["ask_user"]) == "clarify"


def test_derive_mode_is_error_when_main_agent_failed() -> None:
    state = _state()
    state.main_agent_failed = True
    assert _derive_mode(state, []) == "error"
    assert _derive_mode(state, ["recommend"]) == "error"  # 失敗が最優先


def _spots() -> dict[str, SpotFact]:
    return {
        "spot_001": SpotFact(spot_id="spot_001", name_ja="鶴間池", kind="poi"),
        "spot_002": SpotFact(spot_id="spot_002", name_ja="元滝伏流水", kind="poi"),
    }


def test_presented_candidates_empty_unless_recommend_ran_this_turn() -> None:
    """裁定14: `state.last_candidates` フォールバックを廃止した。

    ロード済みの(過去ターンの)候補が残っていても、今回 `recommend` を
    実行していなければ `presented` は空にする。
    """

    stale_state = _state(spot_catalog=_spots())
    stale_state.last_candidates = [
        CandidateReference(spot_id="spot_001", name_ja="鶴間池", rank=1)
    ]
    assert _presented_candidates(stale_state) == []

    fresh_state = _state(spot_catalog=_spots())
    fresh_state.last_candidates = [
        CandidateReference(spot_id="spot_002", name_ja="元滝伏流水", rank=1)
    ]
    fresh_state.step_results[1] = ToolResult(
        step_id=1,
        tool=ToolName.RECOMMEND,
        data={
            "spot_ids": ["spot_002"],
            "candidates": [],
            "provisional_spot_ids": [],
            "rerank_used": True,
        },
    )
    assert _presented_candidates(fresh_state) == fresh_state.last_candidates


def test_result_itinerary_none_unless_itinerary_tool_ran_this_turn() -> None:
    """裁定14: `state.itinerary`(現在旅程)へのフォールバックを廃止した。"""

    itinerary_payload = {
        "days": [
            {
                "date": "2026-08-10",
                "start_min": 540,
                "end_min": 1020,
                "origin": {"kind": "spot", "spot_id": "spot_001"},
                "destination": {"kind": "spot", "spot_id": "spot_001"},
                "items": [],
            }
        ],
        "concessions": [],
        "version": 3,
    }

    state_with_current_itinerary = _state(
        itinerary=ItineraryState(
            itinerary=Itinerary.model_validate(itinerary_payload), constraints=[]
        )
    )
    assert _result_itinerary(state_with_current_itinerary) is None

    state_with_tool_run = _state()
    state_with_tool_run.step_results[1] = ToolResult(
        step_id=1,
        tool=ToolName.PLAN_ITINERARY,
        data={"itinerary": itinerary_payload},
    )
    result = _result_itinerary(state_with_tool_run)
    assert result is not None
    assert result.version == 3


def test_assistant_meta_matches_data_model_contract() -> None:
    state = _state(spot_catalog=_spots(), spot_names={"spot_001": "鶴間池"})
    state.last_candidates = [CandidateReference(spot_id="spot_001", name_ja="鶴間池", rank=1)]
    state.step_results[1] = ToolResult(
        step_id=1,
        tool=ToolName.RECOMMEND,
        data={
            "spot_ids": ["spot_001"],
            "candidates": [],
            "provisional_spot_ids": [],
            "rerank_used": True,
        },
    )
    state.degraded = [DegradedState(code="rerank_degraded", stage="recommend", message="縮退")]

    meta = _assistant_meta(state)

    assert meta["mode"] == "recommend"
    assert meta["tools"] == ["recommend"]
    assert meta["presented"] == [{"rank": 1, "spot_id": "spot_001", "name_ja": "鶴間池"}]
    # 旧フィールド名は presented と同期した互換値のまま残す
    # (フロント chat.js の復元・history.py の機械要約が読む)。
    assert meta["candidate_spot_ids"] == ["spot_001"]
    assert meta["candidate_names"] == ["鶴間池"]
    assert meta["itinerary_version"] is None
    assert meta["degraded"] == ["rerank_degraded"]
