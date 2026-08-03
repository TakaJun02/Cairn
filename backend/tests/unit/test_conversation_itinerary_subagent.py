"""旅程計画サブエージェント(段4・完全ワークフロー)の層1仕様。

`Docs/30_design/agent_react_architecture.md` §5 が仕様。フロー1〜4を
`run_plan_itinerary`/`run_edit_itinerary` に直接与え、既存 constraints
(現行 version から継承)と add/remove のマージ・revert 特例(他 op との
混在を落として報告)・フロー4の整形(曖昧・落とした要素・notes の伝播)を
検査する。実 LLM は叩かない(Tool はスクリプト化した Fake)。
"""

from __future__ import annotations

from typing import Any

from app.domains.conversation.itinerary_subagent import run_edit_itinerary, run_plan_itinerary
from app.domains.conversation.state import ItineraryState, ProfileState, SpotFact, TurnState
from app.domains.conversation.types import ToolError, ToolErrorCode, ToolName, ToolResult
from app.domains.itinerary.types import Itinerary


class FakeItineraryTools:
    """`ConversationToolPort.plan_itinerary`/`edit_itinerary` だけを満たす。"""

    def __init__(self) -> None:
        self.plan_queue: list[Any] = []
        self.edit_queue: list[Any] = []
        self.plan_calls: list[dict[str, Any]] = []
        self.edit_calls: list[dict[str, Any]] = []

    async def plan_itinerary(self, *, step_id: int, user_id: int, args: Any, **kwargs: Any) -> Any:
        self.plan_calls.append({"step_id": step_id, "user_id": user_id, "args": args, **kwargs})
        return self.plan_queue.pop(0)

    async def edit_itinerary(self, *, step_id: int, user_id: int, args: Any, **kwargs: Any) -> Any:
        self.edit_calls.append({"step_id": step_id, "user_id": user_id, "args": args, **kwargs})
        return self.edit_queue.pop(0)

    async def recommend(self, **kwargs: Any) -> Any:  # pragma: no cover
        raise AssertionError("このテストでは呼ばれません")

    async def search_knowledge(self, **kwargs: Any) -> Any:  # pragma: no cover
        raise AssertionError("このテストでは呼ばれません")

    async def ask_user(self, **kwargs: Any) -> Any:  # pragma: no cover
        raise AssertionError("このテストでは呼ばれません")


def _spots() -> dict[str, SpotFact]:
    return {
        "spot_origin": SpotFact(spot_id="spot_origin", name_ja="道の駅", kind="facility"),
        "spot_a": SpotFact(spot_id="spot_a", name_ja="地点エー", kind="poi"),
        "spot_b": SpotFact(spot_id="spot_b", name_ja="地点ビー", kind="poi"),
    }


def _itinerary_payload(version: int = 1, *, spot_ids: list[str] | None = None) -> dict[str, Any]:
    items = [
        {
            "seq": index + 1,
            "spot_id": spot_id,
            "arrive_min": 600 + index * 60,
            "stay_min": 30,
            "depart_min": 630 + index * 60,
            "leg_from_prev": {"mode": "car", "min": 10, "route_id": None},
        }
        for index, spot_id in enumerate(spot_ids or [])
    ]
    return {
        "days": [
            {
                "date": "2026-08-10",
                "start_min": 540,
                "end_min": 1020,
                "origin": {"kind": "spot", "spot_id": "spot_origin"},
                "destination": {"kind": "spot", "spot_id": "spot_origin"},
                "items": items,
            }
        ],
        "concessions": [],
        "version": version,
    }


def _plan_result(
    step_id: int = 1, *, version: int = 1, spot_ids: list[str] | None = None
) -> ToolResult:
    return ToolResult(
        step_id=step_id,
        tool=ToolName.PLAN_ITINERARY,
        data={
            "itinerary": _itinerary_payload(version, spot_ids=spot_ids),
            "alternatives": [],
            "concessions": [],
            "selection_used": False,
            "spot_ids": spot_ids or [],
            "unmodeled": [],
        },
    )


def _edit_result(
    step_id: int = 1, *, version: int = 2, spot_ids: list[str] | None = None
) -> ToolResult:
    return ToolResult(
        step_id=step_id,
        tool=ToolName.EDIT_ITINERARY,
        data={
            "itinerary": _itinerary_payload(version, spot_ids=spot_ids),
            "alternatives": [],
            "concessions": [],
            "selection_used": False,
            "spot_ids": spot_ids or [],
            "unmodeled": [],
            "diff": {"added": [], "removed": [], "moved": [], "retimed": []},
        },
    )


def _state(*, itinerary: ItineraryState | None = None) -> TurnState:
    spots = _spots()
    return TurnState(
        turn_id="turn",
        thread_id=1,
        user_id=7,
        utterance="旅程を作って",
        profile=ProfileState(),
        itinerary=itinerary,
        spot_id_vocab=list(spots),
        spot_names={key: value.name_ja for key, value in spots.items()},
        spot_catalog=spots,
        default_origin_spot_id="spot_origin",
    )


