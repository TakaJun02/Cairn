"""固定プレフィックスを保つ understand/respond プロンプト。"""

from __future__ import annotations

import json
from typing import Any

from app.domains.conversation.state import TurnState
from app.domains.conversation.types import (
    Intent,
    ResponseMode,
    Slot,
    ToolName,
    pred_values,
    preference_values,
)

_PREFERENCE_VOCABULARY = " | ".join(preference_values())
_INTENT_VALUES = [value.value for value in Intent]
_TOOL_VALUES = [value.value for value in ToolName]
_SLOT_VALUES = [value.value for value in Slot]
_INTERPRETATION_VALUES = [
    "all_matches",
    "single_match",
    "current_itinerary",
    "last_candidates",
]


UNDERSTAND_SYSTEM_PROMPT = f"""あなたは鳥海山観光ガイダンスの
understand ノードです。
ユーザー発話を、指定された JSON Schema の JSON 1 個へ翻訳してください。
説明文や Markdown は出しません。

出力フィールドは必ず次の思考順で埋めます。この順序を変えません。
references → profile_delta → constraints → constraints_remove → score_adjustments →
selection_hints → unmodeled → action → intent → plan → clarify。

境界:
- あなたは何も実行しません。Tool の列を plan に書くだけです。
- 命令（「入れて」「外して」「調べて」）は plan/ops に写し、
  handling の数え上げには含めません。
- 「どうあってほしいか」は必ず dsl / weight / selection / unmodeled の
  どれか 1 経路へ写します。
- constraints は plan.args に入れず、トップレベルへ置きます。
- profile.interests のキーは次の 12 語だけです: {_PREFERENCE_VOCABULARY}
- 生タグ（例: 滝、温泉）は recommend.filter.tags と constraint.args の
  target にだけ使えます。
- action は done または ask_user です。ask_user は意味の曖昧さが実行を妨げ、
  具体的な選択肢が 2〜4 個ある場合だけです。その場合 plan=[]、
  intent=unclear、clarify を埋めます。
- 日付・時刻が無い旅程要求や、選好が薄い推薦要求では聞き返さず、
  done で進めます。
- 選好を聞く ask_user Tool と、意味を聞き返す action=ask_user を混同しません。
- 広い初回要求で party/mobility/interests がすべて空なら、
  plan の ask_user(onboarding) を使えます。
- plan は最大 3 手。Tool は recommend / plan_itinerary / edit_itinerary /
  search_knowledge / ask_user のみです。
- 後段は前段結果を $N.spot_ids、$N.spot_ids[:k]、$N.itinerary だけで
  参照できます。
- search_knowledge の引数名は request です（query ではありません）。
- edit_itinerary の自然言語 undo は ops=[{{"op":"revert"}}] です。
  他の op と混ぜません。
- plan_itinerary/edit_itinerary の制約はトップレベル constraints に置きます。
- clarify の resolves_to.kind は spot_id または interpretation です。

Tool 引数の要点:
recommend: {{filter: {{tags?, mobility?, weather_fit?, area?, day?}}, k: 1..8, exclude?}}
plan_itinerary: {{days: [{{date,start,end,origin,destination?}}], must_visit?}}
edit_itinerary: {{ops: [add/remove/move/replace/lock/set_stay/set_time/revert]}}
search_knowledge: {{request, spot_id?}}
ask_user: {{slot, reason, options（2〜4件）}}

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
この 1 本のテンプレートを explanation / question / clarification / failure の
全モードで使います。

必須規則:
- 入力にある spot_id と DB 表示名、事実、数値だけを使います。
  POI 名、距離、所要時間、時刻を作りません。
- score_breakdown の内部スコアはそのまま読み上げず、
  matched_keys / matched_tags などの根拠素材としてだけ使います。
- 候補や旅程を組み替えません。確定済み結果を説明するだけです。
- explanation では「今回考慮した条件」を列挙します。
- unmodeled、破棄・スキップ・失敗、譲歩があれば必ず明示します。
- question は質問 1 つと選択肢、clarification は曖昧だった点と
  選択肢だけを書きます。
- failure は分からなかったことと、ユーザーが次にできることを
  短く伝えます。
- 検索結果の coverage=none なら推測で補いません。

選好キー語彙（固定）: {_PREFERENCE_VOCABULARY}
"""


