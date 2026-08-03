"""④ レコメンドサブエージェント(§4)。

`Docs/30_design/agent_react_architecture.md` §4 が仕様。メインエージェントが
書いた `instruction`(自然言語)を、既存の推薦処理(`tool_adapters.recommend`
→ `RecommendationService`。**内部は一切変更しない**)がそのまま読める
`filter`(タグ・mobility・weather_fit・area・day)へ、1 回の guided JSON で
翻訳する。

段3では SA の Tool は `done` だけ(`ask_user` は段5で追加する。§4 の決定どお
り、enum にも含めずプロンプトにも書かない)。

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

from app.domains.conversation.events import EventSinkLike, emit, state_event
from app.domains.conversation.guards import has_repeated_ngram
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
へ渡す filter を 1 回の JSON で決めてください。指定された JSON Schema の
JSON 1 個(thought + action)だけを出力し、説明文や Markdown は出しません。

出力フィールドは thought → action の順で埋めます。thought は結論を出す前の
1〜2 文の日本語です。action.tool は "done" 固定です
(質問する手段は今回ありません。薄い情報でも仮定して推薦してください)。
action.args は filter と assumptions です。

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
- 質問はできません。情報が薄くても、最も妥当な仮定を置いて filter を決め、
  置いた仮定は必ず assumptions に書いてください(推薦そのものを止めない)。
- 指示・プロフィールから読み取れない項目は filter に書かず null のままに
  します。無理に埋めません。
"""


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
) -> RecommendActResult:
    """`instruction` → `filter` の 1 回翻訳(§4 (a))。"""

    await emit(
        event_sink,
        state_event(
            "step",
            tool="recommend",
            status="progress",
            label_ja="指示を条件に翻訳しています",
        ),
    )
    vocabulary = list(dict.fromkeys(tag_vocabulary))
    messages = build_recommend_agent_messages(
        instruction=instruction, profile=profile, tag_vocabulary=vocabulary
    )
    schema = recommend_agent_guided_schema(vocabulary)
    turn = await _call(client, messages, schema, wallclock_sec=wallclock_sec)
    if turn is None:
        return RecommendActResult(
            filter=RecommendFilter(),
            assumptions=[
                "指示をうまく解釈できなかったため、条件を絞らずおすすめしました。"
            ],
            dropped=[],
            degraded=True,
        )
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


async def _call(
    client: GenerationPort,
    messages: list[dict[str, str]],
    schema: dict[str, Any],
    *,
    wallclock_sec: float,
) -> _RecommendActTurn | None:
    """1 回の guided JSON 呼び出し。契約違反時は 1 回だけ再試行する。"""

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
            if parsed.action.tool != "done":
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
) -> list[dict[str, str]]:
    """SA のプロンプトを組み立てる。タグ 80 語・mobility の enum はここにだけ載る。"""

    system = _SYSTEM_PROMPT_TEMPLATE.format(
        max_tags=MAX_FILTER_TAGS,
        tag_vocabulary=(
            "、".join(tag_vocabulary) if tag_vocabulary else "(語彙が空のため tags は使えません)"
        ),
        mobility_vocabulary="、".join(f'"{value}"' for value in _MOBILITY_VALUES),
    )
    dynamic = "\n\n".join(
        [
            "① メインエージェントからの指示:\n" + instruction,
            "② プロフィール:\n" + _compact_json(profile.model_dump(mode="json")),
        ]
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": dynamic},
    ]


def recommend_agent_guided_schema(tag_vocabulary: list[str]) -> dict[str, Any]:
    """xgrammar 互換の schema。`uniqueItems` は使わない(未実装のため)。"""

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
    return {
        "type": "object",
        "properties": {
            "thought": {"type": "string", "minLength": 1},
            "action": {
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
            },
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
