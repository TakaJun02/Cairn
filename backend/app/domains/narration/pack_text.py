"""パック用ナレーションの素材取得、単一プロンプト生成、出力検証。"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.llm import GenerationClient

PACK_TEXT_MAX_TOKENS = 512
PACK_TEXT_REGENERATION_COUNT = 1
RAIN_ALTERNATIVE_MAX_DURATION_SEC = 20 * 60

FORBIDDEN_EXPRESSIONS = (
    "この資料によると",
    "文脈から",
    "以下に示します",
)


class NarrationRole(StrEnum):
    VISIT = "visit"
    PASS_BY = "pass_by"


class NarrationVariant(StrEnum):
    BASE = "base"
    WEATHER_CLOUDY = "weather_cloudy"
    WEATHER_RAIN = "weather_rain"
    CONGESTION_MID = "congestion_mid"
    CONGESTION_HIGH = "congestion_high"


TARGET_LENGTH_LIMITS: dict[tuple[NarrationRole, NarrationVariant], tuple[int, int]] = {
    (NarrationRole.VISIT, NarrationVariant.BASE): (200, 300),
    (NarrationRole.PASS_BY, NarrationVariant.BASE): (80, 160),
    (NarrationRole.VISIT, NarrationVariant.WEATHER_CLOUDY): (40, 100),
    (NarrationRole.VISIT, NarrationVariant.WEATHER_RAIN): (40, 100),
    (NarrationRole.VISIT, NarrationVariant.CONGESTION_MID): (40, 100),
    (NarrationRole.VISIT, NarrationVariant.CONGESTION_HIGH): (40, 100),
}

VALID_LENGTH_LIMITS: dict[tuple[NarrationRole, NarrationVariant], tuple[int, int]] = {
    (NarrationRole.VISIT, NarrationVariant.BASE): (120, 420),
    (NarrationRole.PASS_BY, NarrationVariant.BASE): (60, 240),
    (NarrationRole.VISIT, NarrationVariant.WEATHER_CLOUDY): (20, 140),
    (NarrationRole.VISIT, NarrationVariant.WEATHER_RAIN): (20, 140),
    (NarrationRole.VISIT, NarrationVariant.CONGESTION_MID): (20, 140),
    (NarrationRole.VISIT, NarrationVariant.CONGESTION_HIGH): (20, 140),
}

_ROLE_INSTRUCTIONS = {
    NarrationRole.VISIT: (
        "利用者は今この地点にいます。その場で見聞きしているような現在形で案内する。"
    ),
    NarrationRole.PASS_BY: (
        "利用者は車などで今この地点の近くを通過中です。短時間で聞き終えられる現在形の案内にする。"
    ),
}

_VARIANT_INSTRUCTIONS = {
    NarrationVariant.BASE: (
        "平常時の案内本体。特定の天気や混雑には触れず、地点の魅力と必要な安全情報を伝える。"
    ),
    NarrationVariant.WEATHER_CLOUDY: (
        "曇天時に本編の後へ流す短い注記。本編の地点紹介を繰り返さず、"
        "雨や混雑には触れず、天候変化、見通し、必要な行動を短い3文で自然に伝える。"
    ),
    NarrationVariant.WEATHER_RAIN: (
        "雨天時に本編の後へ流す短い注記。本編の地点紹介を繰り返さず、"
        "濡れた足元などの注意と、提示された場合は代替候補を伝える。"
    ),
    NarrationVariant.CONGESTION_MID: (
        "やや混雑している時に本編の後へ流す短い注記。本編の地点紹介を繰り返さず、"
        "周囲への配慮だけを自然に伝える。"
    ),
    NarrationVariant.CONGESTION_HIGH: (
        "混雑している時に本編の後へ流す短い注記。本編の地点紹介を繰り返さず、"
        "安全な間隔や譲り合いだけを自然に促す。"
    ),
}

_DIFFICULTY_LABELS = {
    "no_walk": "徒歩移動をほぼ必要としない",
    "short_walk": "短い徒歩移動がある",
    "long_walk": "長めの徒歩移動がある",
    "hike": "登山・ハイキング相当の移動がある",
}

_WEATHER_FIT_LABELS = {
    "indoor": "屋内で過ごせる",
    "rain_ok": "雨天でも訪問しやすい",
    "rain_fair": "小雨なら訪問可能",
    "rain_poor": "雨天には向きにくい",
    "rain_unsafe": "雨天時は安全上訪問を避ける",
}

_OVERLAY_VARIANTS = frozenset(
    {
        NarrationVariant.WEATHER_CLOUDY,
        NarrationVariant.WEATHER_RAIN,
        NarrationVariant.CONGESTION_MID,
        NarrationVariant.CONGESTION_HIGH,
    }
)

# role・variant・季節・素材はすべてパラメータで、この全文テンプレートだけを使う。
PACK_TEXT_TEMPLATE = """あなたは鳥海山周辺の現地音声ガイド原稿を作る担当です。
与えられた素材にある事実だけを使い、日本語の読み上げ本文を1本作ってください。

