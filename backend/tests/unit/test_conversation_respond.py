"""N5 応答のクローズドワールド照合に関する回帰テスト。"""

from typing import Any

from app.domains.conversation.events import MemoryEventSink
from app.domains.conversation.respond import _allowed_spot_ids, respond
from app.domains.conversation.state import (
    CandidateReference,
    ItineraryState,
    ProfileState,
    TurnState,
)
from app.domains.conversation.types import ToolName, ToolResult, TrajectoryStep
from app.domains.itinerary.types import Itinerary


class StaticResponseClient:
    async def generate(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        del messages, kwargs
        return "起点・終点は道の駅象潟で、直前の候補は鶴間池です。"


class RemovedSpotResponseClient:
    async def generate(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        del messages, kwargs
        return "ご指定どおり、二ノ滝を旅程から外しました。"


class OriginMentionResponseClient:
    async def generate(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        del messages, kwargs
        return "鳥海高原家族旅行村から車で約 25 分の鶴間池をおすすめします。"


class HallucinatedSpotResponseClient:
    async def generate(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        del messages, kwargs
        return "架空の滝という場所も良いですよ。"


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
        last_candidates=[CandidateReference(spot_id="spot_012", name_ja="鶴間池", rank=1)],
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
        last_candidates=[CandidateReference(spot_id="spot_012", name_ja="鶴間池", rank=1)],
    )
    sink = MemoryEventSink()

    await respond(state, client=StaticResponseClient(), event_sink=sink)

    assert state.degraded == []
    assert [event.event for event in sink.events] == ["token"]


async def test_removed_spot_is_allowed_in_edit_response() -> None:
    state = TurnState(
        turn_id="turn-edit-removed",
        thread_id=1,
        user_id=1,
        utterance="1つ目の滝を外して",
        profile=ProfileState(),
        spot_names={"spot_010": "二ノ滝"},
        step_results={
            1: ToolResult(
                step_id=1,
                tool=ToolName.EDIT_ITINERARY,
                data={
                    "itinerary": _itinerary(),
                    "diff": {
                        "added": [],
                        "removed": ["spot_010"],
                        "moved": [],
                        "retimed": [],
                    },
                    "concessions": [],
                },
            )
        },
    )
    sink = MemoryEventSink()

    await respond(state, client=RemovedSpotResponseClient(), event_sink=sink)

    assert state.degraded == []
    assert [event.event for event in sink.events] == ["token"]


def test_diff_and_concession_spot_ids_are_all_allowed() -> None:
    state = TurnState(
        turn_id="turn-edit-all-diff-fields",
        thread_id=1,
        user_id=1,
        utterance="旅程を調整して",
        profile=ProfileState(),
        spot_names={
            "spot_added": "追加地点",
            "spot_removed": "削除地点",
            "spot_moved": "移動地点",
            "spot_retimed": "時刻変更地点",
            "spot_concession": "譲歩地点",
        },
        step_results={
            1: ToolResult(
                step_id=1,
                tool=ToolName.EDIT_ITINERARY,
                data={
                    "diff": {
                        "added": ["spot_added"],
                        "removed": ["spot_removed"],
                        "moved": [{"spot_id": "spot_moved"}],
                        "retimed": ["spot_retimed"],
                    },
                    "concessions": [
                        {
                            "pred": "require",
                            "args": {"target": "spot_concession"},
                        }
                    ],
                },
            )
        },
    )

    assert {
        "spot_added",
        "spot_removed",
        "spot_moved",
        "spot_retimed",
        "spot_concession",
    } <= _allowed_spot_ids(state)


async def test_recommend_digest_origin_name_is_allowed_in_response() -> None:
    """不具合2の是正: recommend の移動時間文にだけ出る起点施設名は許可する。

    実機ログ(2026-08-04)では、推薦ダイジェストの移動時間文
    (「鳥海高原家族旅行村から車で約 25 分」)に出る起点施設名が
    `last_candidates`/`step_results` のような構造化フィールドには載らず、
    respond の入力素材(軌跡テキスト)にだけ現れるため
    `response_closed_world_violation` として誤検知していた。
    """

    state = TurnState(
        turn_id="turn-origin-mention",
        thread_id=1,
        user_id=1,
        utterance="おすすめを教えて",
        profile=ProfileState(),
        spot_names={"spot_base": "鳥海高原家族旅行村", "spot_012": "鶴間池"},
        last_candidates=[CandidateReference(spot_id="spot_012", name_ja="鶴間池", rank=1)],
        trajectory=[
            TrajectoryStep(
                tool="recommend",
                thought="おすすめを探す",
                args={"instruction": "おすすめを教えて"},
                observation=(
                    "おすすめ:\n  1. 鶴間池(タグ: 自然) 鳥海高原家族旅行村から車で約 25 分"
                ),
            )
        ],
    )
    sink = MemoryEventSink()

    await respond(state, client=OriginMentionResponseClient(), event_sink=sink)

    assert state.degraded == []
    assert [event.event for event in sink.events] == ["token"]


def test_trajectory_text_only_names_are_allowed() -> None:
    """`_allowed_spot_ids` 単体でも、軌跡テキストにだけ出る名前を拾う。"""

    state = TurnState(
        turn_id="turn-origin-unit",
        thread_id=1,
        user_id=1,
        utterance="おすすめを教えて",
        profile=ProfileState(),
        spot_names={"spot_base": "鳥海高原家族旅行村"},
        trajectory=[
            TrajectoryStep(
                tool="recommend",
                thought="",
                args={},
                observation="鳥海高原家族旅行村から車で約 25 分",
            )
        ],
    )

    assert "spot_base" in _allowed_spot_ids(state)


async def test_unpresented_spot_name_is_still_flagged() -> None:
    """回帰防止: 軌跡・旅程ダイジェストに一切出ない地点名は引き続き検知する。"""

    state = TurnState(
        turn_id="turn-hallucinated",
        thread_id=1,
        user_id=1,
        utterance="おすすめを教えて",
        profile=ProfileState(),
        spot_names={"spot_012": "鶴間池", "spot_hidden": "架空の滝"},
        last_candidates=[CandidateReference(spot_id="spot_012", name_ja="鶴間池", rank=1)],
    )
    sink = MemoryEventSink()

    await respond(state, client=HallucinatedSpotResponseClient(), event_sink=sink)

    assert [value.code for value in state.degraded] == ["response_closed_world_violation"]
    assert "架空の滝" in state.degraded[0].message
