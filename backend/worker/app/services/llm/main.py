from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI, HTTPException

from backend.worker.app.services.llm.describe import describe_job
from backend.worker.app.services.llm.schemas import DescribeRequest, DescribeResponse

logger = logging.getLogger(__name__)

# vLLMは継続バッチングで並行リクエストを捌けるため、ジョブは並列で投げる
MAX_CONCURRENCY = int(os.getenv("LLM_DESCRIBE_CONCURRENCY", "4"))

app = FastAPI(title="llm service")


@app.post("/describe", response_model=DescribeResponse)
def describe_endpoint(req: DescribeRequest) -> DescribeResponse:
    """スポットごとのナレーション生成をvLLMに投げ、結果をまとめて返す。"""
    try:
        with ThreadPoolExecutor(max_workers=MAX_CONCURRENCY) as executor:
            items = list(executor.map(lambda job: describe_job(job, req.language), req.jobs))
        return DescribeResponse(items=items)
    except Exception as e:
        logger.exception("Failed to generate descriptions")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
def health():
    return {"status": "ok"}
