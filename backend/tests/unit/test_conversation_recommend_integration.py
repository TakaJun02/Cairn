"""段3の受け入れ条件(仕様の実害シナリオ)を実際の推薦処理で確認する。

`Docs/30_design/agent_react_architecture.md` §4 の指示書が求める受け入れ条件:
「車で回ります。滝が好き」相当の指示から SA が `{"filter": {"tags": ["滝"],
"mobility": null}}` を返す想定で、推薦が 0 件にならず k=3 で返ること
(2026-08-04、dialogue_style.md §4 決定: 推薦は 3 件に固定。旧 k=5 から
変更)。さらにモックが不正値(`tags: ["滝","山"]`)を返しても「山」だけ落ちて
実行されること(23_ux_issues.md §0.3 の実害の再発防止)。

`recommendation` ドメインの内部(`RecommendationService`/`scoring.py`)は一切
変更していない。ここでは実装をそのまま、DB を使わないインメモリ repository
で駆動し、レコメンド SA(`recommend_agent`)からの `filter` がエンドツーエンド
で 0 件応答にならないことを検査する。実 LLM(127.0.0.1:8000)は叩かない。
"""

from __future__ import annotations

import json
from typing import Any

from app.core.config import Settings
from app.domains.conversation.main_agent import run_main_agent
from app.domains.conversation.state import ProfileState, SpotFact, TurnState
from app.domains.conversation.types import ToolError, ToolErrorCode, ToolName, ToolResult
from app.domains.recommendation.repo_types import RecommendationData, RecommendationSpot
from app.domains.recommendation.service import RecommendationService

# リランクを明示的に無効化した設定。既定値(recommendation_rerank_enabled=True)
# のまま `RecommendationService` を作ると、実 LLM(127.0.0.1:8000)へ
# リランクの guided JSON を投げてしまう(この統合テストの関心はハードフィルタ
# →スコアリングの決定的経路であり、LLM リランクの検証は test_recommendation.py
# の担当)。
_NO_RERANK_SETTINGS = Settings(recommendation_rerank_enabled=False)

_TAG_VOCABULARY = ["滝", "湧水", "登山", "温泉"]


def _turn_json(tool: str, args: dict[str, Any], *, thought: str = "考える") -> str:
    return json.dumps(
        {"thought": thought, "action": {"tool": tool, "args": args}}, ensure_ascii=False
    )


def _recommend_act_json(
    *, filter: dict[str, Any], assumptions: list[str] | None = None
) -> str:
    return json.dumps(
        {
            "thought": "指示を条件に翻訳する",
            "action": {
                "tool": "done",
                "args": {"filter": filter, "assumptions": assumptions or []},
            },
        },
        ensure_ascii=False,
    )


