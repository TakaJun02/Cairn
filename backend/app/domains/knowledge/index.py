"""Markdown ナレッジを決定的に解析し、PostgreSQL へ冪等投入する。"""

import hashlib
import json
import logging
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Protocol

import yaml
from sqlalchemy import func, select

from app.core.config import Settings, get_settings
from app.core.db import session_scope
from app.db_models import KnowledgeChunk, KnowledgeDocument, Spot
from app.domains.knowledge.embedding import EmbeddingError, OpenAIEmbeddingClient

_LOGGER = logging.getLogger("app.knowledge.index")
_BACKEND_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_KNOWLEDGE_ROOT = _BACKEND_ROOT / "data" / "knowledge" / "ja"
MAX_CHUNK_TOKENS = 500
CHUNK_OVERLAP_TOKENS = 50
EMBEDDING_BATCH_SIZE = 32

_SECTION_HEADING = re.compile(r"^##(?!#)[ \t]+(.+?)[ \t]*$", re.MULTILINE)
_SPOT_FILE = re.compile(r"^spot_\d{3}$")
_APPROX_TOKEN = re.compile(
    r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]"
    r"|[A-Za-z0-9]+(?:[._/-][A-Za-z0-9]+)*"
    r"|[^\s]"
)


class KnowledgeIndexError(RuntimeError):
    """ナレッジファイルまたは DB 内容が索引できない。"""


class EmbeddingProvider(Protocol):
    async def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


@dataclass(frozen=True)
class KnowledgeChunkSource:
    chunk_index: int
    heading: str
    body: str
    search_text: str
    embedding_text: str


@dataclass(frozen=True)
class KnowledgeDocumentSource:
    doc_id: str
    category: str
    title: str
    spot_id: str | None
    frontmatter: dict[str, Any]
    body: str
    chunks: tuple[KnowledgeChunkSource, ...]


@dataclass(frozen=True)
class KnowledgeIndexSummary:
    documents: int
    documents_with_spot_id: int
    documents_without_spot_id: int
    chunks: int
    embedded_chunks: int
    null_embedding_chunks: int
    embedding_requested: int
    embedding_written: int
    embedding_reused: int
    removed_documents: int
    removed_chunks: int
    degraded_reason: str | None

    @property
    def status(self) -> str:
        return "degraded" if self.null_embedding_chunks else "ok"

    def as_dict(self) -> dict[str, Any]:
        return {"status": self.status, **asdict(self)}


@dataclass(frozen=True)
class KnowledgeValidationSummary:
    source_documents: int
    source_chunks: int
    indexed_documents: int
    indexed_chunks: int
    embedded_chunks: int
    documents_with_spot_id: int
    documents_without_spot_id: int
    missing_spot_references: dict[str, str]
    unindexed_doc_ids: list[str]
    stale_doc_ids: list[str]
    unindexed_chunk_keys: list[str]
    stale_chunk_keys: list[str]

    @property
    def status(self) -> str:
        has_report = bool(
            self.missing_spot_references
            or self.unindexed_doc_ids
            or self.stale_doc_ids
            or self.unindexed_chunk_keys
            or self.stale_chunk_keys
        )
        return "report" if has_report else "ok"

    def as_dict(self) -> dict[str, Any]:
        return {"status": self.status, **asdict(self)}


def normalize_search_text(value: str) -> str:
    """字句検索と同じ NFKC + casefold を 1 回だけ適用する。"""

    return unicodedata.normalize("NFKC", value).casefold()


def compose_chunk_text(title: str, heading: str, body: str) -> str:
    """意味検索と字句検索が共有する文書側テキストを作る。"""

    return "\n".join(part for part in (title, heading, body) if part)


def approximate_tokens(value: str) -> list[str]:
    """外部 tokenizer なしで安定して使える、日英混在向け概算トークン列。"""

    return [match.group(0) for match in _APPROX_TOKEN.finditer(value)]


def split_oversized_section(
    body: str,
    *,
    max_tokens: int = MAX_CHUNK_TOKENS,
    overlap_tokens: int = CHUNK_OVERLAP_TOKENS,
) -> list[str]:
    """500 概算トークンを超えた節だけ、50 トークン重複で再分割する。"""

    if max_tokens <= 0:
        raise ValueError("max_tokens は正の整数で指定してください")
    if overlap_tokens < 0 or overlap_tokens >= max_tokens:
        raise ValueError("overlap_tokens は 0 以上 max_tokens 未満で指定してください")

    stripped = body.strip()
    spans = [(match.start(), match.end()) for match in _APPROX_TOKEN.finditer(stripped)]
    if len(spans) <= max_tokens:
        return [stripped]

    pieces: list[str] = []
    start_token = 0
    while start_token < len(spans):
        end_token = min(start_token + max_tokens, len(spans))
        start_character = spans[start_token][0]
        end_character = spans[end_token - 1][1]
        pieces.append(stripped[start_character:end_character].strip())
        if end_token == len(spans):
            break
        start_token = end_token - overlap_tokens
    return pieces


