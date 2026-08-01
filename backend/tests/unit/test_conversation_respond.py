"""N5 応答のクローズドワールド照合に関する回帰テスト。"""

from typing import Any

from app.domains.conversation.events import MemoryEventSink
from app.domains.conversation.respond import respond
from app.domains.conversation.state import (
    CandidateReference,
    ItineraryState,
    ProfileState,
    TurnState,
)
from app.domains.conversation.types import ToolName, ToolResult
from app.domains.itinerary.types import Itinerary


class StaticResponseClient:
    async def generate(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        del messages, kwargs
        return "起点・終点は道の駅象潟で、直前の候補は鶴間池です。"


def _itinerary() -> dict[str, Any]:
    return {
        "version": 1,
        "days": [
            {
                "date": "2026-08-03",
                "start_min": 540,
                "end_min": 1020,
                "origin": {"kind": "spot", "spot_id": "spot_011"},
                "destination": {"kind": "spot", "spot_id": "spot_011"},
                "items": [],
            }
        ],
        "concessions": [],
    }


async def test_itinerary_endpoints_and_last_candidates_are_allowed_in_response() -> None:
    state = TurnState(
        turn_id="turn-closed-world",
        thread_id=1,
        user_id=1,
        utterance="旅程を作って",
        profile=ProfileState(),
        spot_names={"spot_011": "道の駅象潟", "spot_012": "鶴間池"},
        last_candidates=[
            CandidateReference(spot_id="spot_012", name_ja="鶴間池", rank=1)
        ],
        step_results={
            1: ToolResult(
                step_id=1,
                tool=ToolName.PLAN_ITINERARY,
                data={"itinerary": _itinerary()},
            )
        },
    )
    sink = MemoryEventSink()

    await respond(state, client=StaticResponseClient(), event_sink=sink)

    assert state.degraded == []
    assert [event.event for event in sink.events] == ["token"]


async def test_current_itinerary_endpoints_are_allowed_in_response() -> None:
    state = TurnState(
        turn_id="turn-current-itinerary",
        thread_id=1,
        user_id=1,
        utterance="今の旅程を説明して",
        profile=ProfileState(),
        itinerary=ItineraryState(
            itinerary=Itinerary.model_validate(_itinerary()),
        ),
        spot_names={"spot_011": "道の駅象潟", "spot_012": "鶴間池"},
        last_candidates=[
            CandidateReference(spot_id="spot_012", name_ja="鶴間池", rank=1)
        ],
    )
    sink = MemoryEventSink()

    await respond(state, client=StaticResponseClient(), event_sink=sink)

    assert state.degraded == []
    assert [event.event for event in sink.events] == ["token"]
