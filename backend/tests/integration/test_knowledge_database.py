"""compose PostgreSQL 上で日本語ナレッジ索引の件数と冪等性を検証する。"""

import asyncio
import os

import pytest
from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.db import dispose_engine, session_scope
from app.db_models import KnowledgeChunk, KnowledgeDocument
from app.domains.knowledge.embedding import EMBEDDING_DIMENSION
from app.domains.knowledge.index import index_knowledge
from app.seeds import load_seed_bundle, seed_database

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DB_INTEGRATION") != "1",
    reason="RUN_DB_INTEGRATION=1 のとき compose DB に対して実行する",
)


class RecordingEmbeddingProvider:
    def __init__(self) -> None:
        self.requested = 0

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.requested += len(texts)
        return [[0.0] * EMBEDDING_DIMENSION for _ in texts]


def test_index_knowledge_counts_and_second_run_is_idempotent() -> None:
    settings = get_settings()

    async def exercise() -> None:
        await seed_database(load_seed_bundle(), settings)
        provider = RecordingEmbeddingProvider()
        first = await index_knowledge(settings, embedding_provider=provider)
        requested_after_first = provider.requested
        second = await index_knowledge(settings, embedding_provider=provider)

        async with session_scope(settings) as session:
            document_count = int(
                await session.scalar(select(func.count()).select_from(KnowledgeDocument)) or 0
            )
            chunk_count = int(
                await session.scalar(select(func.count()).select_from(KnowledgeChunk)) or 0
            )
            spot_document_count = int(
                await session.scalar(
                    select(func.count())
                    .select_from(KnowledgeDocument)
                    .where(KnowledgeDocument.spot_id.is_not(None))
                )
                or 0
            )

        assert document_count == first.documents == second.documents == 118
        assert 400 <= chunk_count == first.chunks == second.chunks <= 600
        assert spot_document_count == first.documents_with_spot_id == 43
        assert first.embedded_chunks == second.embedded_chunks == chunk_count
        assert provider.requested == requested_after_first
        assert second.embedding_requested == 0
        await dispose_engine()

    asyncio.run(exercise())