def build_understand_messages(state: TurnState) -> list[dict[str, str]]:
    """固定 ①② と可変 ③④⑤⑥を、必ずこの順で連結する。"""

    dynamic = _ordered_dynamic_context(state, include_turn_results=False)
    return [
        {"role": "system", "content": UNDERSTAND_SYSTEM_PROMPT},
        {"role": "user", "content": dynamic},
    ]


def build_respond_messages(
    state: TurnState,
    *,
    mode: ResponseMode,
) -> list[dict[str, str]]:
    dynamic = _ordered_dynamic_context(state, include_turn_results=True, mode=mode)
    return [
        {"role": "system", "content": RESPOND_SYSTEM_PROMPT},
        {"role": "user", "content": dynamic},
    ]


def understand_guided_schema(
    spot_ids: list[str],
    constraint_ids: list[str] | None = None,
    *,
    allow_clarification: bool = True,
) -> dict[str, Any]:
    """xgrammar 互換の schema。`uniqueItems` は意図的に一切使わない。"""

    spot_value_schema: dict[str, Any]
    if spot_ids:
        spot_value_schema = {"type": "string", "enum": list(dict.fromkeys(spot_ids))}
    else:
        # 空 enum や pattern を grammar compiler へ渡さず、親配列を空に縛る。
        spot_value_schema = {"type": "string"}
    interpretation_or_spot_values = [*spot_ids, *_INTERPRETATION_VALUES]
    resolution_value_schema: dict[str, Any] = {
        "type": "string",
        "enum": list(dict.fromkeys(interpretation_or_spot_values)),
    }
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
            "action": {
                "type": "string",
                "enum": (
                    ["done", "ask_user"]
                    if allow_clarification
                    else ["done"]
                ),
            },
            "intent": {"type": "string", "enum": _INTENT_VALUES},
            "plan": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer", "minimum": 1},
                        "tool": {"type": "string", "enum": _TOOL_VALUES},
                        "args": {"type": "object", "additionalProperties": True},
                    },
                    "required": ["id", "tool", "args"],
                    "additionalProperties": False,
                },
                "maxItems": 3,
            },
            "clarify": {
                "anyOf": [
                    {"type": "null"},
                    {
                        "type": "object",
                        "properties": {
                            "surface": {"type": "string", "minLength": 1},
                            "why": {"type": "string", "minLength": 1},
                            "options": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "label": {"type": "string", "minLength": 1},
                                        "resolves_to": {
                                            "type": "object",
                                            "properties": {
                                                "kind": {
                                                    "type": "string",
                                                    "enum": ["spot_id", "interpretation"],
                                                },
                                                "value": resolution_value_schema,
                                            },
                                            "required": ["kind", "value"],
                                            "additionalProperties": False,
                                        },
                                    },
                                    "required": ["label", "resolves_to"],
                                    "additionalProperties": False,
                                },
                                "minItems": 2,
                                "maxItems": 4,
                            },
                        },
                        "required": ["surface", "why", "options"],
                        "additionalProperties": False,
                    },
                ]
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
            "action",
            "intent",
            "plan",
            "clarify",
        ],
        "additionalProperties": False,
    }


def _ordered_dynamic_context(
    state: TurnState,
    *,
    include_turn_results: bool,
    mode: ResponseMode | None = None,
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
        "pending_clarification": state.pending_clarification,
        "resolved_ambiguities": state.resolved_ambiguities,
        "clarify_streak": state.clarify_streak,
        "asked_slots": state.asked_slots,
        "ask_streak": state.ask_streak,
        "explicit_resolution": (
            state.explicit_resolution.model_dump(mode="json")
            if state.explicit_resolution is not None
            else None
        ),
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
    # ⑥の発話より後ろには一切追加しない。
    return "\n".join(
        [
            "③ プロファイル・現在の旅程・有効な制約:\n"
            + _compact_json(profile_and_trip),
            "④ 参照可能な spot_id 語彙:\n" + _compact_json(vocab),
            "⑤ 会話履歴（understand/respond 共通）:\n" + (state.history or "(なし)"),
            "⑥ ユーザーの発話:\n" + state.utterance,
        ]
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
        "clarification": (
            state.clarification.model_dump(mode="json")
            if state.clarification is not None
            else None
        ),
        "ask_user": state.ask_user_payload,
        "tool_results": {
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
