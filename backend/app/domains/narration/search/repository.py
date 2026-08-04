"""知識チャンクの pgvector 検索と全文取得。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db_models import KnowledgeChunk, KnowledgeDocument
from app.domains.narration.search.records import (
    KnowledgeDocumentRecord,
    KnowledgeHit,
)


class KnowledgeSearchRepository:
    """呼び出し側の read-only セッションに参加する。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def vector_search(
        self,
        vector: Sequence[float],
        *,
        limit: int,
        min_similarity: float,
    ) -> list[KnowledgeHit]:
        counts = (
            select(
                KnowledgeChunk.doc_id.label("doc_id"),
                func.count().label("chunk_count"),
            )
            .group_by(KnowledgeChunk.doc_id)
            .subquery()
        )
        distance = KnowledgeChunk.embedding.cosine_distance(list(vector))
        rows = (
            await self.session.execute(
                select(
                    KnowledgeChunk.doc_id,
                    KnowledgeDocument.title,
                    KnowledgeDocument.spot_id,
                    KnowledgeChunk.chunk_index,
                    counts.c.chunk_count,
                    KnowledgeChunk.heading,
                    KnowledgeChunk.body,
                    KnowledgeChunk.search_text,
                    (1.0 - distance).label("similarity"),
                )
                .join(
                    KnowledgeDocument,
                    KnowledgeDocument.doc_id == KnowledgeChunk.doc_id,
                )
                .join(counts, counts.c.doc_id == KnowledgeChunk.doc_id)
                .where(
                    KnowledgeChunk.embedding.is_not(None),
                    distance <= 1.0 - min_similarity,
                )
                .order_by(distance, KnowledgeChunk.doc_id, KnowledgeChunk.chunk_index)
                .limit(limit)
            )
        ).all()
        return [
            KnowledgeHit(
                doc_id=row.doc_id,
                title=row.title,
                spot_id=row.spot_id,
                chunk_index=int(row.chunk_index),
                chunk_count=int(row.chunk_count),
                heading=row.heading,
                body=row.body,
                search_text=row.search_text,
                similarity=float(row.similarity),
            )
            for row in rows
        ]

    async def lexical_chunks(self) -> list[KnowledgeHit]:
        counts = (
            select(
                KnowledgeChunk.doc_id.label("doc_id"),
                func.count().label("chunk_count"),
            )
            .group_by(KnowledgeChunk.doc_id)
            .subquery()
        )
        rows = (
            await self.session.execute(
                select(
                    KnowledgeChunk.doc_id,
                    KnowledgeDocument.title,
                    KnowledgeDocument.spot_id,
                    KnowledgeChunk.chunk_index,
                    counts.c.chunk_count,
                    KnowledgeChunk.heading,
                    KnowledgeChunk.body,
                    KnowledgeChunk.search_text,
                )
                .join(
                    KnowledgeDocument,
                    KnowledgeDocument.doc_id == KnowledgeChunk.doc_id,
                )
                .join(counts, counts.c.doc_id == KnowledgeChunk.doc_id)
                .order_by(KnowledgeChunk.doc_id, KnowledgeChunk.chunk_index)
            )
        ).all()
        return [
            KnowledgeHit(
                doc_id=row.doc_id,
                title=row.title,
                spot_id=row.spot_id,
                chunk_index=int(row.chunk_index),
                chunk_count=int(row.chunk_count),
                heading=row.heading,
                body=row.body,
                search_text=row.search_text,
            )
            for row in rows
        ]

    async def get_documents(
        self, doc_ids: Sequence[str]
    ) -> list[KnowledgeDocumentRecord]:
        unique_ids = list(dict.fromkeys(doc_ids))
        if not unique_ids:
            return []
        rows = (
            await self.session.execute(
                select(
                    KnowledgeDocument.doc_id,
                    KnowledgeDocument.title,
                    KnowledgeDocument.spot_id,
                    KnowledgeChunk.chunk_index,
                    KnowledgeChunk.heading,
                    KnowledgeChunk.body,
                    KnowledgeChunk.search_text,
                )
                .join(
                    KnowledgeChunk,
                    KnowledgeChunk.doc_id == KnowledgeDocument.doc_id,
                )
                .where(KnowledgeDocument.doc_id.in_(unique_ids))
                .order_by(KnowledgeDocument.doc_id, KnowledgeChunk.chunk_index)
            )
        ).all()
        grouped: dict[str, dict[str, Any]] = {}
        for row in rows:
            value = grouped.setdefault(
                row.doc_id,
                {
                    "title": row.title,
                    "spot_id": row.spot_id,
                    "chunks": [],
                },
            )
            value["chunks"].append(row)

        documents: list[KnowledgeDocumentRecord] = []
        for doc_id in unique_ids:
            value = grouped.get(doc_id)
            if value is None:
                continue
            chunk_count = len(value["chunks"])
            documents.append(
                KnowledgeDocumentRecord(
                    doc_id=doc_id,
                    title=value["title"],
                    spot_id=value["spot_id"],
                    chunks=tuple(
                        KnowledgeHit(
                            doc_id=doc_id,
                            title=value["title"],
                            spot_id=value["spot_id"],
                            chunk_index=int(row.chunk_index),
                            chunk_count=chunk_count,
                            heading=row.heading,
                            body=row.body,
                            search_text=row.search_text,
                        )
                        for row in value["chunks"]
                    ),
                )
            )
        return documents