async def test_plan_itinerary_reports_ambiguous_must_visit_with_candidates() -> None:
    """フロー2: 曖昧な要素は落とし、フロー4の整形に候補つきで載る。"""

    spots = {
        "spot_origin": SpotFact(spot_id="spot_origin", name_ja="道の駅", kind="facility"),
        "spot_a": SpotFact(spot_id="spot_a", name_ja="湧水地点エー", kind="poi"),
        "spot_b": SpotFact(spot_id="spot_b", name_ja="湧水地点ビー", kind="poi"),
    }
    state = TurnState(
        turn_id="turn",
        thread_id=1,
        user_id=7,
        utterance="旅程を作って",
        profile=ProfileState(),
        spot_id_vocab=list(spots),
        spot_names={key: value.name_ja for key, value in spots.items()},
        spot_catalog=spots,
        default_origin_spot_id="spot_origin",
    )
    tools = FakeItineraryTools()
    tools.plan_queue = [_plan_result()]

    digest, error = await run_plan_itinerary(
        state,
        tools,
        {
            "days": [
                {
                    "date": "2026-08-10",
                    "start": "09:00",
                    "end": "17:00",
                    "origin_name": None,
                    "destination_name": None,
                }
            ],
            "must_visit": ["湧水"],
            "constraints": None,
            "notes": None,
        },
        step_id=1,
    )

    assert error is None
    assert tools.plan_calls[0]["args"].must_visit == []
    assert "曖昧だった項目" in digest
    assert "湧水地点エー" in digest and "湧水地点ビー" in digest


async def test_plan_itinerary_passes_notes_as_selection_hint() -> None:
    """フロー3: notes が解選択のヒントとして selection_text に渡る。"""

    state = _state()
    tools = FakeItineraryTools()
    tools.plan_queue = [_plan_result(spot_ids=["spot_a"])]

    await run_plan_itinerary(
        state,
        tools,
        {
            "days": [
                {
                    "date": "2026-08-10",
                    "start": "09:00",
                    "end": "17:00",
                    "origin_name": None,
                    "destination_name": None,
                }
            ],
            "must_visit": [],
            "constraints": None,
            "notes": "のんびり回りたい",
        },
        step_id=1,
    )

    call = tools.plan_calls[0]
    assert call["selection_text"] == "のんびり回りたい"
    assert call["use_specialist"] is True


def _existing_itinerary(*, constraints: list[dict[str, Any]]) -> ItineraryState:
    itinerary = Itinerary.model_validate(_itinerary_payload(1, spot_ids=["spot_a"]))
    return ItineraryState(itinerary=itinerary, constraints=constraints, parent_version=None)


async def test_edit_itinerary_merges_inherited_constraints_with_add_and_remove() -> None:
    """フロー2: 現行versionから継承する既存constraints + add/removeのマージ。"""

    existing = [
        {"id": "c_001", "pred": "require", "args": {"target": "spot_a"}, "weight": 1.0},
        {"id": "c_002", "pred": "exclude", "args": {"target": "spot_b"}, "weight": 1.0},
    ]
    state = _state(itinerary=_existing_itinerary(constraints=existing))
    tools = FakeItineraryTools()
    tools.edit_queue = [_edit_result(spot_ids=["spot_a"])]

    digest, error = await run_edit_itinerary(
        state,
        tools,
        {
            "ops": [{"op": "lock", "targets": ["地点エー"], "locked": True}],
            "constraints": {
                "add": [
                    {
                        "pred": "stay_at_least",
                        "args": {"target": "地点エー", "min": 60},
                        "weight": 1.0,
                        "source_text": "ゆっくりしたい",
                    }
                ],
                "remove": ["c_001"],
            },
            "notes": None,
        },
        step_id=1,
    )

    assert error is None
    call = tools.edit_calls[0]
    assert call["constraints_remove"] == ["c_001"]
    added_ids = {value.id for value in call["constraints"]}
    # c_001 は削除対象なので新規 id 採番に再利用されない(c_002 と衝突しない)。
    assert added_ids.isdisjoint({"c_001", "c_002"})
    assert call["constraints"][0].args["target"] == "spot_a"

    # フロー3実行後、会話状態側の表示コピーは c_001 を落とし c_002 を残す。
    kept_ids = {value.get("id") for value in state.itinerary.constraints}
    assert "c_001" not in kept_ids
    assert "c_002" in kept_ids
    assert len(kept_ids) == 2  # c_002 + 新規追加分


async def test_edit_itinerary_remove_targets_unknown_id_is_dropped_and_reported() -> None:
    existing = [{"id": "c_001", "pred": "require", "args": {"target": "spot_a"}, "weight": 1.0}]
    state = _state(itinerary=_existing_itinerary(constraints=existing))
    tools = FakeItineraryTools()
    tools.edit_queue = [_edit_result(spot_ids=["spot_a"])]

    digest, error = await run_edit_itinerary(
        state,
        tools,
        {
            "ops": [{"op": "lock", "targets": ["地点エー"], "locked": True}],
            "constraints": {"add": [], "remove": ["c_999"]},
            "notes": None,
        },
        step_id=1,
    )

    assert error is None
    assert tools.edit_calls[0]["constraints_remove"] == []
    assert "c_999" in digest