【出力条件】
- role: {role}
- 訪問文脈: {role_instruction}
- variant: {variant}
- 状況の指示: {variant_instruction}
- 目標文字数: {target_min}〜{target_max}字
- 合成上の絶対条件: {composition_instruction}
- 旅程上の位置: {itinerary_position}
- 対象月: {month_context}
- {retry_instruction}
- 現在形で書き、出力は読み上げ本文だけにする
- 見出し、Markdown、箇条書き、前置き、注釈は出力しない
- 「この資料によると」「文脈から」「以下に示します」は使わない

【地点の構造化素材】
- 地点名: {spot_name}
- 評判・特徴: {social_proof}
- タグ: {tags}
- 訪問難易度: {visit_difficulty}
- 雨天適性: {weather_fit}
- 営業時間: {open_hours}
- 季節閉鎖月: {season_closed_months}
{rain_alternative_context}

【地点に直接紐づく知識本文】
<knowledge>
{knowledge_body}
</knowledge>
"""

_FENCED_CODE = re.compile(r"```(?:[^\n`]*)\n?(.*?)```", flags=re.DOTALL)
_MARKDOWN_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_LINE_MARKER = re.compile(r"^\s{0,3}(?:#{1,6}\s+|>\s?|[-+*]\s+|\d+[.)]\s+)")
_HTML_TAG = re.compile(r"</?[^>]+>")
_INLINE_MARKER = re.compile(r"[*_~`]")


@dataclass(frozen=True, slots=True)
class SpotNarrationSource:
    spot_id: str
    name_ja: str
    social_proof_ja: str | None
    tags_ja: tuple[str, ...]
    knowledge_body: str
    visit_difficulty: str
    season_closed_months: tuple[int, ...]
    weather_fit: str
    open_hours: Any | None


@dataclass(frozen=True, slots=True)
class ItineraryPosition:
    day: int
    position: int
    next_spot_name: str | None = None

    def __post_init__(self) -> None:
        if self.day < 1 or self.position < 1:
            raise ValueError("day と position は 1 以上にしてください")


@dataclass(frozen=True, slots=True)
class RainCandidate:
    spot_id: str
    name_ja: str
    weather_fit: str


@dataclass(frozen=True, slots=True)
class NarrationTravelTime:
    from_spot_id: str
    to_spot_id: str
    mode: str
    duration_sec: int
    distance_m: int


@dataclass(frozen=True, slots=True)
class RainAlternative:
    spot_id: str
    name_ja: str
    travel_min: int
    distance_m: int


@dataclass(frozen=True, slots=True)
class PackNarrationRequest:
    spot_id: str
    role: NarrationRole | str
    variant: NarrationVariant | str
    pack_spot_ids: tuple[str, ...]
    itinerary_position: ItineraryPosition | None = None
    month: int | None = None

    def __post_init__(self) -> None:
        role = NarrationRole(self.role)
        variant = NarrationVariant(self.variant)
        if (role, variant) not in TARGET_LENGTH_LIMITS:
            raise ValueError(f"role={role.value} では variant={variant.value} を生成できません")
        if not self.spot_id:
            raise ValueError("spot_id は空にできません")
        if self.spot_id not in self.pack_spot_ids:
            raise ValueError("spot_id は同じパックの spot_id 群に含めてください")
        if self.month is not None and not 1 <= self.month <= 12:
            raise ValueError("month は 1〜12 にしてください")
        object.__setattr__(self, "role", role)
        object.__setattr__(self, "variant", variant)
        object.__setattr__(self, "pack_spot_ids", tuple(dict.fromkeys(self.pack_spot_ids)))


@dataclass(frozen=True, slots=True)
class NarrationValidation:
    accepted: bool
    text: str
    char_count: int
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class NarrationGenerationResult:
    text: str | None
    narration_state: Literal["ok", "failed"]
    audio_state: Literal["pending", "skipped"]
    attempts: int
    error: str | None = None


class PackTextGenerationPort(Protocol):
    async def generate(
        self,
        messages: Sequence[dict[str, str]],
        *,
        temperature: float = 0.2,
        max_tokens: int = 1024,
        extra_body: dict[str, Any] | None = None,
    ) -> str: ...


class PackNarrationRepositoryPort(Protocol):
    async def load_source(self, spot_id: str) -> SpotNarrationSource: ...

    async def find_rain_alternative(
        self,
        from_spot_id: str,
        pack_spot_ids: Sequence[str],
    ) -> RainAlternative | None: ...


class PackNarrationSourceError(RuntimeError):
    """地点に直接紐づく原稿素材を一意に取得できない。"""


class PackNarrationRepository:
    """`spot_id` 直引きだけを行うパック原稿用 read-only repository。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def load_source(self, spot_id: str) -> SpotNarrationSource:
        from app.db_models import KnowledgeDocument, Spot

        rows = (
            await self.session.execute(
                select(
                    Spot.spot_id,
                    Spot.name_ja,
                    Spot.social_proof,
                    Spot.tags_ja,
                    Spot.visit_difficulty,
                    Spot.season_closed_months,
                    Spot.weather_fit,
                    Spot.open_hours,
                    KnowledgeDocument.body,
                )
                .join(KnowledgeDocument, KnowledgeDocument.spot_id == Spot.spot_id)
                .where(Spot.spot_id == spot_id)
                .order_by(KnowledgeDocument.doc_id)
            )
        ).all()
        if len(rows) != 1:
            raise PackNarrationSourceError(
                f"spot_id={spot_id} の knowledge_document は 1 件必要です: {len(rows)} 件"
            )
        row = rows[0]
        return SpotNarrationSource(
            spot_id=row.spot_id,
            name_ja=row.name_ja,
            social_proof_ja=_japanese_value(row.social_proof),
            tags_ja=tuple(row.tags_ja),
            knowledge_body=row.body,
            visit_difficulty=row.visit_difficulty,
            season_closed_months=tuple(int(value) for value in row.season_closed_months),
            weather_fit=row.weather_fit,
            open_hours=row.open_hours,
        )

    async def find_rain_alternative(
        self,
        from_spot_id: str,
        pack_spot_ids: Sequence[str],
    ) -> RainAlternative | None:
        from app.db_models import Spot, TravelTime

        unique_ids = list(dict.fromkeys(pack_spot_ids))
        if not unique_ids:
            return None
        spot_rows = (
            await self.session.execute(
                select(Spot.spot_id, Spot.name_ja, Spot.weather_fit)
                .where(Spot.spot_id.in_(unique_ids))
                .order_by(Spot.spot_id)
            )
        ).all()
        travel_rows = (
            await self.session.execute(
                select(
                    TravelTime.from_spot_id,
                    TravelTime.to_spot_id,
                    TravelTime.mode,
                    TravelTime.duration_sec,
                    TravelTime.distance_m,
                )
                .where(
                    TravelTime.from_spot_id == from_spot_id,
                    TravelTime.to_spot_id.in_(unique_ids),
                    TravelTime.mode == "car",
                )
                .order_by(TravelTime.to_spot_id)
            )
        ).all()
        return select_rain_alternative(
            from_spot_id=from_spot_id,
            pack_spots=[
                RainCandidate(
                    spot_id=row.spot_id,
                    name_ja=row.name_ja,
                    weather_fit=row.weather_fit,
                )
                for row in spot_rows
            ],
            travel_times=[
                NarrationTravelTime(
                    from_spot_id=row.from_spot_id,
                    to_spot_id=row.to_spot_id,
                    mode=row.mode,
                    duration_sec=int(row.duration_sec),
                    distance_m=int(row.distance_m),
                )
                for row in travel_rows
            ],
        )