def derive_document_identity(path: Path, knowledge_root: Path) -> tuple[str, str, str | None]:
    """ja/ 相対 doc_id、親ディレクトリ category、厳密な spot_id を導出する。"""

    try:
        relative = path.resolve().relative_to(knowledge_root.resolve())
    except ValueError as exc:
        raise KnowledgeIndexError(f"ja/ 配下ではないファイルです: {path}") from exc
    doc_id = relative.with_suffix("").as_posix()
    category = relative.parent.name
    spot_id = path.stem if _SPOT_FILE.fullmatch(path.stem) else None
    return doc_id, category, spot_id


def parse_knowledge_file(path: Path, knowledge_root: Path) -> KnowledgeDocumentSource:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise KnowledgeIndexError(f"Markdown を読み込めません: {path}: {exc}") from exc

    frontmatter, body = _split_frontmatter(raw, path)
    title = frontmatter.get("title")
    if not isinstance(title, str) or not title.strip():
        raise KnowledgeIndexError(f"frontmatter.title がありません: {path}")

    doc_id, category, spot_id = derive_document_identity(path, knowledge_root)
    chunks: list[KnowledgeChunkSource] = []
    for heading, section_body in _split_heading_sections(body):
        for piece in split_oversized_section(section_body):
            embedding_text = compose_chunk_text(title.strip(), heading, piece)
            chunks.append(
                KnowledgeChunkSource(
                    chunk_index=len(chunks),
                    heading=heading,
                    body=piece,
                    search_text=normalize_search_text(embedding_text),
                    embedding_text=embedding_text,
                )
            )

    if not chunks:
        raise KnowledgeIndexError(f"索引対象の本文がありません: {path}")
    return KnowledgeDocumentSource(
        doc_id=doc_id,
        category=category,
        title=title.strip(),
        spot_id=spot_id,
        frontmatter=frontmatter,
        body=body.strip(),
        chunks=tuple(chunks),
    )


def load_knowledge_documents(
    knowledge_root: Path = DEFAULT_KNOWLEDGE_ROOT,
) -> list[KnowledgeDocumentSource]:
    """日本語ディレクトリだけを読み、doc_id 順に返す。"""

    if not knowledge_root.is_dir():
        raise KnowledgeIndexError(f"日本語ナレッジディレクトリがありません: {knowledge_root}")
    paths = sorted(knowledge_root.rglob("*.md"))
    documents = [parse_knowledge_file(path, knowledge_root) for path in paths]
    doc_ids = [document.doc_id for document in documents]
    if len(doc_ids) != len(set(doc_ids)):
        raise KnowledgeIndexError("doc_id が重複しています")
    return documents


