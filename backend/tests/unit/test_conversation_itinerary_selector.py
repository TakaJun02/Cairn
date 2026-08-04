"""P7 の単発 A/B/C 選択と決定的縮退を検証する。"""

from typing import Any

from app.domains.conversation.itinerary_selector import LLMItinerarySelector
from app.domains.itinerary.types import Itinerary


class FakeGenerationClient:
    def __init__(self, response: str | Exception) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    async def generate(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.2,
        max_tokens: int = 1024,
        extra_body: dict[str, Any] | None = None,
    ) -> str:
        self.calls.append(
            {
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "extra_body": extra_body,
            }
        )
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


async def test_guided_selector_chooses_only_from_supplied_solutions() -> None:
    solutions = [Itinerary(days=[], version=value) for value in (1, 2, 3)]
    client = FakeGenerationClient('{"choice":"B"}')
    selector = LLMItinerarySelector(client)

    selected = await selector(solutions, "温泉を優先")

    assert selected == solutions[1]
    assert selected is not solutions[1]
    assert selector.attempted is True
    assert selector.degraded is False
    call = client.calls[0]
    assert call["temperature"] == 0.0
    assert call["max_tokens"] == 64
    schema = call["extra_body"]["response_format"]["json_schema"]["schema"]
    assert schema["properties"]["choice"]["enum"] == ["A", "B", "C"]
    assert "uniqueItems" not in str(schema)


async def test_invalid_choice_degrades_to_a() -> None:
    solutions = [Itinerary(days=[], version=value) for value in (1, 2, 3)]
    selector = LLMItinerarySelector(FakeGenerationClient('{"choice":"D"}'))

    selected = await selector(solutions, "人混みを避けたい")

    assert selected == solutions[0]
    assert selector.degraded is True
    assert selector.failure_reason == "ValueError"


async def test_generation_failure_degrades_to_a() -> None:
    solutions = [Itinerary(days=[], version=value) for value in (1, 2)]
    selector = LLMItinerarySelector(FakeGenerationClient(TimeoutError()))

    selected = await selector(solutions, "のんびり")

    assert selected == solutions[0]
    assert selector.degraded is True
    assert selector.failure_reason == "TimeoutError"
