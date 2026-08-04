"""DB 実装に依存しない知識検索レコードとリポジトリ契約。"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class KnowledgeHit:
    doc_id: str
    title: str
    spot_id: str | None
    chunk_index: int
    chunk_count: int
    heading: str
    body: str
    search_text: str | None = None
    similarity: float | None = None
    matched_keywords: tuple[str, ...] = ()


@dataclass(frozen=True)
class KnowledgeDocumentRecord:
    doc_id: str
    title: str
    spot_id: str | None
    chunks: tuple[KnowledgeHit, ...]

    @property
    def body(self) -> str:
        return "\n\n".join(
            "\n".join(part for part in (chunk.heading, chunk.body) if part)
            for chunk in self.chunks
        )


class KnowledgeRepositoryPort(Protocol):
    async def vector_search(
        self,
        vector: Sequence[float],
        *,
        limit: int,
        min_similarity: float,
    ) -> list[KnowledgeHit]: ...

    async def lexical_chunks(self) -> list[KnowledgeHit]: ...

    async def get_documents(
        self, doc_ids: Sequence[str]
    ) -> list[KnowledgeDocumentRecord]: ...