async def index_knowledge(
    settings: Settings | None = None,
    *,
    knowledge_root: Path = DEFAULT_KNOWLEDGE_ROOT,
    embedding_provider: EmbeddingProvider | None = None,
    embedding_batch_size: int = EMBEDDING_BATCH_SIZE,
) -> KnowledgeIndexSummary:
    """ソースとの差分を反映し、必要なチャンクだけ埋め込む。"""

    if embedding_batch_size <= 0:
        raise ValueError("embedding_batch_size は正の整数で指定してください")
    resolved_settings = settings or get_settings()
    documents = load_knowledge_documents(knowledge_root)
    candidate_chunks = {
        (document.doc_id, chunk.chunk_index): (document, chunk)
        for document in documents
        for chunk in document.chunks
    }
    document_ids = {document.doc_id for document in documents}

    owned_provider = embedding_provider is None
    provider = embedding_provider or OpenAIEmbeddingClient(resolved_settings)
    try:
        async with session_scope(resolved_settings) as session:
            database_spot_ids = set((await session.scalars(select(Spot.spot_id))).all())
            invalid_spots = {
                document.doc_id: document.spot_id
                for document in documents
                if document.spot_id is not None and document.spot_id not in database_spot_ids
            }
            if invalid_spots:
                raise KnowledgeIndexError(
                    "参照先のない spot_id があります。"
                    "先に seed / validate-knowledge を実行してください: "
                    f"{invalid_spots}"
                )

            existing_documents = {
                document.doc_id: document
                for document in (await session.scalars(select(KnowledgeDocument))).all()
            }
            existing_chunks = {
                (chunk.doc_id, chunk.chunk_index): chunk
                for chunk in (await session.scalars(select(KnowledgeChunk))).all()
            }

            requested_keys: list[tuple[str, int]] = []
            reused_embeddings = 0
            for key, (_, source_chunk) in candidate_chunks.items():
                existing = existing_chunks.get(key)
                unchanged = existing is not None and _text_hash(
                    existing.search_text
                ) == _text_hash(source_chunk.search_text)
                if unchanged and existing.embedding is not None:
                    reused_embeddings += 1
                else:
                    requested_keys.append(key)

            embeddings, degraded_reason = await _embed_requested_chunks(
                provider,
                requested_keys,
                candidate_chunks,
                embedding_batch_size,
            )

            now = datetime.now(UTC)
            for source in documents:
                existing = existing_documents.get(source.doc_id)
                if existing is None:
                    session.add(
                        KnowledgeDocument(
                            doc_id=source.doc_id,
                            category=source.category,
                            title=source.title,
                            spot_id=source.spot_id,
                            frontmatter=source.frontmatter,
                            body=source.body,
                            updated_at=now,
                        )
                    )
                    continue
                document_values = (
                    source.category,
                    source.title,
                    source.spot_id,
                    source.frontmatter,
                    source.body,
                )
                existing_values = (
                    existing.category,
                    existing.title,
                    existing.spot_id,
                    existing.frontmatter,
                    existing.body,
                )
                if document_values != existing_values:
                    existing.category = source.category
                    existing.title = source.title
                    existing.spot_id = source.spot_id
                    existing.frontmatter = source.frontmatter
                    existing.body = source.body
                    existing.updated_at = now

            # relationship を持たない ORM モデル同士でも FK 順序を確実にする。
            await session.flush()

            for key, (_, source) in candidate_chunks.items():
                existing = existing_chunks.get(key)
                if existing is None:
                    session.add(
                        KnowledgeChunk(
                            doc_id=key[0],
                            chunk_index=key[1],
                            heading=source.heading,
                            body=source.body,
                            search_text=source.search_text,
                            embedding=embeddings.get(key),
                        )
                    )
                    continue
                search_changed = _text_hash(existing.search_text) != _text_hash(
                    source.search_text
                )
                existing.heading = source.heading
                existing.body = source.body
                existing.search_text = source.search_text
                if search_changed or existing.embedding is None:
                    existing.embedding = embeddings.get(key)

            stale_chunk_keys = [
                key
                for key in existing_chunks
                if key not in candidate_chunks and key[0] in document_ids
            ]
            for key in stale_chunk_keys:
                await session.delete(existing_chunks[key])

            stale_document_ids = set(existing_documents) - document_ids
            for doc_id in stale_document_ids:
                await session.delete(existing_documents[doc_id])

            await session.flush()
            embedded_count = int(
                await session.scalar(
                    select(func.count())
                    .select_from(KnowledgeChunk)
                    .where(KnowledgeChunk.embedding.is_not(None))
                )
                or 0
            )

        null_count = len(candidate_chunks) - embedded_count
        if null_count and degraded_reason is None:
            degraded_reason = "既存の NULL embedding を埋められませんでした"
        if null_count:
            _LOGGER.warning(
                "knowledge_index_degraded",
                extra={"null_embedding_chunks": null_count, "reason": degraded_reason},
            )
        return KnowledgeIndexSummary(
            documents=len(documents),
            documents_with_spot_id=sum(document.spot_id is not None for document in documents),
            documents_without_spot_id=sum(document.spot_id is None for document in documents),
            chunks=len(candidate_chunks),
            embedded_chunks=embedded_count,
            null_embedding_chunks=null_count,
            embedding_requested=len(requested_keys),
            embedding_written=sum(vector is not None for vector in embeddings.values()),
            embedding_reused=reused_embeddings,
            removed_documents=len(stale_document_ids),
            removed_chunks=len(stale_chunk_keys),
            degraded_reason=degraded_reason,
        )
    finally:
        if owned_provider and isinstance(provider, OpenAIEmbeddingClient):
            await provider.aclose()


