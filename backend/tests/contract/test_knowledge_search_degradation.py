"""埋め込み障害時も字句検索へ縮退して回答する契約を検証する。"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import httpx
import respx

from app.core.config import Settings
from app.domains.knowledge.embedding import OpenAIEmbeddingClient
from app.domains.narration.search.agent import KnowledgeSearchAgent
from app.domains.narration.search.records import KnowledgeDocumentRecord, KnowledgeHit
from app.domains.narration.search.retrieval import KnowledgeRetrieval
from app.domains.narration.search.types import SearchResult
from app.domains.narration.search.web import WebSearchResponse


class ContractRepository:
    def __init__(self) -> None:
        self.hit = KnowledgeHit(
            doc_id="nature/animals",
            title="鳥海山の動物",
            spot_id=None,
            chunk_index=0,
            chunk_count=1,
            heading="クマへの注意",
            body="鳥海山周辺にはクマ（ツキノワグマ）が生息しています。",
        )
        self.lexical_calls = 0

    async def vector_search(
        self,
        vector: Sequence[float],
        *,
        limit: int,
        min_similarity: float,
    ) -> list[KnowledgeHit]:
        raise AssertionError("埋め込み失敗後に vector_search を呼んではいけません")

    async def lexical_chunks(self) -> list[KnowledgeHit]:
        self.lexical_calls += 1
        return [self.hit]

    async def get_documents(
        self, doc_ids: Sequence[str]
    ) -> list[KnowledgeDocumentRecord]:
        return []


class NoWebSearch:
    async def search(
        self,
        queries: Sequence[str],
        *,
        include_raw_content: bool,
    ) -> WebSearchResponse:
        return WebSearchResponse(available=False, message="Web 検索は利用不可です。")


class ScriptedDecisions:
    def __init__(self) -> None:
        self.responses = [
            {
                "thought": "関連する安全情報を意味検索します",
                "tool": "semantic_search",
                "args": {"queries": ["クマは出ますか"]},
            },
            {
                "thought": "埋め込みが使えないため字句検索します",
                "tool": "lexical_search",
                "args": {"keywords": ["クマ"]},
            },
            {
                "thought": "確認できた資料をまとめます",
                "tool": "answer",
                "args": {
                    "answer_ja": "鳥海山周辺にはツキノワグマが生息しています。",
                    "sources": [
                        {"kind": "knowledge", "doc_id": "nature/animals"}
                    ],
                    "coverage": "full",
                },
            },
        ]
        self.messages: list[list[dict[str, str]]] = []

    async def generate(
        self,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> str:
        self.messages.append(messages)
        return json.dumps(self.responses.pop(0), ensure_ascii=False)


@respx.mock
async def test_embedding_server_failure_degrades_to_lexical_answer() -> None:
    embedding_route = respx.post("http://embedding.test/v1/embeddings").mock(
        return_value=httpx.Response(400, json={"error": "unavailable"})
    )
    settings = Settings(EMBEDDING_SERVER="http://embedding.test/v1")
    repository = ContractRepository()
    decisions = ScriptedDecisions()

    async with httpx.AsyncClient() as http_client:
        embedding = OpenAIEmbeddingClient(settings, http_client=http_client)
        agent = KnowledgeSearchAgent(
            KnowledgeRetrieval(repository, embedding),
            NoWebSearch(),
            decision_client=decisions,
        )
        result = await agent.search("クマは出ますか")

    assert isinstance(result, SearchResult)
    assert result.coverage == "full"
    assert repository.lexical_calls == 1
    assert embedding_route.call_count == 1
    assert [step.tool for step in agent.last_trace.steps] == [
        "semantic_search",
        "lexical_search",
        "answer",
    ]
    assert "埋め込みサーバ障害" in decisions.messages[1][1]["content"]
