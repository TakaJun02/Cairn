"""stdout JSON ログと request_id の伝播。"""

import contextvars
import json
import logging
import sys
import time
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

request_id_context: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")

_STANDARD_LOG_RECORD_FIELDS = set(logging.makeLogRecord({}).__dict__)


class JsonFormatter(logging.Formatter):
    """LogRecord を 1 行 JSON へ変換する。"""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": request_id_context.get(),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_LOG_RECORD_FIELDS and key not in {"message", "asctime"}:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str, separators=(",", ":"))


def configure_logging(level: str = "INFO") -> None:
    """`basicConfig` を使わず、プロセスの stdout ハンドラを明示設定する。"""

    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root_logger.addHandler(handler)

    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(logger_name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True


class RequestIdMiddleware(BaseHTTPMiddleware):
    """リクエストごとに UUID を採番し、ログとレスポンスへ載せる。"""

    async def dispatch(self, request: Request, call_next: Any) -> Any:
        request_id = str(uuid4())
        token = request_id_context.set(request_id)
        started_at = time.perf_counter()
        logger = logging.getLogger("app.request")
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = request_id
            logger.info(
                "request_completed",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": response.status_code,
                    "duration_ms": round((time.perf_counter() - started_at) * 1000, 2),
                },
            )
            return response
        except BaseException:
            logger.exception(
                "request_failed",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "duration_ms": round((time.perf_counter() - started_at) * 1000, 2),
                },
            )
            raise
        finally:
            request_id_context.reset(token)
