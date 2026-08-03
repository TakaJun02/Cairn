"""N1.5 `update_profile`: guided JSON の型・空差分の扱い・state:profile 送出条件。

`Docs/30_design/agent_react_architecture.md` §2 と、23_ux_issues.md §3-5
（差分が空でも state:profile が出てしまう既存問題）の解消を検査する。
"""

from __future__ import annotations

import json
from typing import Any

from app.domains.conversation.events import MemoryEventSink
from app.domains.conversation.prompts import update_profile_guided_schema
from app.domains.conversation.state import ProfileState, SpotFact, TurnState
from app.domains.conversation.types import ProfileDelta
from app.domains.conversation.update_profile import (
    is_profile_delta_empty,
    merge_profile_delta,
    update_profile,
)


class ScriptedClient:
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    async def generate(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        self.calls.append({"messages": messages, **kwargs})
        return self.response


class SequencedClient:
    """`generate()` を呼ぶたびに、キューした応答を1つずつ返す(再試行検証用)。"""

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def generate(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        self.calls.append({"messages": messages, **kwargs})
        return self.responses.pop(0)


def _output(
    profile_delta: dict[str, Any] | None = None,
    score_adjustments: list[dict[str, Any]] | None = None,
) -> str:
    return json.dumps(
        {"profile_delta": profile_delta, "score_adjustments": score_adjustments or []},
        ensure_ascii=False,
    )


def _state() -> TurnState:
    spots = {
        "spot_001": SpotFact(spot_id="spot_001", name_ja="鶴間池", kind="poi", tags_ja=["自然"]),
        "spot_002": SpotFact(
            spot_id="spot_002", name_ja="元滝伏流水", kind="poi", tags_ja=["滝"]
        ),
    }
    return TurnState(
        turn_id="turn",
        thread_id=1,
        user_id=1,
        utterance="家族で行くので静かな所がいい",
        profile=ProfileState(),
        spot_id_vocab=list(spots),
        spot_names={key: value.name_ja for key, value in spots.items()},
        spot_catalog=spots,
    )


async def test_empty_delta_does_not_write_profile_or_emit_state_event() -> None:
    state = _state()
    sink = MemoryEventSink()

    await update_profile(state, client=ScriptedClient(_output()), event_sink=sink)

    assert state.profile_delta is None
    assert state.profile == ProfileState()
    assert sink.events == []


async def test_non_empty_delta_merges_into_profile_and_emits_state_event() -> None:
    state = _state()
    sink = MemoryEventSink()
    value = _output(profile_delta={"party": "family_kids", "interests": {"water": 0.6}})

    await update_profile(state, client=ScriptedClient(value), event_sink=sink)

    assert state.profile.party == "family_kids"
    assert state.profile.interests == {"water": 0.6}
    assert state.profile_delta is not None
    assert [event.event for event in sink.events] == ["state"]
    assert sink.events[0].data["kind"] == "profile"
    assert sink.events[0].data["profile"]["party"] == "family_kids"


async def test_all_empty_but_non_null_delta_is_normalized_to_no_op() -> None:
    """LLM が `{}` 相当の空オブジェクトを返しても差分なしとして扱う（§3-5 の解消）。"""

    state = _state()
    sink = MemoryEventSink()
    value = _output(
        profile_delta={
            "interests": {},
            "party": None,
            "mobility": None,
            "pace": None,
            "avoid": [],
            "notes": None,
        }
    )

    await update_profile(state, client=ScriptedClient(value), event_sink=sink)

    assert state.profile_delta is None
    assert sink.events == []


async def test_score_adjustments_out_of_vocabulary_are_dropped() -> None:
    state = _state()
    value = _output(
        score_adjustments=[
            {"spot_id": "spot_001", "delta": 0.3, "why": "静か", "handling": "weight"},
            {"spot_id": "spot_999", "delta": 0.2, "why": "架空", "handling": "weight"},
        ]
    )

    await update_profile(state, client=ScriptedClient(value))

    assert [value.spot_id for value in state.score_adjustments] == ["spot_001"]


async def test_score_adjustments_merge_across_reruns_instead_of_replacing() -> None:
    """裁定13(2026-08-04レビュー是正): `ask_user` の回答に対して本ステップを

    ターン内でもう 1 回走らせても(§2・§7)、ターン冒頭の発話由来の
    score_adjustments は消えない(置換ではなくマージ。同じ spot_id は
    後勝ちで上書き)。
    """

    state = _state()
    first_pass = _output(
        score_adjustments=[
            {"spot_id": "spot_001", "delta": 0.3, "why": "静か", "handling": "weight"},
        ]
    )
    await update_profile(state, client=ScriptedClient(first_pass))
    assert [value.spot_id for value in state.score_adjustments] == ["spot_001"]

    second_pass = _output(
        score_adjustments=[
            {"spot_id": "spot_002", "delta": 0.4, "why": "回答後の追加", "handling": "weight"},
        ]
    )
    await update_profile(
        state,
        client=ScriptedClient(second_pass),
        utterance_override="(質問: どのくらい歩けますか) 30分程度なら",
    )

    ids = {value.spot_id for value in state.score_adjustments}
    assert ids == {"spot_001", "spot_002"}


async def test_score_adjustments_merge_overwrites_same_spot_id_with_the_latest() -> None:
    state = _state()
    first_pass = _output(
        score_adjustments=[
            {"spot_id": "spot_001", "delta": 0.3, "why": "静か", "handling": "weight"},
        ]
    )
    await update_profile(state, client=ScriptedClient(first_pass))

    second_pass = _output(
        score_adjustments=[
            {"spot_id": "spot_001", "delta": -0.1, "why": "撤回", "handling": "weight"},
        ]
    )
    await update_profile(state, client=ScriptedClient(second_pass))

    assert len(state.score_adjustments) == 1
    assert state.score_adjustments[0].delta == -0.1
    assert state.score_adjustments[0].why == "撤回"


async def test_generation_failure_degrades_without_raising() -> None:
    class FailingClient:
        async def generate(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
            raise RuntimeError("生成サーバーが応答しません")

    state = _state()
    sink = MemoryEventSink()

    await update_profile(state, client=FailingClient(), event_sink=sink)

    assert state.profile_delta is None
    assert sink.events == []
    assert state.degraded[0].code == "update_profile_failed"


async def test_broken_guided_json_retries_without_guided_and_succeeds() -> None:
    """不具合1の是正: guided decoding が壊れた JSON を返しても、guided を
    外した再試行(`prompts.build_update_profile_fallback_messages`)で
    1 回だけ立て直す。2026-08-04 実機調査で vLLM/xgrammar の guided
    decoding が無限空白ループに陥り JSON が壊れる不具合を確認した際の
    実際の壊れ方(数値を書いた直後で途切れる)を模している。
    """

    broken = (
        '{"profile_delta": null, "score_adjustments": '
        '[{"spot_id": "spot_001", "delta": -0.1   '
    )
    # フォールバックはコードフェンス付き・guided 前提の schema.enum に無い
    # 値(-100)で返ってもクランプで復元できることも合わせて確認する。
    recovered = (
        "```json\n"
        + json.dumps(
            {
                "profile_delta": None,
                "score_adjustments": [
                    {
                        "spot_id": "spot_001",
                        "delta": -100,
                        "why": "ユーザーが除外を希望したため",
                        "handling": "weight",
                    }
                ],
            },
            ensure_ascii=False,
        )
        + "\n```"
    )
    state = _state()
    client = SequencedClient([broken, recovered])

    await update_profile(state, client=client)

    assert len(client.calls) == 2
    assert state.log_fields.get("update_profile_failed") is None
    assert "extra_body" in client.calls[0]  # 1回目は guided decoding
    assert client.calls[1].get("extra_body") is None  # 2回目は guided を外す
    assert [value.spot_id for value in state.score_adjustments] == ["spot_001"]
    assert state.score_adjustments[0].delta == -0.5  # クランプされる(範囲外の -100 → -0.5)
    assert state.degraded == []


async def test_broken_guided_json_both_attempts_fail_degrades() -> None:
    """再試行も壊れていれば、従来どおり縮退して差分なしで続行する(NFR-5)。"""

    broken1 = '{"profile_delta": null, "score_adjustments": [{"spot_id": "spot_001"   '
    broken2 = "not json at all"
    state = _state()
    sink = MemoryEventSink()
    client = SequencedClient([broken1, broken2])

    await update_profile(state, client=client, event_sink=sink)

    assert len(client.calls) == 2
    assert state.profile_delta is None
    assert sink.events == []
    assert state.degraded[0].code == "update_profile_failed"
    assert state.log_fields.get("update_profile_failed") is True


def test_is_profile_delta_empty() -> None:
    assert is_profile_delta_empty(None) is True
    assert is_profile_delta_empty(ProfileDelta()) is True
    assert is_profile_delta_empty(ProfileDelta(party="solo")) is False
    assert is_profile_delta_empty(ProfileDelta(interests={"water": 0.1})) is False


def test_merge_profile_delta_overwrites_scalars_and_unions_lists() -> None:
    profile = ProfileState(
        interests={"water": 0.2}, party="solo", avoid=["長時間歩行"], notes="既存メモ"
    )
    delta = ProfileDelta(
        interests={"mountain": 0.5}, mobility="short_walk_ok", avoid=["長時間歩行", "混雑"]
    )

    merged = merge_profile_delta(profile, delta)

    assert merged.interests == {"water": 0.2, "mountain": 0.5}
    assert merged.party == "solo"  # 差分に無ければ既存を保つ
    assert merged.mobility == "short_walk_ok"
    assert merged.avoid == ["長時間歩行", "混雑"]  # 重複は畳む
    assert merged.notes == "既存メモ"


def test_guided_schema_constrains_interests_and_score_adjustment_spot_ids() -> None:
    schema = update_profile_guided_schema(["spot_001", "spot_002"])

    assert list(schema["properties"]) == ["profile_delta", "score_adjustments"]
    profile_object = schema["properties"]["profile_delta"]["anyOf"][1]
    assert len(profile_object["properties"]["interests"]["properties"]) == 12
    score_item = schema["properties"]["score_adjustments"]["items"]
    assert score_item["properties"]["spot_id"]["enum"] == ["spot_001", "spot_002"]
    assert schema["properties"]["score_adjustments"]["maxItems"] == 8

    empty_vocab = update_profile_guided_schema([])
    empty_score_item = empty_vocab["properties"]["score_adjustments"]["items"]
    assert empty_vocab["properties"]["score_adjustments"]["maxItems"] == 0
    assert "enum" not in empty_score_item["properties"]["spot_id"]
