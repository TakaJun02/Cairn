from __future__ import annotations

from typing import Dict

from backend.worker import llm_client
from backend.worker.app.services.llm import retriever, prompt as prompt_mod


def retrieve_context(spot_ref: Dict, lang: str):
    # md_slug / spot_id / name を考慮した文脈取得
    return retriever.retrieve_context(spot_ref, lang)


def generate_text(prompt: str) -> str:
    # vLLM (OpenAI互換API) で生成
    return llm_client.chat([{"role": "user", "content": prompt}])


def generate_for_spot(spot: Dict, lang: str, style: str = "narration") -> str:
    """
    スポット個別の生成。
    - md_slug があれば該当 MD を探索
    - 見つからなくても description を必ず Facts に含める（プロンプト側の仕様）
    """
    ctx = retrieve_context(spot, lang)
    ptxt = prompt_mod.build_prompt(spot, ctx, lang, style=style)
    return generate_text(ptxt)
