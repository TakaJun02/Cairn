"""P7 で割り当てられた旅程 A/B/C の単発 guided 選択。"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any, Protocol

from app.core.llm import GenerationClient
from app.domains.itinerary.types import Itinerary

_CHOICES = ("A", "B", "C")
_SYSTEM_PROMPT = """あなたは旅程候補 A/B/C の選択器です。
ユーザーの追加希望と、コードが作った候補の要約だけを比較してください。
地点、時刻、数値を作らず、指定された JSON Schema の choice だけを返します。
追加希望に差が無ければ A を選びます。
"""


class SelectorGenerationPort(Protocol):
    async def generate(
        self,
        messages: Sequence[dict[str, str]],
        *,
        temperature: float = 0.2,
        max_tokens: int = 1024,
        extra_body: dict[str, Any] | None = None,
    ) -> str: ...


class LLMItinerarySelector:
    """候補外の値と生成障害を、必ず決定的な解 A へ縮退させる。"""

    def __init__(self, client: SelectorGenerationPort | None = None) -> None:
        self.client = client or GenerationClient()
        self.attempted = False
        self.degraded = False
        self.failure_reason: str | None = None

    async def __call__(
        self,
        solutions: Sequence[Itinerary],
        free_text: str,
    ) -> Itinerary:
        if not solutions:
            raise ValueError("選択できる旅程解がありません")
        labels = list(_CHOICES[: len(solutions)])
        self.attempted = True
        schema = {
            "type": "object",
            "properties": {
                "choice": {"type": "string", "enum": labels},
            },
            "required": ["choice"],
            "additionalProperties": False,
        }
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "request": free_text,
                        "solutions": [
                            _solution_summary(label, solution)
                            for label, solution in zip(labels, solutions, strict=True)
                        ],
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            },
        ]
        try:
            raw = await self.client.generate(
                messages,
                temperature=0.0,
                max_tokens=64,
                extra_body={
                    "response_format": {
                        "type": "json_schema",
                        "json_schema": {
                            "name": "itinerary_solution_choice",
                            "strict": True,
                            "schema": schema,
                        },
                    }
                },
            )
            parsed = json.loads(raw)
            if not isinstance(parsed, dict) or set(parsed) != {"choice"}:
                raise ValueError("choice 以外のフィールドが返りました")
            choice = parsed["choice"]
            if choice not in labels:
                raise ValueError(f"候補外の choice です: {choice}")
            return solutions[labels.index(choice)].model_copy(deep=True)
        except Exception as exc:  # noqa: BLE001 - 専門呼び出しは解 A へ縮退
            self.degraded = True
            self.failure_reason = type(exc).__name__
            return solutions[0].model_copy(deep=True)


def _solution_summary(label: str, itinerary: Itinerary) -> dict[str, Any]:
    return {
        "choice": label,
        "days": [
            {
                "date": day.date,
                "start_min": day.start_min,
                "end_min": day.end_min,
                "spot_ids": [item.spot_id for item in day.items],
                "visit_count": len(day.items),
                "total_stay_min": sum(item.stay_min for item in day.items),
            }
            for day in itinerary.days
        ],
        "concessions": [
            value.model_dump(mode="json") for value in itinerary.concessions
        ],
    }
