"""固定プレフィックスを保つ understand/respond プロンプト。"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.domains.conversation.state import TurnState
from app.domains.conversation.types import (
    Intent,
    ResponseMode,
    Slot,
    ToolName,
    pred_values,
    preference_values,
)
from app.domains.recommendation.types import Mobility

_PREFERENCE_VOCABULARY = " | ".join(preference_values())
_MOBILITY_VOCABULARY = " | ".join(f'"{value.value}"' for value in Mobility)
_SLOT_VOCABULARY = " | ".join(f'"{value.value}"' for value in Slot)
_INTERPRETATION_VOCABULARY = (
    "all_matches | single_match | current_itinerary | last_candidates"
)
_INTENT_VALUES = [value.value for value in Intent]
_TOOL_VALUES = [value.value for value in ToolName]
_JAPAN_TZ = ZoneInfo("Asia/Tokyo")
_WEEKDAYS_JA = ("月", "火", "水", "木", "金", "土", "日")


UNDERSTAND_SYSTEM_PROMPT = f"""あなたは鳥海山観光ガイダンスの
understand ノードです。
ユーザー発話を、指定された JSON Schema の JSON 1 個へ翻訳してください。
説明文や Markdown は出しません。

出力フィールドは必ず次の思考順で埋めます。この順序を変えません。
references → profile_delta → constraints → constraints_remove → score_adjustments →
selection_hints → unmodeled → intent → plan。

境界:
- あなたは何も実行しません。Tool の列を plan に書くだけです。
- 命令（「入れて」「外して」「調べて」）は plan/ops に写し、
  handling の数え上げには含めません。
- 「どうあってほしいか」は必ず dsl / weight / selection / unmodeled の
  どれか 1 経路へ写します。
- constraints は plan.args に入れず、トップレベルへ置きます。
- profile.interests のキーは次の 12 語だけです: {_PREFERENCE_VOCABULARY}
- 生タグは④の「生タグ語彙」にある語だけを recommend.filter.tags と
  constraint.args.target に使えます。語彙に無い概念は tags に入れず、
  その概念を tags ではスキップします。「山」は語彙に無いので、意図に合う
  場合だけ「登山」か「鳥海山」を使い、合わなければ tags に入れません。
- mobility は移動手段ではなく歩行耐性です。値は {_MOBILITY_VOCABULARY}
  だけです。「車で行く」「車で回る」だけでは歩行耐性は不明なので、
  recommend.filter.mobility と profile_delta.mobility のどちらにも写しません。
- 意味の曖昧さが実行を妨げ、具体的な選択肢が 2〜4 個ある場合だけ、
  plan=[ask_user] の 1 手を出します。kind=clarify、intent=unclear とし、
  surface に曖昧だった表現を入れます。
- 日付・時刻が無い旅程要求や、選好が薄い推薦要求では聞き返さず、
  仮定して進めます。
- ask_user は選好を聞く kind=preference と、意味を聞き返す kind=clarify の
  1 つの Tool です。plan の末尾だけに置き、plan 全体で 1 手までです。
- 広い初回要求で party/mobility/interests がすべて空なら、
  kind=preference、slot=onboarding の ask_user を使えます。
- plan は最大 3 手。Tool は recommend / plan_itinerary / edit_itinerary /
  search_knowledge / ask_user のみです。
- 後段は前段結果を $N.spot_ids、$N.spot_ids[:k]、$N.itinerary だけで
  参照できます。
- search_knowledge の引数名は request です（query ではありません）。
- edit_itinerary の自然言語 undo は ops=[{{"op":"revert"}}] です。
  他の op と混ぜません。
- plan_itinerary/edit_itinerary の制約はトップレベル constraints に置きます。
- 前ターンの tool_results があれば、ask_user が何を聞き、ユーザーが
  何と答えたかを元の要求と一緒に解釈して plan を組みます。

