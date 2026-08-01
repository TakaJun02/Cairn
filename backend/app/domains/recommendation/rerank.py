"""候補集合内だけを扱う短い guided JSON リランク。"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Protocol

from app.core.llm import GenerationClient
from app.domains.recommendation.scoring import ScoredSpot
from app.domains.recommendation.types import (
    RecommendRequest,
    RecommendationContext,
)


class RerankError(RuntimeError):
    """生成自体は成功したが、リランク契約を満たさなかった。"""


class CandidateReranker(Protocol):
    async def rerank(
        self,
        candidates: Sequence[ScoredSpot],
        *,
        request: RecommendRequest,
        context: RecommendationContext,
        k: int,
    ) -> Sequence[str]: ...


class LLMRecommendationReranker:
    def __init__(self, client: GenerationClient) -> None:
        self.client = client

    async def rerank(
        self,
        candidates: Sequence[ScoredSpot],
        *,
        request: RecommendRequest,
        context: RecommendationContext,
        k: int,
    ) -> Sequence[str]:
        candidate_ids = [value.spot.spot_id for value in candidates]
        schema = _guided_schema(candidate_ids, k)
        payload = {
            "profile": context.profile.model_dump(mode="json"),
            "request_filter": request.filter.model_dump(mode="json"),
            # 位置バイアス対策として、呼び出し側のスコア降順を
            # 一切並べ替えない。
            "candidates_score_order": [
                {
                    "spot_id": value.spot.spot_id,
                    "name_ja_db": value.spot.name_ja,
                    "description_ja_db": (value.spot.description_ja or "")[:360],
                    "tags_ja_db": list(value.spot.tags_ja),
                    "reason_materials": value.reason_materials.model_dump(mode="json"),
                }
                for value in candidates
            ],
        }
        text = await self.client.generate(
            [
                {
                    "role": "system",
                    "content": (
                        "あなたは鳥海山観光の候補リランカーです。"
                        "候補集合の spot_id だけを使い、プロファイルと"
                        "提示済みの事実に照らして上位を選んでください。"
                        "地理・天気・混雑の数値を推測せず、候補カードの"
                        "事実だけを使います。出力は指定 JSON のみです。"
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                },
            ],
            temperature=0.0,
            max_tokens=192,
            extra_body={
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "recommendation_rerank",
                        "strict": True,
                        "schema": schema,
                    },
                }
            },
        )
        try:
            value = json.loads(text)
            spot_ids = value["spot_ids"]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise RerankError(
                "リランク出力が guided JSON 契約を満たしません"
            ) from exc
        if not isinstance(spot_ids, list) or not all(isinstance(item, str) for item in spot_ids):
            raise RerankError("spot_ids は文字列の配列である必要があります")
        return list(dict.fromkeys(spot_ids))


def _guided_schema(candidate_ids: list[str], k: int) -> dict[str, object]:
    return {
        "type": "object",
        "properties": {
            "spot_ids": {
                "type": "array",
                "items": {"type": "string", "enum": candidate_ids},
                "minItems": k,
                "maxItems": k,
                "uniqueItems": True,
            }
        },
        "required": ["spot_ids"],
        "additionalProperties": False,
    }
