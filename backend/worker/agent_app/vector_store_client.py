"""会話の長期記憶（ChromaDB）クライアント。

埋め込みは vLLM の OpenAI互換API（.env の `Embedding_server`）で生成し、
ベクトルを ChromaDB に保存・検索する。
ChromaDB または埋め込みサーバに接続できない場合、長期記憶は無効化されるだけで
エージェントは動き続ける。
"""
from __future__ import annotations

import os
import threading

import chromadb

from backend.worker import llm_client

CHROMA_URL = os.getenv("CHROMA_URL", "http://chromadb:8000")
COLLECTION_NAME = "conversation_history"

_lock = threading.Lock()
_collection = None
_init_failed = False


def _get_collection():
    """コレクションを遅延初期化して返す。接続不可ならNone。"""
    global _collection, _init_failed
    if _collection is not None or _init_failed:
        return _collection
    with _lock:
        if _collection is not None or _init_failed:
            return _collection
        try:
            host = CHROMA_URL.split("://")[-1].split(":")[0]
            port = int(CHROMA_URL.rsplit(":", 1)[-1])
            client = chromadb.HttpClient(host=host, port=port)
            # 埋め込みは llm_client.embed で明示的に生成するため、
            # コレクションに埋め込み関数は紐付けない
            _collection = client.get_or_create_collection(name=COLLECTION_NAME)
        except Exception as exc:
            print(f"[WARN] ChromaDB unavailable at {CHROMA_URL}; long-term memory disabled: {exc}")
            _init_failed = True
    return _collection


def add_conversation(conversation_id: str, text: str) -> None:
    """会話1ターンぶんのテキストをベクトル化してベクトルストアに保存する。"""
    collection = _get_collection()
    if collection is None:
        return
    try:
        embedding = llm_client.embed([text])[0]
        collection.add(ids=[conversation_id], embeddings=[embedding], documents=[text])
        print(f"[Vector Store] Added conversation {conversation_id}.")
    except Exception as exc:
        print(f"[ERROR] Failed to add conversation {conversation_id}: {exc}")


def search_similar_conversations(query_text: str, n_results: int = 2) -> list:
    """クエリテキストに類似した過去の会話テキストを検索して返す。"""
    collection = _get_collection()
    if collection is None:
        return []
    try:
        query_embedding = llm_client.embed([query_text])[0]
        results = collection.query(query_embeddings=[query_embedding], n_results=n_results)
    except Exception as exc:
        print(f"[ERROR] Long-term memory search failed: {exc}")
        return []

    similar_docs = (results.get("documents") or [[]])[0]
    print(f"[Vector Store] Found {len(similar_docs)} similar conversations.")
    return similar_docs