class PackTextGenerator:
    def __init__(
        self,
        repository: PackNarrationRepositoryPort,
        *,
        client: PackTextGenerationPort | None = None,
    ) -> None:
        self.repository = repository
        self.client = client or GenerationClient()

    async def generate(self, request: PackNarrationRequest) -> NarrationGenerationResult:
        source = await self.repository.load_source(request.spot_id)
        alternative = None
        if request.variant is NarrationVariant.WEATHER_RAIN:
            alternative = await self.repository.find_rain_alternative(
                request.spot_id,
                request.pack_spot_ids,
            )

        failure_reason: str | None = None
        for attempt in range(PACK_TEXT_REGENERATION_COUNT + 1):
            messages = build_pack_text_messages(
                source,
                request,
                rain_alternative=alternative,
                retry_reason=failure_reason,
            )
            raw_text = await self.client.generate(
                messages,
                temperature=0.2,
                max_tokens=PACK_TEXT_MAX_TOKENS,
            )
            checked = validate_narration_text(
                raw_text,
                role=request.role,
                variant=request.variant,
            )
            if checked.accepted:
                if (
                    request.variant is NarrationVariant.WEATHER_RAIN
                    and alternative is not None
                    and alternative.name_ja not in checked.text
                ):
                    failure_reason = f"雨天時の代替候補「{alternative.name_ja}」が含まれていません"
                else:
                    return NarrationGenerationResult(
                        text=checked.text,
                        narration_state="ok",
                        audio_state="pending",
                        attempts=attempt + 1,
                    )
            else:
                failure_reason = checked.reason

        return NarrationGenerationResult(
            text=None,
            narration_state="failed",
            audio_state="skipped",
            attempts=PACK_TEXT_REGENERATION_COUNT + 1,
            error=failure_reason or "原稿の検証に失敗しました",
        )


