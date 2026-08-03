"""④ レコメンドサブエージェント(§4)。

`Docs/30_design/agent_react_architecture.md` §4 が仕様。メインエージェントが
書いた `instruction`(自然言語)を、既存の推薦処理(`tool_adapters.recommend`
→ `RecommendationService`。**内部は一切変更しない**)がそのまま読める
`filter`(タグ・mobility・weather_fit・area・day)へ、1 回の guided JSON で
翻訳する。

SA の Tool は `done`(filter/assumptions)と `ask_user`(slot/reason/options)の
anyOf 排他(§4 (a))。プロフィールが薄すぎるときだけ 1 回質問できる(A7)。
`state`/`tools` を渡さない呼び出し元(既存テスト等)では `ask_user` 分岐自体を
スキーマから外し、従来どおり `done` 単発判定のまま動く。

**タグ 80 語(`static.tag_vocabulary`)と `mobility` の enum は、このモジュール
のプロンプトにだけ載る。**メインエージェント(`prompts.py` の
`MAIN_AGENT_SYSTEM_PROMPT`)には一切現れない — 語彙の当て外しがメインの手を
壊す構造をメインから消す、という §3.3 の設計判断そのものである。
`mobility` は**歩行耐性であって移動手段ではない**ことをプロンプトで明記する
([23_ux_issues.md](../../../../Docs/23_ux_issues.md) §0.3 の実害の再発防止)。

guided decoding が大半を防ぐが、コード側の検証も残す(C4)。`filter` 内の
不正な要素(語彙に無いタグ・不明な mobility 値など)は**その要素だけ落として
実行**し、落とした事実を `RecommendActResult.dropped` で結果へ返す。全要素が
落ちても `filter` なし(絞り込みなし)で実行する — 0 件応答にしない。
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.domains.conversation.ask_execution import execute_ask_user
from app.domains.conversation.events import EventSinkLike, emit, state_event
from app.domains.conversation.guards import (
    MAX_ASK_STREAK,
    MAX_ASK_USER_PER_TURN,
    has_repeated_ngram,
)
from app.domains.conversation.state import TurnState
from app.domains.conversation.tool_ports import ConversationToolPort
from app.domains.conversation.types import AskUserArgs, AskUserOption, Slot, slot_values
from app.domains.recommendation.types import Mobility, RecommendationProfile, RecommendFilter

logger = logging.getLogger("app.conversation.recommend_agent")

RECOMMEND_AGENT_EXPECTED_TOKENS = 400
RECOMMEND_AGENT_MAX_TOKENS = int(RECOMMEND_AGENT_EXPECTED_TOKENS * 1.5)
RECOMMEND_AGENT_WALLCLOCK_SEC = 60.0
MAX_FILTER_TAGS = 6
MAX_ASSUMPTIONS = 5

_MOBILITY_VALUES: tuple[str, ...] = tuple(value.value for value in Mobility)

_SYSTEM_PROMPT_TEMPLATE = """あなたは鳥海山観光ガイダンスの
レコメンドサブエージェントです。
メインエージェントからの指示(自然文)とプロフィールをもとに、既存の推薦処理
へ渡す filter を決めてください。指定された JSON Schema の
JSON 1 個(thought + action)だけを出力し、説明文や Markdown は出しません。

出力フィールドは thought → action の順で埋めます。thought は結論を出す前の
1〜2 文の日本語です。action.tool は次のとおりです。
- done: filter と assumptions を確定する。action.args は filter と
  assumptions です。
{ask_user_section}

filter の項目:
- tags: 推薦の手がかりになった生タグです。0〜{max_tags} 個。
  指示・プロフィールの選好から読み取れる分だけを、次の語彙からそのまま選び
  ます(語彙に無い語は書けません): {tag_vocabulary}
- mobility: **歩行耐性です。移動手段ではありません。**
  「車で回ります」「車で行きたい」は移動手段の話であり、現地でどれだけ歩け
  るかとは無関係です。**移動手段の言及だけでは mobility を書きません。**
  値は次の3つだけです: {mobility_vocabulary}
  (avoid_walk=歩きたくない / short_walk_ok=短い徒歩は平気 /
  hike_ok=登山も平気)。歩行耐性が指示やプロフィールから分からなければ
  null にしてください(プロフィールの mobility があればそちらが使われます)。
