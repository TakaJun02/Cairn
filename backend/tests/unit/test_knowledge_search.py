"""知識検索の決定規則、観測、エージェント安全弁を検証する。"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import httpx

from app.core.config import Settings
from app.domains.narration.search.agent import (
    HARD_BUDGET_TOKENS,
    SOFT_BUDGET_TOKENS,
    KnowledgeSearchAgent,
    SearchToolName,
    _guided_schema,
)
from app.domains.narration.search.records import (
    KnowledgeDocumentRecord,
    KnowledgeHit,
)
from app.domains.narration.search.retrieval import (
    QUERY_INSTRUCT_PREFIX,
    KnowledgeRetrieval,
    centered_snippet,
    rank_lexical_chunks,
)
from app.domains.narration.search.types import SearchResult
from app.domains.narration.search.web import (
    CircuitBreakerState,
    TavilyWebSearchClient,
    WebSearchResponse,
)


def _hit(
    doc_id: str,
    *,
    title: str,
    heading: str = "概要",
    body: str,
    chunk_index: int = 0,
    chunk_count: int = 1,
    similarity: float | None = None,
) -> KnowledgeHit:
    return KnowledgeHit(
        doc_id=doc_id,
        title=title,
        spot_id=None,
        chunk_index=chunk_index,
        chunk_count=chunk_count,
        heading=heading,
        body=body,
        similarity=similarity,
    )


class MemoryRepository:
    def __init__(self, chunks: Sequence[KnowledgeHit]) -> None:
        self.chunks = list(chunks)
        self.lexical_calls = 0
        self.vector_calls = 0

    async def vector_search(
        self,
        vector: Sequence[float],
        *,
        limit: int,
        min_similarity: float,
    ) -> list[KnowledgeHit]:
        self.vector_calls += 1
        return [
            KnowledgeHit(
                **{
                    **hit.__dict__,
                    "similarity": hit.similarity if hit.similarity is not None else 0.8,
                }
            )
            for hit in self.chunks[:limit]
        ]

    async def lexical_chunks(self) -> list[KnowledgeHit]:
        self.lexical_calls += 1
        return list(self.chunks)

    async def get_documents(
        self, doc_ids: Sequence[str]
    ) -> list[KnowledgeDocumentRecord]:
        result: list[KnowledgeDocumentRecord] = []
        for doc_id in doc_ids:
            chunks = tuple(hit for hit in self.chunks if hit.doc_id == doc_id)
            if chunks:
                result.append(
                    KnowledgeDocumentRecord(
                        doc_id=doc_id,
                        title=chunks[0].title,
                        spot_id=chunks[0].spot_id,
                        chunks=chunks,
                    )
                )
        return result


class RecordingEmbedding:
    def __init__(self) -> None:
        self.texts: list[str] = []

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.texts.extend(texts)
        return [[0.0] * 4 for _ in texts]


class NoWebSearch:
    async def search(
        self,
        queries: Sequence[str],
        *,
        include_raw_content: bool,
    ) -> WebSearchResponse:
        return WebSearchResponse(available=False, message="Web 検索は利用不可です。")


class RecordingUnavailableWeb(NoWebSearch):
    def __init__(self) -> None:
        self.raw_content_flags: list[bool] = []

    async def search(
        self,
        queries: Sequence[str],
        *,
        include_raw_content: bool,
    ) -> WebSearchResponse:
        self.raw_content_flags.append(include_raw_content)
        return await super().search(
            queries,
            include_raw_content=include_raw_content,
        )


class ScriptedDecisionClient:
    def __init__(self, responses: Sequence[dict[str, Any]]) -> None:
        self.responses = list(responses)
        self.messages: list[list[dict[str, str]]] = []
        self.kwargs: list[dict[str, Any]] = []

    async def generate(
        self,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> str:
        self.messages.append(messages)
        self.kwargs.append(kwargs)
        return json.dumps(self.responses.pop(0), ensure_ascii=False)


def _decision(tool: str, args: dict[str, Any]) -> dict[str, Any]:
    return {"thought": "資料を確認しています", "tool": tool, "args": args}


def _contains_key(value: Any, key: str) -> bool:
    if isinstance(value, dict):
        return key in value or any(_contains_key(child, key) for child in value.values())
    if isinstance(value, list):
        return any(_contains_key(child, key) for child in value)
    return False


def test_lexical_score_orders_keyword_kinds_then_heading_then_total_hits() -> None:
    chunks = [
        _hit(doc_id="doc/c", title="別資料", body="熊 熊 熊 熊 熊"),
        _hit(doc_id="doc/b", title="熊の資料", body="熊 熊 熊"),
        _hit(doc_id="doc/a", title="自然情報", body="熊と温泉の情報"),
    ]

    result = rank_lexical_chunks(chunks, ["熊", "温泉"])

    assert [hit.doc_id for hit in result.hits] == ["doc/a", "doc/b", "doc/c"]


def test_variant_expansion_runs_only_after_zero_hits() -> None:
    chunks = [_hit(doc_id="doc/pond", title="鶴間池", body="ブナ林に囲まれた池です")]

    direct = rank_lexical_chunks(chunks, ["鶴間池"])
    expanded = rank_lexical_chunks(chunks, ["鶴間池周辺"])

    assert direct.expanded is False
    assert direct.used_terms == ("鶴間池",)
    assert expanded.expanded is True
    assert expanded.used_terms == ("鶴間池",)
    assert [hit.doc_id for hit in expanded.hits] == ["doc/pond"]


def test_snippet_is_centered_on_hit_instead_of_document_start() -> None:
    body = "前" * 600 + "象潟IC" + "後" * 600

    snippet, truncated = centered_snippet(body, ["象潟IC"])

    assert truncated is True
    assert "象潟IC" in snippet
    assert not snippet.startswith("前" * 400)
    assert len(snippet) <= 400


async def test_semantic_search_adds_qwen_query_instruct_prefix_only_to_query() -> None:
    repository = MemoryRepository(
        [_hit(doc_id="nature/animals", title="動物", body="ツキノワグマ")]
    )
    embedding = RecordingEmbedding()
    retrieval = KnowledgeRetrieval(repository, embedding)

    await retrieval.semantic_search(["クマは出ますか"])

    assert embedding.texts == [f"{QUERY_INSTRUCT_PREFIX}クマは出ますか"]
    assert repository.vector_calls == 1


async def test_same_action_is_not_executed_twice_and_observation_keeps_metadata() -> None:
    repository = MemoryRepository(
        [_hit(doc_id="faci/road", title="アクセス", body="象潟ICから約15分です")]
    )
    client = ScriptedDecisionClient(
        [
            _decision("lexical_search", {"keywords": ["象潟IC"]}),
            _decision("lexical_search", {"keywords": ["象潟IC"]}),
            _decision(
                "answer",
                {
                    "answer_ja": "象潟ICから約15分です。",
                    "sources": [{"kind": "knowledge", "doc_id": "faci/road"}],
                    "coverage": "full",
                },
            ),
        ]
    )
    agent = KnowledgeSearchAgent(
        KnowledgeRetrieval(repository, RecordingEmbedding()),
        NoWebSearch(),
        decision_client=client,
    )

    result = await agent.search("象潟ICから近いのは")

    assert isinstance(result, SearchResult)
    assert repository.lexical_calls == 1
    assert [step.tool for step in agent.last_trace.steps] == [
        "lexical_search",
        "lexical_search",
        "answer",
    ]
    assert agent.last_trace.steps[1].repeated is True
    second_prompt = client.messages[1][1]["content"]
    assert "doc_id: faci/road" in second_prompt
    assert "chunk: 1/1" in second_prompt
    assert "truncated:" in second_prompt


async def test_answer_before_any_tool_is_rejected() -> None:
    repository = MemoryRepository(
        [
            _hit(
                doc_id="nature/animals",
                title="動物",
                body="クマ（ツキノワグマ）が生息します",
            )
        ]
    )
    client = ScriptedDecisionClient(
        [
            _decision(
                "answer",
                {"answer_ja": "未調査です", "sources": [], "coverage": "none"},
            ),
            _decision("lexical_search", {"keywords": ["クマ"]}),
            _decision(
                "answer",
                {
                    "answer_ja": "ツキノワグマが生息します。",
                    "sources": [
                        {"kind": "knowledge", "doc_id": "nature/animals"}
                    ],
                    "coverage": "full",
                },
            ),
        ]
    )
    agent = KnowledgeSearchAgent(
        KnowledgeRetrieval(repository, RecordingEmbedding()),
        NoWebSearch(),
        decision_client=client,
    )

    result = await agent.search("クマは出ますか")

    assert isinstance(result, SearchResult)
    assert result.coverage == "full"
    assert agent.last_trace.steps[0].tool == "answer"
    assert agent.last_trace.tool_executions == 1
    assert "Tool 実行 0 回の answer は無効" in client.messages[1][1]["content"]


# ---------------------------------------------------------------------------
# ask_user(§6): ask コールバック(port)の注入
# ---------------------------------------------------------------------------


def _ask_decision(
    *, surface: str = "どちらの池", reason: str = "候補が複数あります"
) -> dict[str, Any]:
    return _decision(
        "ask_user",
        {
            "kind": "clarify",
            "surface": surface,
            "reason": reason,
            "options": [
                {"label": "鶴間池", "value": "鶴間池"},
                {"label": "元滝伏流水", "value": "元滝伏流水"},
            ],
        },
    )


def test_ask_user_tool_is_not_offered_without_a_callback() -> None:
    """port が None なら Tool 自体を出さない(§6)。"""

    agent = KnowledgeSearchAgent(
        KnowledgeRetrieval(MemoryRepository([]), RecordingEmbedding()),
        NoWebSearch(),
        decision_client=ScriptedDecisionClient([]),
    )

    available = agent._available_tools(answer_only=False, soft_mode=False)

    assert SearchToolName.ASK_USER not in available


def test_ask_user_tool_is_excluded_once_soft_budget_is_reached() -> None:
    """soft 予算(70%)以降は port があっても ask_user を選ばせない(narration_qa.md §7.1)。"""

    async def ask_callback(**kwargs: Any) -> str:  # pragma: no cover
        raise AssertionError("soft 予算以降は呼ばれないはず")

    agent = KnowledgeSearchAgent(
        KnowledgeRetrieval(MemoryRepository([]), RecordingEmbedding()),
        NoWebSearch(),
        decision_client=ScriptedDecisionClient([]),
        ask_callback=ask_callback,
    )

    assert SearchToolName.ASK_USER in agent._available_tools(
        answer_only=False, soft_mode=False
    )
    assert SearchToolName.ASK_USER not in agent._available_tools(
        answer_only=False, soft_mode=True
    )
    assert SearchToolName.ASK_USER not in agent._available_tools(
        answer_only=True, soft_mode=False
    )


async def test_ask_user_callback_is_invoked_and_answer_returns_as_observation() -> None:
    """回答は観測として同じ反復(decide ループ)に返る(§6)。"""

    calls: list[dict[str, Any]] = []

    async def ask_callback(
        *,
        kind: str,
        slot: str | None,
        surface: str | None,
        reason: str,
        options: list[dict[str, str]],
    ) -> str:
        calls.append(
            {"kind": kind, "slot": slot, "surface": surface, "reason": reason, "options": options}
        )
        return "質問「候補が複数あります」への回答: 鶴間池(回答方法: chip)"

    repository = MemoryRepository(
        [_hit(doc_id="faci_spot/spot_001", title="鶴間池", body="静かな池です")]
    )
    client = ScriptedDecisionClient(
        [
            _ask_decision(),
            _decision("lexical_search", {"keywords": ["鶴間池"]}),
            _decision(
                "answer",
                {
                    "answer_ja": "鶴間池は静かな池です。",
                    "sources": [{"kind": "knowledge", "doc_id": "faci_spot/spot_001"}],
                    "coverage": "full",
                },
            ),
        ]
    )
    agent = KnowledgeSearchAgent(
        KnowledgeRetrieval(repository, RecordingEmbedding()),
        NoWebSearch(),
        decision_client=client,
        ask_callback=ask_callback,
    )

    result = await agent.search("池について教えて")

    assert isinstance(result, SearchResult)
    assert calls == [
        {
            "kind": "clarify",
            "slot": None,
            "surface": "どちらの池",
            "reason": "候補が複数あります",
            "options": [
                {"label": "鶴間池", "value": "鶴間池"},
                {"label": "元滝伏流水", "value": "元滝伏流水"},
            ],
        }
    ]
    assert [step.tool for step in agent.last_trace.steps] == [
        "ask_user",
        "lexical_search",
        "answer",
    ]
    # 回答が観測として次の decide 周のプロンプトに含まれる(action_log 経由)。
    second_prompt = client.messages[1][1]["content"]
    assert "鶴間池(回答方法: chip)" in second_prompt


def test_guided_schema_includes_ask_user_fields_when_offered() -> None:
    schema = _guided_schema(available_tools=[tool.value for tool in SearchToolName])

    assert "ask_user" in schema["properties"]["tool"]["enum"]
    args_properties = schema["properties"]["args"]["properties"]
    assert "kind" in args_properties
    assert args_properties["kind"]["enum"] == ["preference", "clarify"]
    assert "slot" in args_properties
    assert "surface" in args_properties
    assert "options" in args_properties


async def test_spot_document_id_is_prefixed_before_request() -> None:
    repository = MemoryRepository(
        [_hit(doc_id="faci_spot/spot_012", title="鶴間池", body="小さな池です")]
    )
    client = ScriptedDecisionClient(
        [
            _decision("get_document", {"doc_ids": ["faci_spot/spot_012"]}),
            _decision(
                "answer",
                {
                    "answer_ja": "鶴間池は小さな池です。",
                    "sources": [
                        {"kind": "knowledge", "doc_id": "faci_spot/spot_012"}
                    ],
                    "coverage": "full",
                },
            ),
        ]
    )
    agent = KnowledgeSearchAgent(
        KnowledgeRetrieval(repository, RecordingEmbedding()),
        NoWebSearch(),
        decision_client=client,
    )

    await agent.search("鶴間池ってどんな所?", spot_id="spot_012")

    prompt = client.messages[0][1]["content"]
    assert prompt.startswith("対象スポットの文書 ID: faci_spot/spot_012\n")


def test_decide_schema_avoids_unique_items_and_budget_uses_70_85_percent() -> None:
    schema = _guided_schema()

    assert not _contains_key(schema, "uniqueItems")
    assert schema["properties"]["args"]["properties"]["queries"]["maxItems"] == 3
    assert SOFT_BUDGET_TOKENS == 10_752
    assert HARD_BUDGET_TOKENS == 13_056


async def test_soft_budget_suppresses_web_raw_content() -> None:
    repository = MemoryRepository([])
    web = RecordingUnavailableWeb()
    client = ScriptedDecisionClient(
        [
            _decision("web_search", {"queries": ["今日の営業"]}),
            _decision(
                "answer",
                {
                    "answer_ja": "対象施設を特定できず、営業状況は確認できませんでした。",
                    "sources": [],
                    "coverage": "none",
                },
            ),
        ]
    )
    agent = KnowledgeSearchAgent(
        KnowledgeRetrieval(repository, RecordingEmbedding()),
        web,
        decision_client=client,
        context_window_tokens=1_882,
    )

    result = await agent.search("今日やってますか")

    assert isinstance(result, SearchResult)
    assert web.raw_content_flags == [False]
    assert agent.last_trace.soft_budget_reached is True


async def test_hard_budget_reduces_menu_to_answer_after_emergency_tool() -> None:
    repository = MemoryRepository(
        [_hit(doc_id="known/doc", title="既知資料", body="既知情報です")]
    )
    client = ScriptedDecisionClient(
        [
            _decision(
                "answer",
                {
                    "answer_ja": "既知情報です。",
                    "sources": [{"kind": "knowledge", "doc_id": "known/doc"}],
                    "coverage": "full",
                },
            )
        ]
    )
    agent = KnowledgeSearchAgent(
        KnowledgeRetrieval(repository, RecordingEmbedding()),
        NoWebSearch(),
        decision_client=client,
        context_window_tokens=1_700,
    )

    result = await agent.search("既知情報")

    schema = client.kwargs[0]["extra_body"]["response_format"]["json_schema"]["schema"]
    assert isinstance(result, SearchResult)
    assert schema["properties"]["tool"]["enum"] == ["answer"]
    assert agent.last_trace.hard_budget_reached is True
    assert agent.last_trace.tool_executions == 1
    assert [step.tool for step in agent.last_trace.steps] == ["lexical_search", "answer"]


class FailingHttpClient:
    def __init__(self) -> None:
        self.calls = 0

    async def post(self, url: str, *, json: dict[str, Any]) -> httpx.Response:
        self.calls += 1
        raise httpx.ConnectError("offline", request=httpx.Request("POST", url))


async def test_tavily_missing_key_and_circuit_breaker_return_unavailable() -> None:
    missing = TavilyWebSearchClient(Settings(tavily_APIkey=""))
    missing_result = await missing.search(["今日の営業"], include_raw_content=True)

    clock = [0.0]
    http_client = FailingHttpClient()
    client = TavilyWebSearchClient(
        Settings(tavily_APIkey="test-key"),
        http_client=http_client,  # type: ignore[arg-type]
        failure_threshold=3,
        recovery_seconds=60,
        clock=lambda: clock[0],
        circuit_state=CircuitBreakerState(),
    )
    first_three = [
        await client.search(["今日の営業"], include_raw_content=False)
        for _ in range(3)
    ]
    opened = await client.search(["今日の営業"], include_raw_content=False)
    clock[0] = 61.0
    retried = await client.search(["今日の営業"], include_raw_content=False)

    assert missing_result.available is False
    assert all(result.available is False for result in first_three)
    assert "サーキットブレーカー" in opened.message
    assert http_client.calls == 4
    assert retried.available is False