Tool 引数の要点:
recommend: {{filter: {{tags?:[④の生タグ],
  mobility?:{_MOBILITY_VOCABULARY},
  weather_fit?:true|false, area?:非空文字列, day?:1以上の整数}},
  k:1..8, exclude?:[spot_id]}}
  weather_fit は boolean です。雨天適性を考慮する要求なら true にします。
  day は現在の旅程があるときだけ指定します。
plan_itinerary: {{days: [{{date:"YYYY-MM-DD", start:"HH:MM", end:"HH:MM",
  origin:{{kind:"spot"|"facility"|"coord", id?:spot_id, lat?:数値, lon?:数値}},
  destination?:{{kind, id?, lat?, lon?}}}}], must_visit?:[spot_id]}}
  例: 起点が道の駅象潟なら
  origin={{"kind":"facility","id":"spot_011"}}（文字列だけにしない）。
edit_itinerary: {{ops:[
  {{op:"add", targets:[spot_id] または "$N.spot_ids" または
    "$N.spot_ids[:k]", day?:1以上整数, after?:spot_id}},
  {{op:"remove", targets:[spot_id]}},
  {{op:"move", target:spot_id, day?:1以上整数, position?:1以上整数}},
  {{op:"replace", target:spot_id, with:spot_id または "$N.spot_ids[:1]"}},
  {{op:"lock", targets:[spot_id], locked:true|false}},
  {{op:"set_stay", target:spot_id, min:1以上整数}},
  {{op:"set_time", target:spot_id, arrive?:"HH:MM", depart?:"HH:MM"}},
  {{op:"revert", to_version?:1以上整数}}
]}}
  各 op では ? の無い引数が必須です。set_time は arrive/depart の少なくとも
  一方が必須です。
search_knowledge: {{request:非空文字列, spot_id?:spot_id}}
ask_user: {{kind:"preference"|"clarify",
  slot?:{_SLOT_VOCABULARY},
  surface?:非空文字列, reason:非空文字列,
  options:[{{label:非空文字列, value:非空文字列}}]（2〜4件）}}
  kind=preference は slot が必須で surface は不可、kind=clarify は surface が
  必須で slot は不可です。clarify の options.value は参照可能な spot_id または
  {_INTERPRETATION_VOCABULARY} のどれかだけです。

例:
- 「明日は滝を2つ入れて、昼を取れるようにして」なら recommend の後に
  edit_itinerary(add targets=$1.spot_ids) を置き、lunch_break は
  トップレベル constraints に置きます。
- 「さっきのに戻して」なら edit_itinerary(revert) の 1 手です。

選好キー語彙（固定）: {_PREFERENCE_VOCABULARY}
"""


RESPOND_SYSTEM_PROMPT = f"""あなたは鳥海山観光ガイダンスの
respond ノードです。
入力 JSON の mode に従い、自然で簡潔な日本語を
1 回だけ生成してください。
この 1 本のテンプレートを explanation / question / failure の
全モードで使います。

必須規則:
- 入力にある spot_id と DB 表示名、事実、数値だけを使います。
  POI 名、距離、所要時間、時刻を作りません。
- score_breakdown の内部スコアはそのまま読み上げず、
  matched_keys / matched_tags などの根拠素材としてだけ使います。
- 候補や旅程を組み替えません。確定済み結果を説明するだけです。
- explanation では「今回考慮した条件」を列挙します。
- unmodeled、破棄・スキップ・失敗、譲歩があれば必ず明示します。
- question は質問 1 つと選択肢だけを書きます。ask_user.kind=preference なら
  聞きたいことを、kind=clarify なら何が曖昧だったかを述べます。
- failure は分からなかったことと、ユーザーが次にできることを
  短く伝えます。
- 検索結果の coverage=none なら推測で補いません。