def build_pack_text_messages(
    source: SpotNarrationSource,
    request: PackNarrationRequest,
    *,
    rain_alternative: RainAlternative | None = None,
    retry_reason: str | None = None,
) -> list[dict[str, str]]:
    """全 role・variant を同じテンプレートから構築する。"""

    target_min, target_max = TARGET_LENGTH_LIMITS[(request.role, request.variant)]
    prompt = PACK_TEXT_TEMPLATE.format(
        role=request.role.value,
        role_instruction=_ROLE_INSTRUCTIONS[request.role],
        variant=request.variant.value,
        variant_instruction=_VARIANT_INSTRUCTIONS[request.variant],
        target_min=target_min,
        target_max=target_max,
        composition_instruction=_composition_instruction(request.variant),
        itinerary_position=_position_text(request.itinerary_position),
        month_context=_month_text(request.month, source.season_closed_months),
        retry_instruction=(
            f"前回は「{retry_reason}」で不合格。条件を直して再生成する"
            if retry_reason
            else "初回生成。すべての条件を満たす"
        ),
        spot_name=source.name_ja,
        social_proof=source.social_proof_ja or "情報なし",
        tags="、".join(source.tags_ja) or "情報なし",
        visit_difficulty=_DIFFICULTY_LABELS.get(
            source.visit_difficulty,
            source.visit_difficulty,
        ),
        weather_fit=_WEATHER_FIT_LABELS.get(source.weather_fit, source.weather_fit),
        open_hours=_serialize_fact(source.open_hours),
        season_closed_months=(
            "、".join(f"{month}月" for month in source.season_closed_months)
            if source.season_closed_months
            else "通年の月単位閉鎖なし"
        ),
        rain_alternative_context=_rain_alternative_text(
            request.variant,
            rain_alternative,
        ),
        knowledge_body=source.knowledge_body,
    )
    return [{"role": "user", "content": prompt}]


