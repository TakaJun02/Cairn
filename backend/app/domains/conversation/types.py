"""対話パイプライン内部の閉じた契約。

API の入出力ではなく、`Docs/30_design/agent_react_architecture.md` をノード間で
共有する型である。Tool 固有ドメインにはこのモジュールを import させない。

段2(ReAct メインループ)により、以下の区分になっている:

- Tool アダプタ(`tool_adapters.py`)向けの内部契約(`spot_id` ベース。旧設計から
  ほぼ変更なし): `RecommendArgs` / `PlanItineraryArgs` / `EditItineraryArgs` /
  `SearchKnowledgeArgs`
- メインエージェント(`main_agent.py`)が guided JSON として書く契約
  (スポット名ベース。§3.3 の最終契約): `MainAgentTurn` / `MainRecommendArgs` /
  `MainPlanItineraryArgs` / `MainEditItineraryArgs` / `MainSearchKnowledgeArgs`
- `ask_user`(`AskUserArgs` 等)は段5で使うため型だけ残す。段2のメインループの
  Tool enum には含めない
"""

from __future__ import annotations

import math
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domains.itinerary.types import PredEnum
from app.domains.recommendation.types import Mobility, Pace, Party, PreferenceKey


class ConversationModel(BaseModel):
    """未知フィールドを対話状態へ紛れ込ませない。"""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ToolName(StrEnum):
    """Tool アダプタ(`tool_ports.ConversationToolPort`)の実行結果に載る Tool 名。"""

    RECOMMEND = "recommend"
    PLAN_ITINERARY = "plan_itinerary"
    EDIT_ITINERARY = "edit_itinerary"
    SEARCH_KNOWLEDGE = "search_knowledge"
    ASK_USER = "ask_user"


class MainToolName(StrEnum):
    """メインエージェントの `action.tool` enum(§3.3)。"""

    RECOMMEND = "recommend"
    PLAN_ITINERARY = "plan_itinerary"
    EDIT_ITINERARY = "edit_itinerary"
    SEARCH_KNOWLEDGE = "search_knowledge"
    ASK_USER = "ask_user"
    DONE = "done"


class ResponseMode(StrEnum):
    """`respond` の 1 テンプレート内の分岐。"""

    EXPLANATION = "explanation"
    FAILURE = "failure"


class Slot(StrEnum):
    """`ask_user`(kind=preference)が使うスロット語彙(§3.3・§7)。"""

    ONBOARDING = "onboarding"
    PARTY = "party"
    MOBILITY = "mobility"
    PACE = "pace"
    INTERESTS = "interests"
    DATES = "dates"
    ORIGIN = "origin"


class ToolErrorCode(StrEnum):
    PRECONDITION_UNMET = "precondition_unmet"
    REFERENCE_UNRESOLVED = "reference_unresolved"
    EMPTY_RESULT = "empty_result"
    UPSTREAM_TIMEOUT = "upstream_timeout"
    INTERNAL = "internal"