async def test_revert_mixed_with_other_ops_drops_others_and_reports() -> None:
    """フロー3: revert が他opと混在したら、revert だけを実行し混在を報告する。"""

    state = _state(itinerary=_existing_itinerary(constraints=[]))
    tools = FakeItineraryTools()
    tools.edit_queue = [_edit_result(version=1, spot_ids=["spot_a"])]

    digest, error = await run_edit_itinerary(
        state,
        tools,
        {
            "ops": [
                {"op": "add", "targets": ["地点ビー"], "day": None, "after": None},
                {"op": "revert"},
            ],
            "constraints": None,
            "notes": None,
        },
        step_id=1,
    )

    assert error is None
    call = tools.edit_calls[0]
    assert call["args"].ops == [{"op": "revert"}]
    assert "元に戻す操作" in digest


async def test_revert_alone_does_not_report_mixed_ops() -> None:
    state = _state(itinerary=_existing_itinerary(constraints=[]))
    tools = FakeItineraryTools()
    tools.edit_queue = [_edit_result(version=1, spot_ids=["spot_a"])]

    digest, error = await run_edit_itinerary(
        state, tools, {"ops": [{"op": "revert"}], "constraints": None, "notes": None}, step_id=1
    )

    assert error is None
    assert tools.edit_calls[0]["args"].ops == [{"op": "revert"}]
    assert "元に戻す操作" not in digest


async def test_edit_itinerary_without_existing_itinerary_returns_precondition_error() -> None:
    state = _state(itinerary=None)
    tools = FakeItineraryTools()

    digest, error = await run_edit_itinerary(
        state, tools, {"ops": [{"op": "revert"}], "constraints": None, "notes": None}, step_id=1
    )

    assert error is not None
    assert error["code"] == ToolErrorCode.PRECONDITION_UNMET.value
    assert tools.edit_calls == []


async def test_plan_itinerary_returns_reference_unresolved_when_no_days() -> None:
    state = _state()
    tools = FakeItineraryTools()

    digest, error = await run_plan_itinerary(
        state, tools, {"days": [], "must_visit": [], "constraints": None, "notes": None}, step_id=1
    )

    assert error is not None
    assert error["code"] == ToolErrorCode.REFERENCE_UNRESOLVED.value
    assert tools.plan_calls == []


async def test_tool_error_from_edit_itinerary_is_returned_without_mutating_state() -> None:
    state = _state(itinerary=_existing_itinerary(constraints=[]))
    tools = FakeItineraryTools()
    tools.edit_queue = [
        ToolError(
            code=ToolErrorCode.INTERNAL,
            message_ja="物理的に整合する旅程を確定できませんでした。",
            recoverable=False,
        )
    ]
    original_version = state.itinerary.version

    digest, error = await run_edit_itinerary(
        state,
        tools,
        {"ops": [{"op": "revert"}], "constraints": None, "notes": None},
        step_id=1,
    )

    assert error is not None
    assert error["recoverable"] is False
    assert state.itinerary.version == original_version


async def test_two_edits_in_one_turn_thread_version_and_avoid_constraint_id_collision() -> None:
    """1ターンに複数回の旅程書き換え(既に段2で許可)が version 管理と整合する。

    2回目の edit_itinerary が1回目で追加した制約 id を `used_ids` として
    正しく引き継ぎ、新規採番が衝突しないことを検査する(§5)。
    """

    state = _state(itinerary=_existing_itinerary(constraints=[]))
    tools = FakeItineraryTools()
    tools.edit_queue = [
        _edit_result(step_id=1, version=2, spot_ids=["spot_a"]),
        _edit_result(step_id=2, version=3, spot_ids=["spot_a"]),
    ]

    _, error1 = await run_edit_itinerary(
        state,
        tools,
        {
            "ops": [{"op": "lock", "targets": ["地点エー"], "locked": True}],
            "constraints": {
                "add": [
                    {
                        "pred": "require",
                        "args": {"target": "地点エー"},
                        "weight": 1.0,
                        "source_text": "1回目",
                    }
                ],
                "remove": [],
            },
            "notes": None,
        },
        step_id=1,
    )
    assert error1 is None
    assert state.itinerary.version == 2
    first_ids = {value.get("id") for value in state.itinerary.constraints}
    assert len(first_ids) == 1

    _, error2 = await run_edit_itinerary(
        state,
        tools,
        {
            "ops": [{"op": "lock", "targets": ["地点ビー"], "locked": True}],
            "constraints": {
                "add": [
                    {
                        "pred": "require",
                        "args": {"target": "地点ビー"},
                        "weight": 1.0,
                        "source_text": "2回目",
                    }
                ],
                "remove": [],
            },
            "notes": None,
        },
        step_id=2,
    )

    assert error2 is None
    assert state.itinerary.version == 3
    all_ids = [value.get("id") for value in state.itinerary.constraints]
    # 2回目の追加分は1回目の id と衝突しない(id の重複が無い)。
    assert len(all_ids) == len(set(all_ids)) == 2
    assert first_ids.issubset(set(all_ids))
