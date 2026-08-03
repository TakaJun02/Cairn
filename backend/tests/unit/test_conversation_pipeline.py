"""失敗の三段階、イベント順、N6 finally をモックで検査する。"""

from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from typing import Any

import pytest

from app.domains.conversation.events import MemoryEventSink, emit, state_event
from app.domains.conversation.executor import act
from app.domains.conversation.pipeline import ConversationPipeline
from app.domains.conversation.respond import response_mode
from app.domains.conversation.state import (
    CandidateReference,
    ContextSnapshot,
    ProfileState,
    SkippedStep,
    SpotFact,
    TurnState,
)
from app.domains.conversation.types import (
    Intent,
    PlanStep,
    ResponseMode,
    ToolError,
    ToolErrorCode,
    ToolName,
    ToolResult,
)


def _understand_output(plan: list[dict[str, Any]]) -> str:
    return json.dumps(
        {
            "references": [],
            "constraints": [],
            "constraints_remove": [],
            "selection_hints": [],
            "unmodeled": [],
            "intent": "recommend",
            "plan": plan,
        },
        ensure_ascii=False,
    )


def _update_profile_output(
    *,
    profile_delta: dict[str, Any] | None = None,
    score_adjustments: list[dict[str, Any]] | None = None,
) -> str:
    """N1.5 update_profile 用の guided JSON レスポンス。既定は差分なし。"""

    return json.dumps(
        {
            "profile_delta": profile_delta,
            "score_adjustments": score_adjustments or [],
        },
        ensure_ascii=False,
    )


