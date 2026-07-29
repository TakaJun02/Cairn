"""vLLM (OpenAI互換API) への同期HTTPクライアント。

システム内の全てのテキスト生成・埋め込み生成はこのモジュールを経由する。
接続先は .env で指定する:
  - 生成:   `Inference_server` (例: http://127.0.0.1:8000/v1)
  - 埋め込み: `Embedding_server` (例: http://172.28.208.107:8001/v1)
モデル名は `INFERENCE_MODEL` / `EMBEDDING_MODEL` で指定できるが、未指定の場合は
各サーバの /models から自動検出する（vLLMは通常1サーバ1モデル運用のため）。
"""
from __future__ import annotations

import os
import threading
from typing import Dict, List, Optional

import httpx
from dotenv import load_dotenv

# ローカル実行時にリポジトリ直下の .env を読む（設定済みの環境変数は上書きしない）
load_dotenv()

BASE_URL = (
    os.getenv("Inference_server")
    or os.getenv("INFERENCE_SERVER")
    or "http://127.0.0.1:8000/v1"
).rstrip("/")

EMBEDDING_BASE_URL = (
    os.getenv("Embedding_server") or os.getenv("EMBEDDING_SERVER") or ""
).rstrip("/")

MODEL_OVERRIDE = os.getenv("INFERENCE_MODEL")
EMBEDDING_MODEL_OVERRIDE = os.getenv("EMBEDDING_MODEL")
REQUEST_TIMEOUT = float(os.getenv("INFERENCE_TIMEOUT_SECONDS", "600"))

_model_lock = threading.Lock()
_detected_models: Dict[str, str] = {}


def _detect_model(base_url: str) -> str:
    """サーバの /models から先頭のモデル名を検出してキャッシュする。"""
    if base_url not in _detected_models:
        with _model_lock:
            if base_url not in _detected_models:
                response = httpx.get(f"{base_url}/models", timeout=30.0)
                response.raise_for_status()
                models = response.json().get("data") or []
                if not models:
                    raise RuntimeError(f"No models served at {base_url}")
                _detected_models[base_url] = models[0]["id"]
    return _detected_models[base_url]


def resolve_model() -> str:
    """生成に使用するモデル名を返す。"""
    return MODEL_OVERRIDE or _detect_model(BASE_URL)


def _embedding_base() -> str:
    if not EMBEDDING_BASE_URL:
        raise RuntimeError("Embedding_server is not configured (.env を確認してください)")
    return EMBEDDING_BASE_URL


def resolve_embedding_model() -> str:
    """埋め込みに使用するモデル名を返す。"""
    return EMBEDDING_MODEL_OVERRIDE or _detect_model(_embedding_base())


def chat(
    messages: List[Dict[str, str]],
    *,
    temperature: Optional[float] = None,
    json_mode: bool = False,
    max_tokens: Optional[int] = None,
) -> str:
    """/chat/completions を呼び出し、応答本文のテキストを返す。"""
    payload: Dict = {"model": resolve_model(), "messages": messages}
    if temperature is not None:
        payload["temperature"] = temperature
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    response = httpx.post(
        f"{BASE_URL}/chat/completions", json=payload, timeout=REQUEST_TIMEOUT
    )
    response.raise_for_status()
    content = response.json()["choices"][0]["message"]["content"]
    return (content or "").strip()


def generate_text(
    system_prompt: str,
    user_prompt: str,
    history: Optional[List[Dict[str, str]]] = None,
    *,
    temperature: Optional[float] = None,
    json_mode: bool = False,
) -> str:
    """system/history/user を組み立てて1回の生成を行う。

    多くのチャットテンプレート（Gemma系など）はsystemロールを先頭1件しか
    許容しないため、history中のsystemメッセージはsystemプロンプト側へ統合する。
    """
    system_parts = [system_prompt] if system_prompt else []
    chat_history: List[Dict[str, str]] = []
    for message in history or []:
        if message.get("role") == "system":
            system_parts.append(message.get("content", ""))
        else:
            chat_history.append(message)

    messages: List[Dict[str, str]] = []
    if system_parts:
        messages.append({"role": "system", "content": "\n\n".join(system_parts)})
    messages.extend(chat_history)
    if user_prompt:
        messages.append({"role": "user", "content": user_prompt})

    return chat(messages, temperature=temperature, json_mode=json_mode)


def embed(texts: List[str]) -> List[List[float]]:
    """/embeddings を呼び出し、入力と同じ順序の埋め込みベクトルを返す。"""
    payload = {"model": resolve_embedding_model(), "input": texts}
    response = httpx.post(
        f"{_embedding_base()}/embeddings", json=payload, timeout=REQUEST_TIMEOUT
    )
    response.raise_for_status()
    data = response.json()["data"]
    return [item["embedding"] for item in sorted(data, key=lambda d: d["index"])]
