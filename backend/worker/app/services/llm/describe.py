from __future__ import annotations

import logging
import re

from backend.worker.app.services.llm import generator, prompt
from backend.worker.app.services.llm.schemas import DescribeItem, DescribeJob

logger = logging.getLogger(__name__)


def _extract_narration(raw_text: str) -> str:
    """LLM出力から <think> ブロックやエコーバックされた定型文を除去する。"""
    clean_text = re.sub(r"<think>.*?</think>", "", raw_text, flags=re.DOTALL)

    patterns_to_remove = [
        r"^あなたは鳥海山エリアを訪れる観光客向けのプロのツアーガイドです。\s*",
        r"^スポット「.*?」の現在の状況を伝える、簡潔な音声案内を作成してください。\s*",
        r"^スポット名: .*?\s*",
        r"^現在の状況: .*?\s*",
    ]
    for pattern in patterns_to_remove:
        clean_text = re.sub(pattern, "", clean_text, flags=re.MULTILINE | re.DOTALL)

    return clean_text.strip()


def describe_job(job: DescribeJob, language: str) -> DescribeItem:
    """単一スポットのナレーションテキストを生成する。"""
    spot = job.spot
    logger.debug(f"[{job.job_id}] Describing spot: {spot.spot_id}, situation={spot.situation}")

    ctx = generator.retrieve_context(spot.model_dump(), language)
    prompt_text = prompt.build_prompt(spot.model_dump(), ctx, language)

    raw_text = generator.generate_text(prompt_text)
    logger.info(f"[{job.job_id}] LLM raw output for spot {spot.spot_id}: {raw_text}")

    narration_text = _extract_narration(raw_text)
    logger.info(f"[{job.job_id}] Cleaned narration for spot {spot.spot_id}: {narration_text}")

    return DescribeItem(
        job_id=job.job_id,
        spot_id=spot.spot_id,
        playback=spot.playback,
        situation=spot.situation,
        text=narration_text,
    )