選好キー語彙（固定）: {_PREFERENCE_VOCABULARY}
"""


def build_understand_messages(
    state: TurnState,
    *,
    now: datetime | None = None,
) -> list[dict[str, str]]:
    """固定 ①② と可変 ③④⑤⑥を、必ずこの順で連結する。"""

    dynamic = _ordered_dynamic_context(
        state,
        include_turn_results=False,
        include_tag_vocabulary=True,
        now=now,
    )
    return [
        {"role": "system", "content": UNDERSTAND_SYSTEM_PROMPT},
        {"role": "user", "content": dynamic},
    ]


def build_respond_messages(
    state: TurnState,
    *,
    mode: ResponseMode,
    now: datetime | None = None,
) -> list[dict[str, str]]:
    dynamic = _ordered_dynamic_context(
        state,
        include_turn_results=True,
        include_tag_vocabulary=False,
        mode=mode,
        now=now,
    )
    return [
        {"role": "system", "content": RESPOND_SYSTEM_PROMPT},
        {"role": "user", "content": dynamic},
    ]


def understand_guided_schema(
    spot_ids: list[str],
    constraint_ids: list[str] | None = None,
) -> dict[str, Any]:
    """xgrammar 互換の schema。`uniqueItems` は意図的に一切使わない。"""

    spot_value_schema: dict[str, Any]
    if spot_ids:
        spot_value_schema = {"type": "string", "enum": list(dict.fromkeys(spot_ids))}
    else:
        # 空 enum や pattern を grammar compiler へ渡さず、親配列を空に縛る。
        spot_value_schema = {"type": "string"}
    normalized_constraint_ids = list(dict.fromkeys(constraint_ids or []))
    constraint_id_schema: dict[str, Any]
    if normalized_constraint_ids:
        constraint_id_schema = {
            "type": "string",
            "enum": normalized_constraint_ids,
        }
    else:
        constraint_id_schema = {"type": "string"}
    profile_properties = {
        key: {"type": "number", "minimum": -1.0, "maximum": 1.0}
        for key in preference_values()
    }
    regular_plan_step_schema = {
        "type": "object",
        "properties": {
            "id": {"type": "integer", "minimum": 1},
            "tool": {
                "type": "string",
                "enum": [
                    value
                    for value in _TOOL_VALUES
                    if value != ToolName.ASK_USER.value
                ],
            },
            "args": {"type": "object", "additionalProperties": True},
        },
        "required": ["id", "tool", "args"],
        "additionalProperties": False,
    }
    ask_user_plan_step_schema = {
        "type": "object",
        "properties": {
            "id": {"type": "integer", "minimum": 1},
            "tool": {"type": "string", "enum": [ToolName.ASK_USER.value]},
            "args": {
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["preference", "clarify"],
                    },
                    "slot": {
                        "type": "string",
                        "enum": [value.value for value in Slot],
                    },
                    "surface": {"type": "string", "minLength": 1},
                    "reason": {"type": "string", "minLength": 1},
                    "options": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "label": {"type": "string", "minLength": 1},
                                "value": {"type": "string", "minLength": 1},
                            },
                            "required": ["label", "value"],
                            "additionalProperties": False,
                        },
                        "minItems": 2,
                        "maxItems": 4,
                    },
                },
                "required": ["kind", "reason", "options"],
                "additionalProperties": False,
            },
        },
        "required": ["id", "tool", "args"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "references": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "surface": {"type": "string", "minLength": 1},
                        "spot_id": spot_value_schema,
                    },
                    "required": ["surface", "spot_id"],
                    "additionalProperties": False,
                },
                "maxItems": 8 if spot_ids else 0,
            },
            "profile_delta": {
                "anyOf": [
                    {"type": "null"},
                    {
                        "type": "object",
                        "properties": {
                            "interests": {
                                "type": "object",
                                "properties": profile_properties,
                                "additionalProperties": False,
                            },
                            "party": {
                                "anyOf": [
                                    {"type": "null"},
                                    {
                                        "type": "string",
                                        "enum": [
                                            "family_kids",
                                            "couple",
                                            "solo",
                                            "senior",
                                            "group",
                                        ],
                                    },
                                ]
                            },
                            "mobility": {
                                "anyOf": [
                                    {"type": "null"},
                                    {
                                        "type": "string",
                                        "enum": [
                                            "avoid_walk",
                                            "short_walk_ok",
                                            "hike_ok",
                                        ],
                                    },
                                ]
                            },
                            "pace": {
                                "anyOf": [
                                    {"type": "null"},
                                    {"type": "string", "enum": ["packed", "relaxed"]},
                                ]
                            },
                            "avoid": {
                                "type": "array",
                                "items": {"type": "string", "minLength": 1},
                                "maxItems": 8,
                            },
                            "notes": {"anyOf": [{"type": "null"}, {"type": "string"}]},
                        },
                        "required": [
                            "interests",
                            "party",
                            "mobility",
                            "pace",
                            "avoid",
                            "notes",
                        ],
                        "additionalProperties": False,
                    },
                ]
            },
            "constraints": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "pred": {"type": "string", "enum": pred_values()},
                        "args": {"type": "object", "additionalProperties": True},
                        "weight": {"type": "number", "minimum": 0.0},
                        "source_text": {"type": "string"},
                        "handling": {"type": "string", "enum": ["dsl"]},
                    },
                    "required": ["pred", "args", "weight", "source_text", "handling"],
                    "additionalProperties": False,
                },
                "maxItems": 12,
            },
            "constraints_remove": {
                "type": "array",
                "items": constraint_id_schema,
                "maxItems": 12 if normalized_constraint_ids else 0,
            },
            "score_adjustments": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "spot_id": spot_value_schema,
                        "delta": {"type": "number", "minimum": -0.5, "maximum": 0.5},
                        "why": {"type": "string"},
                        "handling": {"type": "string", "enum": ["weight"]},
                    },
                    "required": ["spot_id", "delta", "why", "handling"],
                    "additionalProperties": False,
                },
                "maxItems": 8 if spot_ids else 0,
            },
            "selection_hints": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "minLength": 1},
                        "handling": {"type": "string", "enum": ["selection"]},
                    },
                    "required": ["text", "handling"],
                    "additionalProperties": False,
                },
                "maxItems": 8,
            },
            "unmodeled": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "minLength": 1},
                        "handling": {"type": "string", "enum": ["unmodeled"]},
                    },
                    "required": ["text", "handling"],
                    "additionalProperties": False,
                },
                "maxItems": 8,
            },
            "intent": {"type": "string", "enum": _INTENT_VALUES},
            "plan": {
                "type": "array",
                "items": {
                    "anyOf": [
                        regular_plan_step_schema,
                        ask_user_plan_step_schema,
                    ]
                },
                "maxItems": 3,
            },
        },
        "required": [
            "references",
            "profile_delta",
            "constraints",
            "constraints_remove",
            "score_adjustments",
            "selection_hints",
            "unmodeled",
            "intent",
            "plan",
        ],
        "additionalProperties": False,
    }


def _ordered_dynamic_context(
    state: TurnState,
    *,
    include_turn_results: bool,
    include_tag_vocabulary: bool,
    mode: ResponseMode | None = None,
    now: datetime | None = None,
) -> str:
    active_constraints = (
        state.itinerary.constraints if state.itinerary is not None else state.pending_constraints
    )
    profile_and_trip: dict[str, Any] = {
        "profile": state.profile.model_dump(mode="json"),
        "current_itinerary": _itinerary_summary(state),
        "active_constraints": active_constraints,
        "last_candidates": [
            value.model_dump(mode="json") for value in state.last_candidates
        ],
        "resolved_ambiguities": state.resolved_ambiguities,
        "asked_slots": state.asked_slots,
        "ask_streak": state.ask_streak,
        "tool_results": state.tool_results,
        "realtime": {
            spot_id: state.realtime.get(spot_id, {}) for spot_id in state.spot_id_vocab
        },
    }
    if include_turn_results:
        profile_and_trip["mode"] = mode.value if mode is not None else None
        profile_and_trip["turn_result"] = _turn_result(state)
    vocab = [
        {"spot_id": spot_id, "name_ja": state.spot_names.get(spot_id, spot_id)}
        for spot_id in state.spot_id_vocab
    ]
    vocabulary_context = "④ 参照可能な spot_id 語彙:\n" + _compact_json(vocab)
    if include_tag_vocabulary:
        vocabulary_context += "\n" + _tag_vocabulary_context(state.tag_vocabulary)
    # ⑥の発話より後ろには一切追加しない。
    return "\n".join(
        [
            "③ 今日の日付（JST）・プロファイル・現在の旅程・有効な制約:\n"
            + _date_context(now)
            + "\n"
            + _compact_json(profile_and_trip),
            vocabulary_context,
            "⑤ 会話履歴（understand/respond 共通）:\n" + (state.history or "(なし)"),
            "⑥ ユーザーの発話:\n" + state.utterance,
        ]
    )


def _date_context(now: datetime | None) -> str:
    current = now or datetime.now(_JAPAN_TZ)
    if current.tzinfo is None:
        current = current.replace(tzinfo=_JAPAN_TZ)
    else:
        current = current.astimezone(_JAPAN_TZ)
    today = current.date()
    tomorrow = today + timedelta(days=1)
    weekday = _WEEKDAYS_JA[today.weekday()]
    return (
        f"今日は {today.isoformat()}({weekday})です。"
        f"『明日』は {tomorrow.isoformat()} を指します。"
    )


def _itinerary_summary(state: TurnState) -> dict[str, Any] | None:
    if state.itinerary is None:
        return None
    itinerary = state.itinerary.itinerary
    return {
        "version": itinerary.version,
        "days": [
            {
                "date": day.date,
                "origin": day.origin.spot_id,
                "destination": day.destination.spot_id,
                "spot_ids_in_order": [item.spot_id for item in day.items],
            }
            for day in itinerary.days
        ],
    }


def _turn_result(state: TurnState) -> dict[str, Any]:
    return {
        "intent": state.intent.value if state.intent is not None else None,
        "profile_delta": (
            state.profile_delta.model_dump(mode="json")
            if state.profile_delta is not None
            else None
        ),
        "constraints": [value.model_dump(mode="json") for value in state.constraints],
        "constraints_remove": state.constraints_remove,
        "score_adjustments": [
            value.model_dump(mode="json") for value in state.score_adjustments
        ],
        "selection_hints": [
            value.model_dump(mode="json") for value in state.selection_hints
        ],
        "unmodeled": [value.model_dump(mode="json") for value in state.unmodeled],
        "accepted_steps": [value.model_dump(mode="json") for value in state.accepted_steps],
        "rejected_steps": [value.model_dump(mode="json") for value in state.rejected_steps],
        "skipped_steps": [value.model_dump(mode="json") for value in state.skipped_steps],
        "aborted_at": state.aborted_at,
        "degraded": [value.model_dump(mode="json") for value in state.degraded],
        "assumptions": state.assumptions,
        "ask_user": state.pending_ask,
        "step_results": {
            str(step_id): result.model_dump(mode="json")
            for step_id, result in state.step_results.items()
        },
        "spot_names_from_db": {
            spot_id: state.spot_names[spot_id]
            for spot_id in _allowed_response_spot_ids(state)
            if spot_id in state.spot_names
        },
    }


def _allowed_response_spot_ids(state: TurnState) -> list[str]:
    values: list[str] = []
    for result in state.step_results.values():
        raw_ids = result.data.get("spot_ids")
        if isinstance(raw_ids, list):
            values.extend(value for value in raw_ids if isinstance(value, str))
        spot_id = result.data.get("spot_id")
        if isinstance(spot_id, str):
            values.append(spot_id)
    if state.itinerary is not None:
        values.extend(
            item.spot_id
            for day in state.itinerary.itinerary.days
            for item in day.items
        )
    values.extend(reference.spot_id for reference in state.references)
    return list(dict.fromkeys(values))


def _compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _tag_vocabulary_context(values: list[str]) -> str:
    vocabulary = list(dict.fromkeys(value for value in values if value))
    rendered = " | ".join(vocabulary) if vocabulary else "(なし)"
    return (
        f"生タグ語彙（{len(vocabulary)}語・ここにある語だけ使用可）:\n"
        f"{rendered}"
    )