class ProfileDelta(ConversationModel):
    interests: dict[PreferenceKey, float] = Field(default_factory=dict)
    party: Party | None = None
    mobility: Mobility | None = None
    pace: Pace | None = None
    avoid: list[str] = Field(default_factory=list)
    notes: str | None = None

    @field_validator("interests")
    @classmethod
    def validate_interests(
        cls, values: dict[PreferenceKey, float]
    ) -> dict[PreferenceKey, float]:
        # 2026-08-04 実機調査(不具合1): guided JSON の minimum/maximum は
        # vLLM/xgrammar 側で確実に強制されるとは限らない(update_profile.py
        # の調査コメント参照)。範囲外は棄てず -1.0〜1.0 にクランプする
        # (NFR-5: 抽出ステップの部分的な逸脱でターン全体を落とさない)。
        # 非有限値(NaN/Infinity)だけは異常値として引き続き拒否する。
        result: dict[PreferenceKey, float] = {}
        for key, value in values.items():
            if not math.isfinite(value):
                raise ValueError(f"interests.{key.value} は有限の値にしてください")
            result[key] = max(-1.0, min(1.0, value))
        return result

    @field_validator("avoid")
    @classmethod
    def normalize_avoid(cls, values: list[str]) -> list[str]:
        return _deduplicate_nonempty(values)

    @field_validator("notes")
    @classmethod
    def normalize_notes(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class ConstraintDraft(ConversationModel):
    """メインエージェント(または旧 understand)由来の制約。

    `pred` は Pydantic enum にせず文字列で受ける。guided decoding 後にも
    未知値をコードで `unmodeled` へ移す不変条件をテスト可能にするためである。
    """

    id: str | None = None
    pred: str
    args: dict[str, Any] = Field(default_factory=dict)
    weight: float = 1.0
    source_text: str = ""
    source_message_id: int | None = None
    created_at_version: int | None = None
    handling: Literal["dsl"] = "dsl"

    @field_validator("weight")
    @classmethod
    def validate_weight(cls, value: float) -> float:
        if not math.isfinite(value) or value < 0:
            raise ValueError("constraint.weight は有限の 0 以上にしてください")
        return value


class UnmodeledItem(ConversationModel):
    """分類・検証できなかった要素の報告(C4: 部分不正は要素単位で落とす)。"""

    text: str = Field(min_length=1)
    reason: str | None = None


class ScoreAdjustment(ConversationModel):
    spot_id: str = Field(min_length=1)
    delta: float
    why: str = ""
    handling: Literal["weight"] = "weight"

    @field_validator("delta")
    @classmethod
    def validate_delta(cls, value: float) -> float:
        # 2026-08-04 実機調査(不具合1): ProfileDelta.validate_interests と同じ
        # 理由でクランプにする(guided decoding が壊れて外した再試行では
        # minimum/maximum の強制自体が無い)。非有限値だけ拒否する。
        if not math.isfinite(value):
            raise ValueError("score_adjustments.delta は有限の値にしてください")
        return max(-0.5, min(0.5, value))


class UpdateProfileOutput(ConversationModel):
    """N1.5 `update_profile` の guided JSON(段1から変更なし)。"""

    profile_delta: ProfileDelta | None = None
    score_adjustments: list[ScoreAdjustment] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Tool アダプタ向け内部契約(spot_id ベース。既存 Tool 実装が期待する形のまま)
# ---------------------------------------------------------------------------


class RecommendArgs(ConversationModel):
    filter: dict[str, Any] = Field(default_factory=dict)
    k: int = Field(default=5, ge=1, le=8)
    exclude: list[str] = Field(default_factory=list)


class PlanItineraryArgs(ConversationModel):
    days: list[dict[str, Any]] = Field(default_factory=list)
    must_visit: list[str] = Field(default_factory=list)
    # 挿入してもしなくてもよい候補(spot_id。既定空。2026-08-04 夜追加、
    # ADR-0022)。`must_visit` と異なり require 制約は作らない —
    # `ItineraryService.plan_itinerary` が `insertion_pool` に
    # `must_visit ∪ candidate_spots` として渡す。
    candidate_spots: list[str] = Field(default_factory=list)
    # 未確認の前提(日付・起点等)。レコメンド SA の assumptions と同型
    # (2026-08-04 追加。クローズドワールド原則の明示的な例外 —
    # Docs/30_design/agent_react_architecture.md §5・§14、
    # Docs/40_api/chat_sse.md §1.2)。
    assumptions: list[str] = Field(default_factory=list)


class EditItineraryArgs(ConversationModel):
    ops: list[dict[str, Any]] = Field(default_factory=list)
    # 既定 false。true のときだけ従来どおりフル ILS + A/B/C + LLM 選択を行う
    # (ADR-0021)。
    allow_refill: bool = False
    # None = 基の版からそのままコピー。リストを与えたら置換する
    # (Docs/30_design/agent_react_architecture.md §5・§14)。
    assumptions: list[str] | None = None


class SearchKnowledgeArgs(ConversationModel):
    request: str = Field(min_length=1)
    spot_id: str | None = None


class AskUserOption(ConversationModel):
    label: str = Field(min_length=1)
    value: str = Field(min_length=1)


class AskUserArgs(ConversationModel):
    """`ask_user` Tool の引数(§3.3・§7)。`kind` が `state:ask_user`/`clarify` を分ける。"""

    kind: Literal["preference", "clarify"]
    slot: Slot | None = None
    surface: str | None = None
    reason: str = Field(min_length=1)
    options: list[AskUserOption] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_kind_shape(self) -> AskUserArgs:
        if self.kind == "preference":
            if self.slot is None:
                raise ValueError("kind=preference のとき slot が必要です")
            if self.surface is not None:
                raise ValueError("kind=preference のとき surface は指定できません")
        else:
            if self.surface is None or not self.surface.strip():
                raise ValueError("kind=clarify のとき surface が必要です")
            if self.slot is not None:
                raise ValueError("kind=clarify のとき slot は指定できません")
        return self


class AskUserResult(ConversationModel):
    answer: str = Field(min_length=1)
    answered_by: Literal["chip", "free_text", "timeout"]
    slot: Slot | None = None
    surface: str | None = None


class ToolError(ConversationModel):
    code: ToolErrorCode
    message_ja: str
    recoverable: bool
    details: dict[str, Any] = Field(default_factory=dict)


class ToolResult(ConversationModel):
    step_id: int
    tool: ToolName
    data: dict[str, Any] = Field(default_factory=dict)
    degraded: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# メインエージェントの guided JSON(§3.2〜§3.3 の最終契約。スポット名ベース)
# ---------------------------------------------------------------------------


class MainRecommendArgs(ConversationModel):
    instruction: str = Field(min_length=1)


class MainConstraintAdd(ConversationModel):
    """メインエージェントが直接書く制約(述語 DSL 17 種)。"""

    pred: str
    args: dict[str, Any] = Field(default_factory=dict)
    weight: float = 1.0
    source_text: str = ""

    @field_validator("weight")
    @classmethod
    def validate_weight(cls, value: float) -> float:
        if not math.isfinite(value) or value < 0:
            raise ValueError("constraint.weight は有限の 0 以上にしてください")
        return value


class MainConstraintOps(ConversationModel):
    add: list[MainConstraintAdd] = Field(default_factory=list)
    remove: list[str] = Field(default_factory=list)


class MainPlanDay(ConversationModel):
    date: str = Field(min_length=1)
    start: str = Field(min_length=1)
    end: str = Field(min_length=1)
    origin_name: str | None = None
    destination_name: str | None = None


class MainPlanItineraryArgs(ConversationModel):
    days: list[MainPlanDay] = Field(default_factory=list)
    must_visit: list[str] = Field(default_factory=list)
    # 挿入してもしなくてもよい候補のスポット名(既定空。2026-08-04 夜追加、
    # ADR-0022)。ソルバーの挿入プールは既定で must_visit ∪ candidate_spots
    # の解決済み spot_id に限定される(カタログ全件を暗黙に使わない)。
    # ユーザーが具体的なスポット名を挙げておらず候補が必要な場合は、先に
    # recommend を呼んで候補を得てから、その候補名をここに渡すこと。
    candidate_spots: list[str] = Field(default_factory=list)
    constraints: MainConstraintOps | None = None
    notes: str | None = None
    # 未確認の前提(日付・起点等)を日本語短文で列挙する(2026-08-04 追加。
    # Docs/30_design/agent_react_architecture.md §5)。
    assumptions: list[str] = Field(default_factory=list)


class MainEditItineraryArgs(ConversationModel):
    """`ops` は既存の add/remove/move/replace/lock/set_stay/set_time/revert の

    形のまま、`targets`/`target`/`with` だけスポット名にした dict。
    実際の検証(discriminated union)は名前解決後、`EditItineraryArgs.ops` を
    通じて既存の `parse_ops` が行う。
    """

    ops: list[dict[str, Any]] = Field(default_factory=list)
    constraints: MainConstraintOps | None = None
    notes: str | None = None
    # 既定 false。ユーザーが「代わりにどこか入れて」「空いた時間に何か
    # 足して」のように補充・入れ替えを明示的に求めたときだけ true にする
    # (ADR-0021)。false のときソルバーは訪問集合を変えず並び・時刻の
    # 再調整のみ行う。
    allow_refill: bool = False
    # None = 基の版からそのままコピー。前提が解消されたときだけメインが
    # リストを与えて置換する(Docs/30_design/agent_react_architecture.md §5)。
    assumptions: list[str] | None = None


class MainSearchKnowledgeArgs(ConversationModel):
    request: str = Field(min_length=1)
    spot_name: str | None = None


class MainAgentAction(ConversationModel):
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)


class MainAgentTurn(ConversationModel):
    """メインループ 1 周の guided JSON(§3.2)。"""

    thought: str
    action: MainAgentAction


class TrajectoryStep(ConversationModel):
    """このターンの軌跡(§3.1 ⑤)。実行した手と結果を1件ずつ積む。

    `observation` は名前空間ダイジェスト(spot_id を含まない自然文)。
    """

    tool: str
    thought: str
    args: dict[str, Any] = Field(default_factory=dict)
    observation: str
    error: dict[str, Any] | None = None


def constraint_to_mapping(value: ConstraintDraft) -> dict[str, Any]:
    """ItineraryService へ渡せる JSON 互換 dict にする。"""

    return value.model_dump(mode="json", exclude_none=True)


def pred_values() -> list[str]:
    return [value.value for value in PredEnum]


def preference_values() -> list[str]:
    return [value.value for value in PreferenceKey]


def slot_values() -> list[str]:
    return [value.value for value in Slot]


def _deduplicate_nonempty(values: list[str]) -> list[str]:
    result: list[str] = []
    for raw in values:
        value = raw.strip()
        if value and value not in result:
            result.append(value)
    return result