def validate_narration_text(
    value: str,
    *,
    role: NarrationRole | str,
    variant: NarrationVariant | str,
) -> NarrationValidation:
    """Markdown を除去してから、TTS 前の受入規則を適用する。"""

    parsed_role = NarrationRole(role)
    parsed_variant = NarrationVariant(variant)
    limits = VALID_LENGTH_LIMITS.get((parsed_role, parsed_variant))
    if limits is None:
        raise ValueError(
            f"role={parsed_role.value} では variant={parsed_variant.value} を検証できません"
        )

    text = strip_markdown_for_speech(value)
    char_count = len(text)
    if not text:
        return NarrationValidation(False, text, char_count, "原稿が空です")
    if char_count < 40 and text.startswith(("申し訳", "できません")):
        return NarrationValidation(False, text, char_count, "短い拒否応答です")
    minimum, maximum = limits
    if not minimum <= char_count <= maximum:
        return NarrationValidation(
            False,
            text,
            char_count,
            f"文字数 {char_count} 字が許容範囲 {minimum}〜{maximum} 字を外れています",
        )
    forbidden = next((phrase for phrase in FORBIDDEN_EXPRESSIONS if phrase in text), None)
    if forbidden is not None:
        return NarrationValidation(
            False,
            text,
            char_count,
            f"禁止表現「{forbidden}」が含まれています",
        )
    return NarrationValidation(True, text, char_count)


def strip_markdown_for_speech(value: str) -> str:
    """読み上げない Markdown 記号だけを除き、本文は保持する。"""

    text = _FENCED_CODE.sub(lambda match: match.group(1), value)
    text = _MARKDOWN_LINK.sub(lambda match: match.group(1), text)
    text = _HTML_TAG.sub("", text)
    lines = [_LINE_MARKER.sub("", line) for line in text.splitlines()]
    text = _INLINE_MARKER.sub("", " ".join(lines))
    return re.sub(r"\s+", " ", text).strip()


def select_rain_alternative(
    *,
    from_spot_id: str,
    pack_spots: Sequence[RainCandidate],
    travel_times: Sequence[NarrationTravelTime],
) -> RainAlternative | None:
    """同一パックの rain_ok 地点から車20分以内・距離最短を1件選ぶ。"""

    candidates = {
        spot.spot_id: spot
        for spot in pack_spots
        if spot.spot_id != from_spot_id and spot.weather_fit == "rain_ok"
    }
    eligible = [
        value
        for value in travel_times
        if value.from_spot_id == from_spot_id
        and value.to_spot_id in candidates
        and value.mode == "car"
        and 0 <= value.duration_sec <= RAIN_ALTERNATIVE_MAX_DURATION_SEC
    ]
    if not eligible:
        return None
    selected = min(
        eligible,
        key=lambda value: (value.distance_m, value.duration_sec, value.to_spot_id),
    )
    candidate = candidates[selected.to_spot_id]
    return RainAlternative(
        spot_id=candidate.spot_id,
        name_ja=candidate.name_ja,
        travel_min=(selected.duration_sec + 59) // 60,
        distance_m=selected.distance_m,
    )


def _position_text(value: ItineraryPosition | None) -> str:
    if value is None:
        return "位置情報なし"
    current = f"{value.day}日目の{value.position}番目"
    if value.next_spot_name:
        return f"{current}。次は{value.next_spot_name}へ向かいます"
    return f"{current}。この日の最後の案内地点です"


def _composition_instruction(variant: NarrationVariant) -> str:
    if variant in _OVERLAY_VARIANTS:
        return (
            "直前にbaseが再生済み。現在地点の設備・魅力や次の地点を本文へ書かず、"
            "状況注記だけにして目標文字数の上限を超えない。雨天代替候補の固有名は例外"
        )
    return "状況overlayと重複しない案内本体にして、目標文字数の範囲内に収める"


def _month_text(month: int | None, closed_months: Sequence[int]) -> str:
    if month is None:
        return "指定なし"
    if month in closed_months:
        return f"{month}月（季節閉鎖月に含まれる）"
    return f"{month}月（季節閉鎖月には含まれない）"


def _rain_alternative_text(
    variant: NarrationVariant,
    alternative: RainAlternative | None,
) -> str:
    if variant is not NarrationVariant.WEATHER_RAIN:
        return ""
    if alternative is None:
        return "- 雨天時の代替候補: 同じパック内の車20分以内には該当なし"
    return (
        "- 雨天時の代替候補: "
        f"{alternative.name_ja}（車で約{alternative.travel_min}分）。"
        "候補名と所要時間を本文に必ず含め、行程変更を断定せず選択肢として紹介する"
    )


def _serialize_fact(value: Any | None) -> str:
    if value is None:
        return "情報なし"
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _japanese_value(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    japanese = value.get("ja")
    return japanese if isinstance(japanese, str) else None
