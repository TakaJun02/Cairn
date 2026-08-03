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
    step_id: int = 1,
    *,
    version: int = 1,
    spot_ids: list[str] | None = None,
    constraints: list[dict[str, Any]] | None = None,
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
            # Service が確定した制約(2026-08-04、レビュー是正・裁定7)。
            # 未指定なら「制約なし」として空のまま返す。
            "constraints": constraints if constraints is not None else [],
        },
    )


def _edit_result(
    step_id: int = 1,
    *,
    version: int = 2,
    spot_ids: list[str] | None = None,
    constraints: list[dict[str, Any]] | None = None,
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
            # Service が確定した制約(2026-08-04、レビュー是正・裁定7)。
            "constraints": constraints if constraints is not None else [],
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


async def test_edit_itinerary_resolves_names_and_adopts_the_tools_returned_constraints() -> None:
    """フロー2: 名前解決した制約案を渡し、Service が返した constraints を

    `state.itinerary.constraints` としてそのまま採用する(2026-08-04、
    レビュー是正・裁定7: 既存 constraints とのマージは Service の責務になり、
    conversation 側でのローカル再マージは廃止した)。
    """

    existing = [
        {"id": "c_001", "pred": "require", "args": {"target": "spot_a"}, "weight": 1.0},
        {"id": "c_002", "pred": "exclude", "args": {"target": "spot_b"}, "weight": 1.0},
    ]
    state = _state(itinerary=_existing_itinerary(constraints=existing))
    tools = FakeItineraryTools()
    # Service が実際に返すであろう制約(c_001 は remove 済み、c_002 は継続、
    # stay_at_least が新規 id c_003 で追加された、という想定)。
    returned_constraints = [
        {"id": "c_002", "pred": "exclude", "args": {"target": "spot_b"}, "weight": 1.0},
        {
            "id": "c_003",
            "pred": "stay_at_least",
            "args": {"target": "spot_a", "min": 60},
            "weight": 1.0,
        },
    ]
    tools.edit_queue = [_edit_result(spot_ids=["spot_a"], constraints=returned_constraints)]

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

    # 会話状態側は Service が返した constraints をそのまま採用する。
    assert state.itinerary.constraints == returned_constraints


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
    assert "保存済み" not in error["message_ja"]
    assert state.pending_constraints == []


async def test_edit_without_itinerary_saves_constraints_add_to_pending_constraints() -> None:
    """§5「旅程がまだ無いターンの制約はスレッド行に一時保持する」(2026-08-04、

    レビュー是正・裁定4: `edit_itinerary` が precondition_unmet で失敗した
    ターンでも、`constraints.add` は `threads.pending_constraints`
    (= `state.pending_constraints`)へ保存する。これが新アーキで
    `pending_constraints` へ書き込める唯一の経路である)。
    """

    state = _state(itinerary=None)
    tools = FakeItineraryTools()

    digest, error = await run_edit_itinerary(
        state,
        tools,
        {
            "ops": [],
            "constraints": {
                "add": [
                    {
                        "pred": "stay_at_least",
                        "args": {"target": "地点エー", "min": 60},
                        "weight": 0.6,
                        "source_text": "ゆっくりしたい",
                    }
                ],
                "remove": [],
            },
            "notes": None,
        },
        step_id=1,
    )

    assert error is not None
    assert error["code"] == ToolErrorCode.PRECONDITION_UNMET.value
    assert "保存済み" in error["message_ja"]
    assert tools.edit_calls == []
    assert len(state.pending_constraints) == 1
    assert state.pending_constraints[0]["pred"] == "stay_at_least"
    # スポット名は id へ解決された状態で保存される。
    assert state.pending_constraints[0]["args"]["target"] == "spot_a"


async def test_add_op_resolves_after_target_name_to_spot_id() -> None:
    """`add` op の `after` もスポット名 → id へ解決する(2026-08-04、

    レビュー是正: High。旧実装は `targets` だけ解決し `after` を素通り
    させていたため、「地点Bを地点Aの後に追加」が常に失敗していた。
    """

    state = _state(itinerary=_existing_itinerary(constraints=[]))
    tools = FakeItineraryTools()
    tools.edit_queue = [_edit_result(spot_ids=["spot_a", "spot_b"])]

    digest, error = await run_edit_itinerary(
        state,
        tools,
        {
            "ops": [{"op": "add", "targets": ["地点ビー"], "day": None, "after": "地点エー"}],
            "constraints": None,
            "notes": None,
        },
        step_id=1,
    )

    assert error is None
    call = tools.edit_calls[0]
    assert call["args"].ops == [
        {"op": "add", "targets": ["spot_b"], "day": None, "after": "spot_a"}
    ]


async def test_add_op_drops_unresolved_after_but_still_executes() -> None:
    """`after` が解決できなくても手全体は落とさず、`after` だけ落として報告する(C4)。"""

    state = _state(itinerary=_existing_itinerary(constraints=[]))
    tools = FakeItineraryTools()
    tools.edit_queue = [_edit_result(spot_ids=["spot_a", "spot_b"])]

    digest, error = await run_edit_itinerary(
        state,
        tools,
        {
            "ops": [{"op": "add", "targets": ["地点ビー"], "day": None, "after": "架空スポット"}],
            "constraints": None,
            "notes": None,
        },
        step_id=1,
    )

    assert error is None
    call = tools.edit_calls[0]
    assert call["args"].ops == [{"op": "add", "targets": ["spot_b"], "day": None, "after": None}]
    assert "架空スポット" in digest


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

    2回目の edit_itinerary が1回目で(Service が返し `state.itinerary` に
    採用された)制約 id を `used_ids` として正しく引き継ぎ、Tool へ渡す新規
    採番が衝突しないことを検査する(§5。2026-08-04 レビュー是正・裁定7で
    `state.itinerary.constraints` は Service の返り値そのものになったため、
    Fake の返り値もそれを模した現実的な内容にする)。
    """

    state = _state(itinerary=_existing_itinerary(constraints=[]))
    tools = FakeItineraryTools()
    first_returned_constraints = [
        {"id": "c_001", "pred": "require", "args": {"target": "spot_a"}, "weight": 1.0},
    ]
    second_returned_constraints = [
        *first_returned_constraints,
        {"id": "c_002", "pred": "require", "args": {"target": "spot_b"}, "weight": 1.0},
    ]
    tools.edit_queue = [
        _edit_result(
            step_id=1, version=2, spot_ids=["spot_a"], constraints=first_returned_constraints
        ),
        _edit_result(
            step_id=2, version=3, spot_ids=["spot_a"], constraints=second_returned_constraints
        ),
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
    assert state.itinerary.constraints == first_returned_constraints
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
    assert state.itinerary.constraints == second_returned_constraints
    all_ids = [value.get("id") for value in state.itinerary.constraints]
    assert len(all_ids) == len(set(all_ids)) == 2
    assert first_ids.issubset(set(all_ids))
    # 2回目に Tool へ渡した新規制約案の id も 1回目の id と衝突しない
    # (itinerary_subagent.py 自身の採番ロジック。`used_ids` の引き継ぎ)。
    second_call_added_ids = {value.id for value in tools.edit_calls[1]["constraints"]}
    assert second_call_added_ids.isdisjoint(first_ids)