class ScriptedMainAgentClient:
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)

    async def generate(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        return self.responses.pop(0)


class MemoryRecommendationRepository:
    """`RecommendationRepositoryPort` を満たすインメモリ実装(DB を使わない)。"""

    def __init__(self, data: RecommendationData) -> None:
        self.data = data

    async def load_data(self, origin_spot_id: str | None = None) -> RecommendationData:
        del origin_spot_id
        return self.data

    async def existing_spot_ids(self, spot_ids: list[str]) -> set[str]:
        return set(spot_ids) & self.data.spot_ids


class RealRecommendationTools:
    """段3の接続点だけを検査する最小 Tool アダプタ。

    `RecommendationService`(既存の推薦処理。ハードフィルタ→スコアリング)は
    一切変更せずそのまま使う。リランクは無効化し、決定的スコア順で確認する
    (このテストの関心は「SA の filter が 0 件応答を起こさないか」であり、
    LLM リランクの検証は `test_recommendation.py` の担当)。
    """

    def __init__(self, repository: MemoryRecommendationRepository) -> None:
        self.repository = repository

    async def recommend(
        self, *, step_id: int, args: Any, context: Any, use_specialist: bool
    ) -> ToolResult | ToolError:
        del use_specialist
        service = RecommendationService(self.repository, settings=_NO_RERANK_SETTINGS)
        result = await service.recommend(
            {"filter": args.filter, "k": args.k, "exclude": args.exclude}, context=context
        )
        if not result.spot_ids:
            return ToolError(
                code=ToolErrorCode.EMPTY_RESULT,
                message_ja="候補が見つかりませんでした。",
                recoverable=True,
            )
        return ToolResult(
            step_id=step_id, tool=ToolName.RECOMMEND, data=result.model_dump(mode="json")
        )

    async def plan_itinerary(self, **kwargs: Any) -> Any:  # pragma: no cover
        raise AssertionError("このテストでは呼ばれません")

    async def edit_itinerary(self, **kwargs: Any) -> Any:  # pragma: no cover
        raise AssertionError("このテストでは呼ばれません")

    async def search_knowledge(self, **kwargs: Any) -> Any:  # pragma: no cover
        raise AssertionError("このテストでは呼ばれません")

    async def ask_user(self, **kwargs: Any) -> Any:  # pragma: no cover
        raise AssertionError("このテストでは呼ばれません")


def _spot(number: int, tags: tuple[str, ...]) -> RecommendationSpot:
    return RecommendationSpot(
        spot_id=f"spot_{number:03d}",
        name_ja=f"地点{number}",
        description_ja=None,
        address_ja="秋田県にかほ市",
        tags_ja=tags,
        stay_min=30,
        weather_fit="rain_ok",
        visit_difficulty="no_walk",
    )


def _data() -> RecommendationData:
    # 「滝」タグの地点を 6 件用意し、k=3 に対して常に十分な母数を確保する。
    spots = tuple(_spot(number, ("滝", "自然")) for number in range(1, 7))
    return RecommendationData(
        spots=spots,
        tag_to_preference={"滝": None, "自然": None},
        realtime={},
        travel_minutes={},
    )


def _state() -> TurnState:
    spots = {
        f"spot_{number:03d}": SpotFact(
            spot_id=f"spot_{number:03d}", name_ja=f"地点{number}", kind="poi", tags_ja=["滝"]
        )
        for number in range(1, 7)
    }
    return TurnState(
        turn_id="turn",
        thread_id=1,
        user_id=1,
        utterance="滝や湧水が好きです。車で回ります",
        profile=ProfileState(),
        spot_id_vocab=list(spots),
        spot_names={key: value.name_ja for key, value in spots.items()},
        spot_catalog=spots,
        tag_vocabulary=_TAG_VOCABULARY,
    )


async def test_car_mobility_instruction_no_longer_zeroes_out_recommendations() -> None:
    """受け入れ条件1: 「車で回ります。滝が好き」相当が 0 件にならず k=3 で返る。"""

    state = _state()
    tools = RealRecommendationTools(MemoryRecommendationRepository(_data()))
    client = ScriptedMainAgentClient(
        [
            _turn_json(
                "recommend", {"instruction": "滝や湧水が好きです。車で回ります"}
            ),
            _recommend_act_json(filter={"tags": ["滝"], "mobility": None}),
            _turn_json("done", {}),
        ]
    )

    await run_main_agent(state, tools=tools, client=client)

    assert state.main_agent_failed is False
    result = state.step_results[1]
    assert result.tool is ToolName.RECOMMEND
    assert len(result.data["spot_ids"]) == 3
    assert state.trajectory[0].error is None


async def test_partially_invalid_filter_drops_only_the_bad_tag_and_still_returns_three() -> None:
    """受け入れ条件2: 不正値(tags: ["滝","山"])でも「山」だけ落ちて実行される。"""

    state = _state()
    tools = RealRecommendationTools(MemoryRecommendationRepository(_data()))
    client = ScriptedMainAgentClient(
        [
            _turn_json(
                "recommend", {"instruction": "滝や山が好きです。車で回ります"}
            ),
            _recommend_act_json(filter={"tags": ["滝", "山"], "mobility": "car"}),
            _turn_json("done", {}),
        ]
    )

    await run_main_agent(state, tools=tools, client=client)

    result = state.step_results[1]
    assert len(result.data["spot_ids"]) == 3
    observation = state.trajectory[0].observation
    assert "除外した条件" in observation
    assert "山" in observation