class TurnClient:
    """update_profile → understand の順で消費される generate() 応答キュー。"""

    def __init__(
        self,
        generate_responses: list[str | BaseException],
        *,
        response_chunks: list[str] | None = None,
        response_error: BaseException | None = None,
    ) -> None:
        self.generate_responses = list(generate_responses)
        self.response_chunks = response_chunks or []
        self.response_error = response_error

    async def generate(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        del messages, kwargs
        response = self.generate_responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response

    async def stream(self, messages: list[dict[str, str]], **kwargs: Any):
        del messages, kwargs
        for value in self.response_chunks:
            yield value
        if self.response_error is not None:
            raise self.response_error


class MemoryConversationRepository:
    def __init__(self) -> None:
        spot = SpotFact(
            spot_id="spot_001",
            name_ja="鶴間池",
            kind="facility",
            aliases_ja=["つるまいけ"],
            tags_ja=["自然"],
        )
        self.snapshot = ContextSnapshot(
            thread_id=1,
            profile=ProfileState(),
            itinerary=None,
            messages=[],
            last_candidates=[],
            presented_spot_ids=[],
            asked_slots=[],
            ask_streak=0,
            pending_ask=None,
            resolved_ambiguities=[],
            pending_constraints=[],
            realtime={},
            spots={spot.spot_id: spot},
        )
        self.persisted: list[TurnState] = []

    async def load_snapshot(self, user_id: int) -> ContextSnapshot:
        assert user_id == 1
        return self.snapshot.model_copy(deep=True)

    async def persist_turn(self, state: TurnState) -> int:
        self.persisted.append(state.model_copy(deep=True))
        self.snapshot.pending_ask = deepcopy(state.pending_ask)
        self.snapshot.ask_streak = (
            state.ask_streak + 1 if state.pending_ask is not None else 0
        )
        return 88


class FakeTools:
    def __init__(
        self,
        *,
        sink: MemoryEventSink | None = None,
        scripted: dict[int, ToolResult | ToolError | Exception] | None = None,
    ) -> None:
        self.sink = sink
        self.scripted = scripted or {}
        self.called: list[int] = []

    async def recommend(self, *, step_id: int, **kwargs: Any):
        del kwargs
        self.called.append(step_id)
        if step_id in self.scripted:
            return self.scripted[step_id]
        if self.sink is not None:
            await emit(
                self.sink,
                state_event(
                    "candidates",
                    phase="provisional",
                    items=[{"spot_id": "spot_001", "name_ja": "鶴間池"}],
                ),
            )
            await emit(
                self.sink,
                state_event(
                    "candidates",
                    phase="final",
                    items=[{"spot_id": "spot_001", "name_ja": "鶴間池"}],
                ),
            )
        return ToolResult(
            step_id=step_id,
            tool=ToolName.RECOMMEND,
            data={
                "spot_ids": ["spot_001"],
                "candidates": [{"spot_id": "spot_001", "rank": 1}],
                "provisional_spot_ids": ["spot_001"],
                "rerank_used": False,
            },
        )

    async def plan_itinerary(self, *, step_id: int, **kwargs: Any):
        del kwargs
        self.called.append(step_id)
        return self.scripted[step_id]

    async def edit_itinerary(self, *, step_id: int, **kwargs: Any):
        del kwargs
        self.called.append(step_id)
        return self.scripted[step_id]

    async def search_knowledge(self, *, step_id: int, **kwargs: Any):
        del kwargs
        self.called.append(step_id)
        scripted = self.scripted.get(step_id)
        if isinstance(scripted, Exception):
            raise scripted
        if scripted is not None:
            return scripted
        return ToolResult(
            step_id=step_id,
            tool=ToolName.SEARCH_KNOWLEDGE,
            data={
                "answer_ja": "資料の回答",
                "sources": [],
                "coverage": "none",
                "spot_id": None,
            },
        )

    async def ask_user(self, *, step_id: int, **kwargs: Any):
        self.called.append(step_id)
        if step_id in self.scripted:
            return self.scripted[step_id]
        args = kwargs["args"]
        payload = args.model_dump(mode="json", exclude_none=True)
        if self.sink is not None:
            if args.kind == "preference":
                await emit(
                    self.sink,
                    state_event(
                        "ask_user",
                        slot=args.slot.value,
                        options=[option.label for option in args.options],
                    ),
                )
            else:
                await emit(
                    self.sink,
                    state_event(
                        "clarify",
                        surface=args.surface,
                        options=[
                            {"label": option.label, "value": option.value}
                            for option in args.options
                        ],
                    ),
                )
        return ToolResult(
            step_id=step_id,
            tool=ToolName.ASK_USER,
            data=payload,
        )


def _state(steps: list[PlanStep]) -> TurnState:
    spot = SpotFact(
        spot_id="spot_001",
        name_ja="鶴間池",
        kind="facility",
        tags_ja=["自然"],
    )
    return TurnState(
        turn_id="turn",
        thread_id=1,
        user_id=1,
        utterance="test",
        profile=ProfileState(),
        spot_id_vocab=[spot.spot_id],
        spot_names={spot.spot_id: spot.name_ja},
        spot_catalog={spot.spot_id: spot},
        accepted_steps=steps,
    )


async def test_act_has_skip_skip_abort_three_levels_and_keeps_prior_result() -> None:
    fatal = ToolError(
        code=ToolErrorCode.INTERNAL,
        message_ja="Tool が失敗しました",
        recoverable=False,
    )
    tools = FakeTools(
        scripted={
            1: ToolResult(
                step_id=1,
                tool=ToolName.SEARCH_KNOWLEDGE,
                data={"answer_ja": "ok", "sources": [], "coverage": "none"},
            ),
            2: fatal,
        }
    )
    state = _state(
        [
            PlanStep(id=1, tool="search_knowledge", args={"request": "one"}),
            PlanStep(id=2, tool="search_knowledge", args={"request": "two"}),
            PlanStep(id=3, tool="search_knowledge", args={"request": "three"}),
        ]
    )

    await act(state, tools=tools)

    assert list(state.step_results) == [1]
    assert state.aborted_at == 2
    assert tools.called == [1, 2]

    unresolved = _state(
        [
            PlanStep(
                id=4,
                tool="edit_itinerary",
                args={"ops": [{"op": "add", "targets": "$99.spot_ids"}]},
            )
        ]
    )
    await act(unresolved, tools=tools)
    assert unresolved.skipped_steps[0].code == "reference_unresolved"

    precondition = _state(
        [PlanStep(id=5, tool="edit_itinerary", args={"ops": []})]
    )
    await act(precondition, tools=tools)
    assert precondition.skipped_steps[0].code == "precondition_unmet"


async def test_act_converts_leaked_tool_exception_to_abort_and_can_continue_recoverable() -> None:
    recoverable = ToolError(
        code=ToolErrorCode.EMPTY_RESULT,
        message_ja="候補がありません",
        recoverable=True,
    )
    tools = FakeTools(
        scripted={
            1: recoverable,
            2: RuntimeError("adapter leak"),
        }
    )
    state = _state(
        [
            PlanStep(id=1, tool="search_knowledge", args={"request": "one"}),
            PlanStep(id=2, tool="search_knowledge", args={"request": "two"}),
            PlanStep(id=3, tool="search_knowledge", args={"request": "three"}),
        ]
    )

    await act(state, tools=tools)

    assert tools.called == [1, 2]
    assert [value.code for value in state.skipped_steps] == [
        "empty_result",
        "internal",
    ]
    assert state.aborted_at == 2


async def test_pipeline_event_order_and_persist() -> None:
    sink = MemoryEventSink()
    repository = MemoryConversationRepository()
    tools = FakeTools(sink=sink)
    client = TurnClient(
        [
            _update_profile_output(profile_delta={"party": "family_kids"}),
            _understand_output([{"id": 1, "tool": "recommend", "args": {"k": 1}}]),
        ],
        response_chunks=[
            "鶴間池をご案内します。",
            "今回考慮した条件: なし。",
        ],
    )

    state = await ConversationPipeline(
        repository,
        event_sink=sink,
        llm_client=client,
        tools=tools,
    ).run(user_id=1, utterance="おすすめは？")

    assert repository.persisted[0].assistant_text == state.assistant_text
    assert [event.event for event in sink.events] == [
        "state",
        "state",
        "state",
        "state",
        "token",
        "token",
        "done",
    ]
    # update_profile（N1.5）が最初に走るため、profile イベントが先頭に来る。
    assert sink.events[0].data["kind"] == "profile"
    assert [event.data.get("kind") for event in sink.events[1:4]] == [
        "plan",
        "candidates",
        "candidates",
    ]
    assert sink.events[-1].data["message_id"] == 88


async def test_ask_user_suspends_resumes_and_pending_expires_after_one_turn() -> None:
    sink = MemoryEventSink()
    repository = MemoryConversationRepository()
    repository.snapshot.last_candidates = [
        CandidateReference(spot_id="spot_001", name_ja="鶴間池", rank=1)
    ]
    ask_output = json.loads(
        _understand_output(
            [
                {
                    "id": 1,
                    "tool": "ask_user",
                    "args": {
                        "kind": "clarify",
                        "surface": "2番目",
                        "reason": "候補が複数あります",
                        "options": [
                            {"label": "鶴間池", "value": "spot_001"},
                            {
                                "label": "直近候補全体",
                                "value": "last_candidates",
                            },
                        ],
                    },
                }
            ]
        )
    )
    ask_output["intent"] = "unclear"

    first = await ConversationPipeline(
        repository,
        event_sink=sink,
        llm_client=TurnClient(
            [_update_profile_output(), json.dumps(ask_output, ensure_ascii=False)],
            response_chunks=["「2番目」はどちらでしょうか。"],
        ),
        tools=FakeTools(sink=sink),
    ).run(user_id=1, utterance="2番目を外して")

    assert first.should_end_turn is True
    assert first.pending_ask == {
        "kind": "clarify",
        "surface": "2番目",
        "reason": "候補が複数あります",
        "options": [
            {"label": "鶴間池", "value": "spot_001"},
            {"label": "直近候補全体", "value": "last_candidates"},
        ],
    }
    assert repository.snapshot.pending_ask == first.pending_ask
    assert [event.data.get("kind") for event in sink.events if event.event == "state"] == [
        "plan",
        "clarify",
    ]

    second = await ConversationPipeline(
        repository,
        llm_client=TurnClient(
            [_update_profile_output(), _understand_output([])],
            response_chunks=["鶴間池として承りました。"],
        ),
        tools=FakeTools(),
    ).run(
        user_id=1,
        utterance="鶴間池",
        resolves={"surface": "2番目", "value": "spot_001"},
    )

    assert second.tool_results[0]["output"] == {
        "answer": "spot_001",
        "answered_by": "chip",
        "surface": "2番目",
    }
    assert second.log_fields["resumed_from_ask"] is True
    assert repository.snapshot.pending_ask is None


async def test_respond_failure_still_persists_failed_assistant_and_done() -> None:
    sink = MemoryEventSink()
    repository = MemoryConversationRepository()
    client = TurnClient(
        [_update_profile_output(), _understand_output([])],
        response_error=RuntimeError("stream down"),
    )

    state = await ConversationPipeline(
        repository,
        event_sink=sink,
        llm_client=client,
        tools=FakeTools(),
    ).run(user_id=1, utterance="こんにちは")

    assert state.respond_status == "failed"
    assert len(repository.persisted) == 1
    assert repository.persisted[0].respond_status == "failed"
    assert [event.event for event in sink.events][-2:] == ["error", "done"]


async def test_understand_fatal_persists_user_only_path_and_done() -> None:
    sink = MemoryEventSink()
    repository = MemoryConversationRepository()
    client = TurnClient([_update_profile_output(), "bad", "bad again"])

    state = await ConversationPipeline(
        repository,
        event_sink=sink,
        llm_client=client,
        tools=FakeTools(),
    ).run(user_id=1, utterance="理解不能")

    assert state.understand_failed is True
    assert len(repository.persisted) == 1
    assert repository.persisted[0].understand_failed is True
    assert [event.event for event in sink.events] == ["error", "done"]


def test_response_mode_marks_empty_actionable_turn_and_all_skips_as_failure() -> None:
    empty = _state([])
    empty.intent = Intent.RECOMMEND

    skipped = _state(
        [PlanStep(id=1, tool="search_knowledge", args={"request": "由来"})]
    )
    skipped.intent = Intent.QA
    skipped.skipped_steps = [
        SkippedStep(
            step_id=1,
            tool="search_knowledge",
            code="reference_unresolved",
            reason="照応を解決できません",
        )
    ]

    chitchat = _state([])
    chitchat.intent = Intent.CHITCHAT

    assert response_mode(empty) is ResponseMode.FAILURE
    assert response_mode(skipped) is ResponseMode.FAILURE
    assert response_mode(chitchat) is ResponseMode.EXPLANATION


async def test_cancelled_respond_persists_partial_text_before_propagating() -> None:
    sink = MemoryEventSink()
    repository = MemoryConversationRepository()
    client = TurnClient(
        [_update_profile_output(), _understand_output([])],
        response_chunks=["生成途中"],
        response_error=asyncio.CancelledError(),
    )

    with pytest.raises(asyncio.CancelledError):
        await ConversationPipeline(
            repository,
            event_sink=sink,
            llm_client=client,
            tools=FakeTools(),
        ).run(user_id=1, utterance="こんにちは")

    assert len(repository.persisted) == 1
    assert repository.persisted[0].assistant_text == "生成途中"
    assert repository.persisted[0].respond_status == "partial"
    assert sink.events[-1].event == "done"


async def test_cancelled_understand_persists_user_only_before_propagating() -> None:
    sink = MemoryEventSink()
    repository = MemoryConversationRepository()

    with pytest.raises(asyncio.CancelledError):
        await ConversationPipeline(
            repository,
            event_sink=sink,
            llm_client=TurnClient(
                [_update_profile_output(), asyncio.CancelledError()]
            ),
            tools=FakeTools(),
        ).run(user_id=1, utterance="途中で停止")

    assert len(repository.persisted) == 1
    assert repository.persisted[0].understand_failed is True
    assert sink.events[-1].event == "done"


async def test_cancelled_update_profile_persists_user_only_before_propagating() -> None:
    """N1.5 update_profile 自体がキャンセルされても persist に必ず到達する（§13）。"""

    sink = MemoryEventSink()
    repository = MemoryConversationRepository()

    with pytest.raises(asyncio.CancelledError):
        await ConversationPipeline(
            repository,
            event_sink=sink,
            llm_client=TurnClient([asyncio.CancelledError()]),
            tools=FakeTools(),
        ).run(user_id=1, utterance="途中で停止")

    assert len(repository.persisted) == 1
    assert repository.persisted[0].understand_failed is True
    assert sink.events[-1].event == "done"