- weather_fit: 雨天でも楽しめる地点だけに絞りたいときだけ true。それ以外は
  null。
- area: 地名で絞りたいときだけ書きます(例: "象潟")。それ以外は null。
- day: 旅程の日番号で絞りたいときだけ書きます。それ以外は null。

assumptions: 情報が薄いまま置いた仮定を日本語の短文で列挙します
(例: "同行者の情報が無いため、特定の対象者向けには絞りませんでした")。
仮定を置かなかった場合は空配列にします。

境界:
- {ask_user_boundary}情報が薄くても、最も妥当な仮定を置いて filter を決め、
  置いた仮定は必ず assumptions に書いてください(推薦そのものを止めない)。
- 指示・プロフィールから読み取れない項目は filter に書かず null のままに
  します。無理に埋めません。
"""

_ASK_USER_SECTION_TEMPLATE = """- ask_user: プロフィールが著しく薄く、このまま
  では的外れになりそうなときだけ、1 回だけ聞き返せます。action.args =
  {{"slot":スロット名({slot_vocabulary} のどれか),"reason":質問文(専用
  フォームにそのまま表示されます),"options":[{{"label":選択肢の表示文,
  "value":選択肢を識別する短い値}}](2〜4 個)}}。回答は次の周に観測として
  返ります。念のための確認には使いません。"""


class GenerationPort(Protocol):
    async def generate(
        self,
        messages: Sequence[dict[str, str]],
        *,
        temperature: float = 0.2,
        max_tokens: int = 1024,
        extra_body: dict[str, Any] | None = None,
    ) -> str: ...


class _RecommendActAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: str
    args: dict[str, Any] = Field(default_factory=dict)


class _RecommendActTurn(BaseModel):
    """guided JSON の構造だけを検査する(filter/assumptions の中身は素通し)。

    guided decoding のスキーマで大半は防げるが、モックや逸脱応答に備えて
    `filter`/`assumptions` はここでは型付けせず、`_sanitize_filter`/
    `_sanitize_assumptions` が要素単位で検証する(C4)。
    """

    model_config = ConfigDict(extra="forbid")

    thought: str
    action: _RecommendActAction


@dataclass(frozen=True, slots=True)
class RecommendActResult:
    """SA の act(`done`)結果。`RecommendArgs.filter` へそのまま渡せる。"""

    filter: RecommendFilter
    assumptions: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)
    degraded: bool = False


async def run_recommend_subagent(
    *,
    instruction: str,
    profile: RecommendationProfile,
    tag_vocabulary: Sequence[str],
    client: GenerationPort,
    event_sink: EventSinkLike = None,
    wallclock_sec: float = RECOMMEND_AGENT_WALLCLOCK_SEC,
    state: TurnState | None = None,
    tools: ConversationToolPort | None = None,
    step_id: int = 0,
) -> RecommendActResult:
    """`instruction` → `filter` の翻訳(§4 (a))。

    `state`/`tools` を渡すと `ask_user`(§4 (b))を許す: プロフィールが薄すぎる
    と判断すれば 1 回だけ質問し(A7)、回答 → `update_profile` 再実行 →
    更新後プロフィールで再判定してから `done` する。渡さない場合(既存の
    呼び出し元・テスト)は `ask_user` 分岐自体をスキーマから外し、従来どおり
    `done` の単発判定のまま動く。
    """

    # 2026-08-04 レビュー是正(Medium・裁定16): R4(メイン・SA 合算で
    # 1 ターン 2 回まで)に到達済みなら、この SA の guided schema からも
    # `ask_user` を外す。実行時の共通ガード(`execute_ask_user` →
    # `evaluate_ask_user`)は既に超過を拒否するが、それだけだと SA は
    # 無効な質問を選んで 1 周を浪費できてしまう(§10 R4 の「スキーマから
    # 外す」を字義どおり満たす)。
    ask_enabled = (
        state is not None
        and tools is not None
        and state.ask_user_count < MAX_ASK_USER_PER_TURN
        and state.ask_streak < MAX_ASK_STREAK
    )
    asked = False
    current_profile = profile
    vocabulary = list(dict.fromkeys(tag_vocabulary))
    # 2026-08-04 レビュー是正(High・裁定3a): 質問した場合、再判定プロンプトに
    # 「質問文 + ユーザーの回答」を含める。`current_profile` の更新だけでは、
    # `ProfileDelta` のフィールドではない自由記述の回答(dates/origin 等)が
    # 完全に失われうる(§4 (b) の「回答は本サブエージェントの act に返る」を
    # 字義どおり満たすため、生の Q&A を必ず持ち越す)。
    qa_pairs: list[tuple[str, str]] = []

    while True:
        await emit(
            event_sink,
            state_event(
                "step",
                tool="recommend",
                status="progress",
                label_ja="指示を条件に翻訳しています",
            ),
        )
        allow_ask = ask_enabled and not asked
        messages = build_recommend_agent_messages(
            instruction=instruction,
            profile=current_profile,
            tag_vocabulary=vocabulary,
            allow_ask_user=allow_ask,
            qa_pairs=qa_pairs,
        )
        schema = recommend_agent_guided_schema(vocabulary, allow_ask_user=allow_ask)
        turn = await _call(
            client,
            messages,
            schema,
            wallclock_sec=wallclock_sec,
            allow_ask_user=allow_ask,
        )
        if turn is None:
            return RecommendActResult(
                filter=RecommendFilter(),
                assumptions=[
                    "指示をうまく解釈できなかったため、条件を絞らずおすすめしました。"
                ],
                dropped=[],
                degraded=True,
            )

        if allow_ask and turn.action.tool == "ask_user":
            asked = True  # A7: 質問できるのは 1 回まで。次周は allow_ask=False。
            question = _parse_recommend_ask_args(turn.action.args)
            if question is not None:
                assert state is not None and tools is not None  # allow_ask が真の前提
                outcome = await execute_ask_user(
                    state,
                    tools,
                    question,
                    step_id=step_id,
                    client=client,
                    event_sink=event_sink,
                )
                if outcome.executed and outcome.answer_text is not None:
                    current_profile = RecommendationProfile.model_validate(
                        state.profile.model_dump(mode="python")
                    )
                    qa_pairs.append((question.reason, outcome.answer_text))
            continue

        sanitized_filter, dropped = _sanitize_filter(
            turn.action.args.get("filter"), vocabulary
        )
        assumptions = _sanitize_assumptions(turn.action.args.get("assumptions"))
        return RecommendActResult(
            filter=RecommendFilter.model_validate(sanitized_filter),
            assumptions=assumptions,
            dropped=dropped,
            degraded=False,
        )


def _parse_recommend_ask_args(raw_args: Mapping[str, Any]) -> AskUserArgs | None:
    """SA の `ask_user{slot,reason,options}` を `AskUserArgs` へ正規化する。

    guided decoding のスキーマがほとんどを防ぐが、モック・逸脱応答に備えて
    ここでも検証する。壊れていれば None を返し、呼び出し元は質問を実行せず
    次周(`allow_ask=False`)で `done` を確定させる(推薦を止めない。NFR-5)。
    """

    slot_raw = raw_args.get("slot")
    reason_raw = raw_args.get("reason")
    options_raw = raw_args.get("options")
    if not isinstance(slot_raw, str) or not isinstance(reason_raw, str):
        return None
    if not isinstance(options_raw, list):
        return None
    try:
        slot = Slot(slot_raw)
        options = [
            AskUserOption(
                label=str(option.get("label", "")), value=str(option.get("value", ""))
            )
            for option in options_raw
            if isinstance(option, Mapping)
        ]
        return AskUserArgs(kind="preference", slot=slot, reason=reason_raw, options=options)
    except (ValueError, ValidationError):
        return None


async def _call(
    client: GenerationPort,
    messages: list[dict[str, str]],
    schema: dict[str, Any],
    *,
    wallclock_sec: float,
    allow_ask_user: bool = False,
) -> _RecommendActTurn | None:
    """1 回の guided JSON 呼び出し。契約違反時は 1 回だけ再試行する。"""

    allowed_tools = {"done", "ask_user"} if allow_ask_user else {"done"}
    attempt_messages = messages
    for attempt in range(2):
        try:
            async with asyncio.timeout(wallclock_sec):
                raw = await client.generate(
                    attempt_messages,
                    temperature=0.2,
                    max_tokens=RECOMMEND_AGENT_MAX_TOKENS,
                    extra_body={
                        "response_format": {
                            "type": "json_schema",
                            "json_schema": {
                                "name": "conversation_recommend_agent",
                                "strict": True,
                                "schema": schema,
                            },
                        }
                    },
                )
            if has_repeated_ngram(raw):
                raise ValueError("同一 n-gram の反復を検知しました")
            parsed = _RecommendActTurn.model_validate(json.loads(raw))
            if parsed.action.tool not in allowed_tools:
                raise ValueError(f"未対応の tool です: {parsed.action.tool}")
            return parsed
        except asyncio.CancelledError:
            # キャンセルは呼び出し元(main_agent → pipeline)へ伝播させる。
            raise
        except (json.JSONDecodeError, ValidationError, ValueError) as exc:
            if attempt == 0:
                attempt_messages = [dict(value) for value in messages]
                attempt_messages[0] = dict(attempt_messages[0])
                attempt_messages[0]["content"] += (
                    "\n再試行です。前回は契約違反でした。"
                    f"JSON Schema に厳密に従ってください。原因: {str(exc)[:240]}"
                )
                continue
            logger.warning("recommend_agent_parse_failed", extra={"reason": str(exc)})
            return None
        except Exception as exc:  # noqa: BLE001 - 補助ステップの失敗で対話を止めない(NFR-5)
            logger.warning(
                "recommend_agent_generation_failed", extra={"reason": str(exc)}
            )
            return None
    return None  # pragma: no cover - for 文で必ず return する


def build_recommend_agent_messages(
    *,
    instruction: str,
    profile: RecommendationProfile,
    tag_vocabulary: list[str],
    allow_ask_user: bool = False,
    qa_pairs: Sequence[tuple[str, str]] = (),
) -> list[dict[str, str]]:
    """SA のプロンプトを組み立てる。タグ 80 語・mobility の enum はここにだけ載る。

    `qa_pairs`(2026-08-04、レビュー是正・裁定3a): このターン内で既に聞いた
    質問と回答の生テキストを③として必ず含める。`profile` の更新だけでは
    `ProfileDelta` に無いフィールド(dates/origin 等)の回答が消えるため。
    """

    system = _SYSTEM_PROMPT_TEMPLATE.format(
        max_tags=MAX_FILTER_TAGS,
        tag_vocabulary=(
            "、".join(tag_vocabulary) if tag_vocabulary else "(語彙が空のため tags は使えません)"
        ),
        mobility_vocabulary="、".join(f'"{value}"' for value in _MOBILITY_VALUES),
        ask_user_section=(
            _ASK_USER_SECTION_TEMPLATE.format(slot_vocabulary=" | ".join(slot_values()))
            if allow_ask_user
            else ""
        ),
        ask_user_boundary=(
            "質問できるのは 1 回までです。2 回目以降は聞かず、"
            if allow_ask_user
            else "質問はできません。"
        ),
    )
    sections = [
        "① メインエージェントからの指示:\n" + instruction,
        "② プロフィール:\n" + _compact_json(profile.model_dump(mode="json")),
    ]
    if qa_pairs:
        qa_text = "\n".join(
            f"Q: {question}\nA: {answer}" for question, answer in qa_pairs
        )
        sections.append("③ このターンで既に聞いた質問と回答:\n" + qa_text)
    dynamic = "\n\n".join(sections)
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": dynamic},
    ]


def recommend_agent_guided_schema(
    tag_vocabulary: list[str],
    *,
    allow_ask_user: bool = False,
) -> dict[str, Any]:
    """xgrammar 互換の schema。`uniqueItems` は使わない(未実装のため)。

    `allow_ask_user=False`(既定。既存の呼び出し元との完全互換)では
    `action` は `tool` enum が `["done"]` の平坦なオブジェクトのまま返す。
    `True` のときだけ `done`/`ask_user` の anyOf 排他に広げる(§4 (a))。
    """

    tag_item_schema: dict[str, Any] = (
        {"type": "string", "enum": tag_vocabulary} if tag_vocabulary else {"type": "string"}
    )
    filter_schema = {
        "type": "object",
        "properties": {
            "tags": {
                "type": "array",
                "items": tag_item_schema,
                "maxItems": MAX_FILTER_TAGS,
            },
            "mobility": {
                "anyOf": [
                    {"type": "null"},
                    {"type": "string", "enum": list(_MOBILITY_VALUES)},
                ]
            },
            "weather_fit": {"anyOf": [{"type": "null"}, {"type": "boolean"}]},
            "area": {"anyOf": [{"type": "null"}, {"type": "string", "minLength": 1}]},
            "day": {"anyOf": [{"type": "null"}, {"type": "integer", "minimum": 1}]},
        },
        "required": ["tags", "mobility", "weather_fit", "area", "day"],
        "additionalProperties": False,
    }
    done_branch = {
        "type": "object",
        "properties": {
            "tool": {"type": "string", "enum": ["done"]},
            "args": {
                "type": "object",
                "properties": {
                    "filter": filter_schema,
                    "assumptions": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1},
                        "maxItems": MAX_ASSUMPTIONS,
                    },
                },
                "required": ["filter", "assumptions"],
                "additionalProperties": False,
            },
        },
        "required": ["tool", "args"],
        "additionalProperties": False,
    }
    action_schema: dict[str, Any] = done_branch
    if allow_ask_user:
        ask_user_branch = {
            "type": "object",
            "properties": {
                "tool": {"type": "string", "enum": ["ask_user"]},
                "args": {
                    "type": "object",
                    "properties": {
                        "slot": {"type": "string", "enum": slot_values()},
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
                    "required": ["slot", "reason", "options"],
                    "additionalProperties": False,
                },
            },
            "required": ["tool", "args"],
            "additionalProperties": False,
        }
        action_schema = {"anyOf": [done_branch, ask_user_branch]}
    return {
        "type": "object",
        "properties": {
            "thought": {"type": "string", "minLength": 1},
            "action": action_schema,
        },
        "required": ["thought", "action"],
        "additionalProperties": False,
    }


def _sanitize_filter(
    raw: Any, tag_vocabulary: Sequence[str]
) -> tuple[dict[str, Any], list[str]]:
    """C4: filter を要素単位で検証する。無効な要素はその要素だけ落とす。

    guided decoding の enum で大半は防げるが、モック・スキーマ逸脱応答にも
    備えたコード側の検証である。全要素が無効でも空 dict を返す(=
    `RecommendFilter()` と等価。絞り込み無しで実行する。0 件応答にしない)。
    """

    vocabulary = set(tag_vocabulary)
    dropped: list[str] = []
    if not isinstance(raw, Mapping):
        return {}, dropped

    sanitized: dict[str, Any] = {}

    tags_raw = raw.get("tags")
    if isinstance(tags_raw, list):
        valid_tags: list[str] = []
        for tag in tags_raw:
            if not isinstance(tag, str):
                dropped.append("タグの形式が不正なため除外しました")
            elif tag not in vocabulary:
                dropped.append(f"タグ「{tag}」は語彙にないため除外しました")
            elif tag not in valid_tags:
                valid_tags.append(tag)
        if valid_tags:
            sanitized["tags"] = valid_tags
    elif tags_raw is not None:
        dropped.append("tags の形式が不正なため除外しました")

    mobility_raw = raw.get("mobility")
    if isinstance(mobility_raw, str):
        if mobility_raw in _MOBILITY_VALUES:
            sanitized["mobility"] = mobility_raw
        else:
            dropped.append(f"mobility「{mobility_raw}」は不明な値のため除外しました")
    elif mobility_raw is not None:
        dropped.append("mobility の形式が不正なため除外しました")

    weather_fit_raw = raw.get("weather_fit")
    if isinstance(weather_fit_raw, bool):
        sanitized["weather_fit"] = weather_fit_raw
    elif weather_fit_raw is not None:
        dropped.append("weather_fit の形式が不正なため除外しました")

    area_raw = raw.get("area")
    if isinstance(area_raw, str) and area_raw.strip():
        sanitized["area"] = area_raw.strip()
    elif area_raw is not None:
        dropped.append("area の形式が不正なため除外しました")

    day_raw = raw.get("day")
    if isinstance(day_raw, int) and not isinstance(day_raw, bool) and day_raw >= 1:
        sanitized["day"] = day_raw
    elif day_raw is not None:
        dropped.append("day の形式が不正なため除外しました")

    return sanitized, dropped


def _sanitize_assumptions(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    result: list[str] = []
    for value in raw:
        if isinstance(value, str) and value.strip():
            result.append(value.strip())
    return result[:MAX_ASSUMPTIONS]


def _compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
