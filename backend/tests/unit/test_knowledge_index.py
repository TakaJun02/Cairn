"""Markdown の決定的な分割・正規化・識別子導出を検証する。"""

from pathlib import Path

import pytest

from app.domains.knowledge.embedding import EmbeddingError
from app.domains.knowledge.index import (
    KnowledgeChunkSource,
    KnowledgeDocumentSource,
    _embed_requested_chunks,
    approximate_tokens,
    derive_document_identity,
    normalize_search_text,
    parse_knowledge_file,
    split_oversized_section,
)


def test_parse_chunks_at_level_two_headings(tmp_path: Path) -> None:
    root = tmp_path / "ja"
    path = root / "nature" / "sample.md"
    path.parent.mkdir(parents=True)
    path.write_text(
        """---
title: "ＡＢＣ鳥海"
category: nature
---

# ＡＢＣ鳥海

## 概要
Straße の説明です。

### 補足
この行も概要に含みます。

## アクセス
象潟ICから向かいます。
""",
        encoding="utf-8",
    )

    document = parse_knowledge_file(path, root)

    assert [chunk.heading for chunk in document.chunks] == ["概要", "アクセス"]
    assert "### 補足" in document.chunks[0].body
    assert document.chunks[0].search_text == normalize_search_text(
        "ＡＢＣ鳥海\n概要\nStraße の説明です。\n\n### 補足\nこの行も概要に含みます。"
    )
    assert not document.chunks[0].embedding_text.startswith("Instruct:")


def test_split_long_section_at_500_tokens_with_50_token_overlap() -> None:
    body = " ".join(f"word{index}" for index in range(725))

    pieces = split_oversized_section(body)

    assert len(pieces) == 2
    first_tokens = approximate_tokens(pieces[0])
    second_tokens = approximate_tokens(pieces[1])
    assert len(first_tokens) == 500
    assert len(second_tokens) == 275
    assert first_tokens[-50:] == second_tokens[:50]


def test_normalize_search_text_applies_nfkc_and_casefold() -> None:
    assert normalize_search_text("ＡＢＣ Straße ①") == "abc strasse 1"


def test_derive_doc_id_category_and_strict_spot_id(tmp_path: Path) -> None:
    root = tmp_path / "ja"
    numeric = root / "faci_spot" / "spot_012.md"
    named = root / "faci_spot" / "spot_meisho_numa.md"

    assert derive_document_identity(numeric, root) == (
        "faci_spot/spot_012",
        "faci_spot",
        "spot_012",
    )
    assert derive_document_identity(named, root) == (
        "faci_spot/spot_meisho_numa",
        "faci_spot",
        None,
    )


@pytest.mark.asyncio
async def test_embedding_failure_leaves_requested_chunk_null() -> None:
    class UnavailableEmbeddingProvider:
        async def embed(self, texts: list[str]) -> list[list[float]]:
            raise EmbeddingError("server unavailable")

    source_chunk = KnowledgeChunkSource(0, "概要", "本文", "検索", "埋め込み対象")
    source_document = KnowledgeDocumentSource(
        "nature/sample",
        "nature",
        "題名",
        None,
        {"title": "題名"},
        "本文",
        (source_chunk,),
    )
    key = (source_document.doc_id, 0)

    embeddings, reason = await _embed_requested_chunks(
        UnavailableEmbeddingProvider(),
        [key],
        {key: (source_document, source_chunk)},
        32,
    )

    assert embeddings == {key: None}
    assert reason == "server unavailable"
