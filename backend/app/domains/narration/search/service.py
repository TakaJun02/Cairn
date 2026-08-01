"""メインエージェントが呼ぶ `search_knowledge` Tool 入口。"""

from __future__ import annotations

from app.core.config import Settings, get_settings
from app.core.db import get_session_factory
from app.core.llm import GenerationClient
from app.domains.knowledge.embedding import OpenAIEmbeddingClient
from app.domains.narration.search.agent import (
    KnowledgeSearchAgent,
    SearchEventSink,
)
from app.domains.narration.search.repository import KnowledgeSearchRepository
from app.domains.narration.search.retrieval import KnowledgeRetrieval
from app.domains.narration.search.types import SearchResult, ToolError
from app.domains.narration.search.web import TavilyWebSearchClient


async def search_knowledge(
    request: str,
    spot_id: str | None = None,
    *,
    settings: Settings | None = None,
    event_sink: SearchEventSink | None = None,
) -> SearchResult | ToolError:
    """独立コンテキストで検索し、回答素材と出典だけを返す。"""

    resolved_settings = settings or get_settings()
    embedding = OpenAIEmbeddingClient(resolved_settings)
    web = TavilyWebSearchClient(resolved_settings)
    factory = get_session_factory(resolved_settings)
    try:
        async with factory() as session:
            repository = KnowledgeSearchRepository(session)
            retrieval = KnowledgeRetrieval(repository, embedding)
            agent = KnowledgeSearchAgent(
                retrieval,
                web,
                decision_client=GenerationClient(resolved_settings),
                event_sink=event_sink,
            )
            return await agent.search(request, spot_id)
    finally:
        await embedding.aclose()
        await web.aclose()