async def validate_knowledge(
    settings: Settings | None = None,
    *,
    knowledge_root: Path = DEFAULT_KNOWLEDGE_ROOT,
) -> KnowledgeValidationSummary:
    """索引件数と spot 対応を検査し、孤児は失敗ではなく報告する。"""

    resolved_settings = settings or get_settings()
    source_documents = load_knowledge_documents(knowledge_root)
    source_by_id = {document.doc_id: document for document in source_documents}
    source_chunk_keys = {
        (document.doc_id, chunk.chunk_index)
        for document in source_documents
        for chunk in document.chunks
    }
    async with session_scope(resolved_settings) as session:
        spot_ids = set((await session.scalars(select(Spot.spot_id))).all())
        indexed_doc_ids = set(
            (await session.scalars(select(KnowledgeDocument.doc_id))).all()
        )
        indexed_document_count = len(indexed_doc_ids)
        indexed_chunk_count = int(
            await session.scalar(select(func.count()).select_from(KnowledgeChunk)) or 0
        )
        indexed_chunk_keys = {
            (doc_id, chunk_index)
            for doc_id, chunk_index in (
                await session.execute(
                    select(KnowledgeChunk.doc_id, KnowledgeChunk.chunk_index)
                )
            ).all()
        }
        embedded_chunk_count = int(
            await session.scalar(
                select(func.count())
                .select_from(KnowledgeChunk)
                .where(KnowledgeChunk.embedding.is_not(None))
            )
            or 0
        )
        linked_document_count = int(
            await session.scalar(
                select(func.count())
                .select_from(KnowledgeDocument)
                .where(KnowledgeDocument.spot_id.is_not(None))
            )
            or 0
        )

    missing_spot_references = {
        document.doc_id: document.spot_id
        for document in source_documents
        if document.spot_id is not None and document.spot_id not in spot_ids
    }
    return KnowledgeValidationSummary(
        source_documents=len(source_documents),
        source_chunks=len(source_chunk_keys),
        indexed_documents=indexed_document_count,
        indexed_chunks=indexed_chunk_count,
        embedded_chunks=embedded_chunk_count,
        documents_with_spot_id=linked_document_count,
        documents_without_spot_id=indexed_document_count - linked_document_count,
        missing_spot_references=missing_spot_references,
        unindexed_doc_ids=sorted(set(source_by_id) - indexed_doc_ids),
        stale_doc_ids=sorted(indexed_doc_ids - set(source_by_id)),
        unindexed_chunk_keys=_format_chunk_keys(source_chunk_keys - indexed_chunk_keys),
        stale_chunk_keys=_format_chunk_keys(indexed_chunk_keys - source_chunk_keys),
    )


async def _embed_requested_chunks(
    provider: EmbeddingProvider,
    requested_keys: list[tuple[str, int]],
    candidates: Mapping[
        tuple[str, int], tuple[KnowledgeDocumentSource, KnowledgeChunkSource]
    ],
    batch_size: int,
) -> tuple[dict[tuple[str, int], list[float] | None], str | None]:
    embeddings: dict[tuple[str, int], list[float] | None] = {
        key: None for key in requested_keys
    }
    degraded_reason: str | None = None
    for start in range(0, len(requested_keys), batch_size):
        batch_keys = requested_keys[start : start + batch_size]
        texts = [candidates[key][1].embedding_text for key in batch_keys]
        try:
            vectors = await provider.embed(texts)
            if len(vectors) != len(batch_keys):
                raise EmbeddingError("埋め込み応答の件数が入力件数と一致しません")
        except (EmbeddingError, OSError) as exc:
            degraded_reason = str(exc)
            break
        for key, vector in zip(batch_keys, vectors, strict=True):
            embeddings[key] = vector
    return embeddings, degraded_reason


def _split_frontmatter(raw: str, path: Path) -> tuple[dict[str, Any], str]:
    lines = raw.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise KnowledgeIndexError(f"frontmatter の開始区切りがありません: {path}")
    closing_index = next(
        (index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---"),
        None,
    )
    if closing_index is None:
        raise KnowledgeIndexError(f"frontmatter の終了区切りがありません: {path}")
    try:
        loaded = yaml.safe_load("".join(lines[1:closing_index])) or {}
    except yaml.YAMLError as exc:
        raise KnowledgeIndexError(f"frontmatter を解析できません: {path}: {exc}") from exc
    if not isinstance(loaded, dict) or any(not isinstance(key, str) for key in loaded):
        raise KnowledgeIndexError(f"frontmatter がオブジェクトではありません: {path}")
    return _json_compatible(loaded), "".join(lines[closing_index + 1 :])


def _split_heading_sections(body: str) -> list[tuple[str, str]]:
    matches = list(_SECTION_HEADING.finditer(body))
    if not matches:
        return [("", body.strip())] if body.strip() else []
    sections: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        sections.append((match.group(1).strip(), body[match.end() : end].strip()))
    return sections


def _json_compatible(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, list):
        return [_json_compatible(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_compatible(item) for key, item in value.items()}
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _text_hash(value: str) -> bytes:
    return hashlib.sha256(value.encode("utf-8")).digest()


def _format_chunk_keys(keys: set[tuple[str, int]]) -> list[str]:
    return [f"{doc_id}#{chunk_index}" for doc_id, chunk_index in sorted(keys)]
