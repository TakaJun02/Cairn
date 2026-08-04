"""実 DB と埋め込みサーバに対する意味検索・字句検索を検証する。"""

import asyncio
import os

import pytest

from app.core.config import get_settings
from app.core.db import dispose_engine, get_session_factory
from app.domains.knowledge.embedding import OpenAIEmbeddingClient
from app.domains.narration.search.repository import KnowledgeSearchRepository
from app.domains.narration.search.retrieval import KnowledgeRetrieval

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DB_INTEGRATION") != "1",
    reason="RUN_DB_INTEGRATION=1 のとき compose DB に対して実行する",
)


def test_semantic_and_lexical_search_against_indexed_knowledge() -> None:
    settings = get_settings()

    async def exercise() -> None:
        factory = get_session_factory(settings)
        embedding = OpenAIEmbeddingClient(settings)
        try:
            async with factory() as session:
                retrieval = KnowledgeRetrieval(
                    KnowledgeSearchRepository(session),
                    embedding,
                )
                semantic = await retrieval.semantic_search(["クマは出ますか"])
                lexical = await retrieval.lexical_search(["象潟IC"])

            assert semantic.hits
            assert all(hit.similarity is not None for hit in semantic.hits)
            assert all((hit.similarity or 0) >= 0.35 for hit in semantic.hits)
            assert all(hit.doc_id and hit.chunk_count >= 1 for hit in semantic.hits)
            assert lexical.hits
            assert any(
                "象潟ic" in f"{hit.title}\n{hit.heading}\n{hit.body}".casefold()
                for hit in lexical.hits
            )
            assert all(hit.doc_id and hit.chunk_count >= 1 for hit in lexical.hits)
        finally:
            await embedding.aclose()
            await dispose_engine()

    asyncio.run(exercise())
