"""N2 の guided JSON、再試行、安全弁、意味後検証。"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.domains.conversation.events import MemoryEventSink
from app.domains.conversation.state import ProfileState, SpotFact, TurnState
from app.domains.conversation.understand import (
    UNDERSTAND_MAX_TOKENS,
    UnderstandFatalError,
    has_repeated_ngram,
    understand,
)


class ScriptedClient:
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def generate(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        self.calls.append({"messages": messages, **kwargs})
        return self.responses.pop(0)


def _state() -> TurnState:
    spots = {
        "spot_001": SpotFact(
            spot_id="spot_001",
            name_ja="鶴間池",
            kind="poi",
            tags_ja=["自然"],
        ),
        "spot_002": SpotFact(
            spot_id="spot_002",
            name_ja="元滝伏流水",
            kind="poi",
            tags_ja=["滝"],
        ),
    }
    return TurnState(
        turn_id="turn",
        thread_id=1,
        user_id=1,
        utterance="滝を紹介して",
        profile=ProfileState(),
        spot_id_vocab=list(spots),
        spot_names={key: value.name_ja for key, value in spots.items()},
        spot_catalog=spots,
    )


def _output(**overrides: Any) -> str:
    value: dict[str, Any] = {
        "references": [],
        "profile_delta": None,
        "constraints": [],
        "constraints_remove": [],
        "score_adjustments": [],
        "selection_hints": [],
        "unmodeled": [],
        "intent": "recommend",
        "plan": [{"id": 1, "tool": "recommend", "args": {"k": 2}}],
    }
    value.update(overrides)
    return json.dumps(value, ensure_ascii=False)


async def test_invalid_json_retries_once_and_records_degradation() -> None:
    client = ScriptedClient(["not-json", _output()])
    state = _state()

    await understand(state, client=client)

    assert state.understand_attempts == 2
    assert len(client.calls) == 2
    assert client.calls[0]["max_tokens"] == UNDERSTAND_MAX_TOKENS
    assert state.plan[0].tool == "recommend"
    assert [value.code for value in state.degraded] == ["understand_retry"]


async def test_invalid_json_twice_is_fatal() -> None:
    state = _state()

    with pytest.raises(UnderstandFatalError):
        await understand(state, client=ScriptedClient(["bad", "still bad"]))

    assert state.understand_failed is True
    assert state.understand_attempts == 2


def test_repeated_ngram_detector_handles_tokens_and_no_space_text() -> None:
    assert has_repeated_ngram(("同じブロックを反復します。" * 8)) is True
    assert has_repeated_ngram("普通の短い JSON です") is False


async def test_duplicate_values_are_removed_in_code_without_unique_items() -> None:
    duplicated = _output(
        references=[
            {"surface": "そこ", "spot_id": "spot_001"},
            {"surface": "そこ", "spot_id": "spot_001"},
        ],
        constraints_remove=["c_001", "c_001"],
        score_adjustments=[
            {
                "spot_id": "spot_001",
                "delta": 0.2,
                "why": "静か",
                "handling": "weight",
            },
            {
                "spot_id": "spot_001",
                "delta": 0.2,
                "why": "静か",
                "handling": "weight",
            },
        ],
    )
    state = _state()

    await understand(state, client=ScriptedClient([duplicated]))

    assert len(state.references) == 1
    assert len(state.score_adjustments) == 1


async def test_unknown_predicate_and_out_of_world_reference_are_not_silent() -> None:
    state = _state()
    value = _output(
        references=[{"surface": "架空", "spot_id": "spot_999"}],
        constraints=[
            {
                "pred": "unknown_pred",
                "args": {},
                "weight": 1.0,
                "source_text": "屋台",
                "handling": "dsl",
            }
        ],
    )

    await understand(state, client=ScriptedClient([value]))

    assert state.references == []
    assert any(item.rule == "closed_world" for item in state.rejected_steps)
    assert any(item.text == "屋台" for item in state.unmodeled)


async def test_clarification_is_returned_as_single_ask_user_plan_step() -> None:
    state = _state()
    sink = MemoryEventSink()
    value = _output(
        intent="unclear",
        plan=[
            {
                "id": 1,
                "tool": "ask_user",
                "args": {
                    "kind": "clarify",
                    "surface": "2番目",
                    "reason": "候補が2つあります",
                    "options": [
                        {"label": "鶴間池", "value": "spot_001"},
                        {"label": "元滝伏流水", "value": "spot_002"},
                    ],
                },
            }
        ],
    )

    await understand(state, client=ScriptedClient([value]), event_sink=sink)

    assert len(state.plan) == 1
    assert state.plan[0].tool == "ask_user"
    assert state.plan[0].args["kind"] == "clarify"
    assert sink.events == []
