"""意味検索・字句検索の決定的な検索規則。"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Protocol

from app.domains.narration.search.records import (
    KnowledgeDocumentRecord,
    KnowledgeHit,
    KnowledgeRepositoryPort,
)

QUERY_INSTRUCT_PREFIX = (
    "Instruct: Given a web search query, retrieve relevant passages that answer the query\n"
    "Query: "
)
DEFAULT_RESULT_LIMIT = 4
DEFAULT_MIN_SIMILARITY = 0.35

# 長いものを先に剥がす。大学ドメインの語彙は含めない。
VARIANT_SUFFIXES = (
    "までの行き方",
    "への行き方",
    "の行き方",
    "について",
    "の登山コース",
    "登山コース",
    "の登山道",
    "登山道",
    "のアクセス",
    "アクセス",
    "の営業時間",
    "営業時間",
    "の駐車場",
    "駐車場",
    "のコース",
    "コース",
    "の周辺",
    "周辺",
    "の付近",
    "付近",
)

_KATAKANA_RUN = re.compile(r"[ァ-ヴー]{2,}")
_KANJI_RUN = re.compile(r"[々〆ヵヶ一-龯]{2,}")


class QueryEmbeddingProvider(Protocol):
    async def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class SemanticEmbeddingError(RuntimeError):
    """質問文の埋め込みだけが失敗した。"""


@dataclass(frozen=True)
class RetrievalBatch:
    hits: tuple[KnowledgeHit, ...]
    used_terms: tuple[str, ...]
    expanded: bool = False


class KnowledgeRetrieval:
    def __init__(
        self,
        repository: KnowledgeRepositoryPort,
        embedding_provider: QueryEmbeddingProvider,
        *,
        result_limit: int = DEFAULT_RESULT_LIMIT,
        min_similarity: float = DEFAULT_MIN_SIMILARITY,
    ) -> None:
        self.repository = repository
        self.embedding_provider = embedding_provider
        self.result_limit = result_limit
        self.min_similarity = min_similarity

    async def semantic_search(self, queries: Sequence[str]) -> RetrievalBatch:
        clean_queries = tuple(query.strip() for query in queries if query.strip())
        prefixed = [f"{QUERY_INSTRUCT_PREFIX}{query}" for query in clean_queries]
        try:
            vectors = await self.embedding_provider.embed(prefixed)
        except Exception as exc:  # noqa: BLE001 - 埋め込み障害を DB 障害と分離する
            raise SemanticEmbeddingError("質問文を埋め込めませんでした") from exc
        if len(vectors) != len(clean_queries):
            raise SemanticEmbeddingError("埋め込み応答の件数が queries と一致しません")

        by_key: dict[tuple[str, int], KnowledgeHit] = {}
        for query, vector in zip(clean_queries, vectors, strict=True):
            hits = await self.repository.vector_search(
                vector,
                limit=self.result_limit,
                min_similarity=self.min_similarity,
            )
            for hit in hits:
                key = (hit.doc_id, hit.chunk_index)
                candidate = replace(hit, matched_keywords=(query,))
                previous = by_key.get(key)
                if previous is None or (candidate.similarity or 0) > (
                    previous.similarity or 0
                ):
                    by_key[key] = candidate
        ranked = sorted(
            by_key.values(),
            key=lambda hit: (
                -(hit.similarity or 0.0),
                hit.doc_id,
                hit.chunk_index,
            ),
        )[: self.result_limit]
        return RetrievalBatch(hits=tuple(ranked), used_terms=clean_queries)

    async def lexical_search(self, keywords: Sequence[str]) -> RetrievalBatch:
        chunks = await self.repository.lexical_chunks()
        return rank_lexical_chunks(chunks, keywords, limit=self.result_limit)

    async def get_documents(
        self, doc_ids: Sequence[str]
    ) -> list[KnowledgeDocumentRecord]:
        return await self.repository.get_documents(doc_ids)


def rank_lexical_chunks(
    chunks: Sequence[KnowledgeHit],
    keywords: Sequence[str],
    *,
    limit: int = DEFAULT_RESULT_LIMIT,
) -> RetrievalBatch:
    """種類数、title/heading、総ヒット数の順で安定ソートする。"""

    original = tuple(dict.fromkeys(keyword.strip() for keyword in keywords if keyword.strip()))
    first = _rank(chunks, original, limit=limit)
    if first:
        return RetrievalBatch(hits=tuple(first), used_terms=original, expanded=False)

    variants = expand_keyword_variants(original)
    if not variants:
        return RetrievalBatch(hits=(), used_terms=original, expanded=True)
    retried = _rank(chunks, variants, limit=limit)
    return RetrievalBatch(hits=tuple(retried), used_terms=variants, expanded=True)


def expand_keyword_variants(keywords: Sequence[str]) -> tuple[str, ...]:
    """鳥海山向け接尾辞除去と固有語抽出を行う。"""

    expanded: list[str] = []
    originals = {normalize_search_text(keyword) for keyword in keywords}
    for keyword in keywords:
        clean = keyword.strip()
        for suffix in VARIANT_SUFFIXES:
            if clean.endswith(suffix) and len(clean) > len(suffix):
                base = clean[: -len(suffix)].rstrip(" のへまで")
                if base:
                    expanded.append(base)
                break
        katakana = _longest_run(_KATAKANA_RUN, clean)
        if katakana is not None:
            expanded.append(katakana)
        kanji = _longest_run(_KANJI_RUN, clean)
        if kanji is not None:
            expanded.append(kanji)

    result: list[str] = []
    seen: set[str] = set()
    for value in expanded:
        normalized = normalize_search_text(value)
        if normalized in originals or normalized in seen:
            continue
        seen.add(normalized)
        result.append(value)
    return tuple(result)


def centered_snippet(
    body: str,
    terms: Sequence[str],
    *,
    radius: int = 200,
) -> tuple[str, bool]:
    """最初の字句ヒットを中心に前後 200 文字を返す。"""

    normalized_body = normalize_search_text(body)
    match_start: int | None = None
    match_length = 0
    for term in terms:
        normalized_term = normalize_search_text(term)
        if not normalized_term:
            continue
        position = normalized_body.find(normalized_term)
        if position >= 0 and (match_start is None or position < match_start):
            match_start = position
            match_length = len(normalized_term)

    if match_start is None:
        start = 0
        end = min(len(body), radius * 2)
    else:
        center = match_start + match_length // 2
        start = max(0, center - radius)
        end = min(len(body), center + radius)
    return body[start:end], start > 0 or end < len(body)


def _rank(
    chunks: Sequence[KnowledgeHit],
    keywords: Sequence[str],
    *,
    limit: int,
) -> list[KnowledgeHit]:
    normalized_keywords = tuple(
        (keyword, normalize_search_text(keyword))
        for keyword in keywords
        if normalize_search_text(keyword)
    )
    scored: list[tuple[tuple[int, int, int], KnowledgeHit]] = []
    for chunk in chunks:
        searchable = chunk.search_text or normalize_search_text(
            "\n".join((chunk.title, chunk.heading, chunk.body))
        )
        title_heading = normalize_search_text("\n".join((chunk.title, chunk.heading)))
        matched: list[str] = []
        total_hits = 0
        heading_hit = False
        for original, normalized in normalized_keywords:
            count = searchable.count(normalized)
            if count == 0:
                continue
            matched.append(original)
            total_hits += count
            heading_hit = heading_hit or normalized in title_heading
        if not matched:
            continue
        score = (len(matched), int(heading_hit), total_hits)
        scored.append((score, replace(chunk, matched_keywords=tuple(matched))))

    scored.sort(
        key=lambda value: (
            -value[0][0],
            -value[0][1],
            -value[0][2],
            value[1].doc_id,
            value[1].chunk_index,
        )
    )
    return [hit for _, hit in scored[:limit]]


def _longest_run(pattern: re.Pattern[str], value: str) -> str | None:
    matches = pattern.findall(value)
    return max(matches, key=len) if matches else None


def normalize_search_text(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()
