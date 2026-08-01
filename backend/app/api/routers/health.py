"""依存先を実際に呼ぶヘルスチェック。"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends

from app.api.schemas.health import DependencyHealth, HealthDependencies, HealthResponse
from app.core.config import Settings, get_settings
from app.core.db import check_database

router = APIRouter(tags=["health"])

DatabaseProbe = Callable[[], Awaitable[None]]
_OSRM_TEST_COORDINATES = "140.0244,39.1594;140.0354,39.0342"


class HealthChecker:
    """DB、生成 vLLM、OSRM 2 系統を並行して検査する。"""

    def __init__(
        self,
        settings: Settings,
        database_probe: DatabaseProbe | None = None,
    ) -> None:
        self.settings = settings
        self.database_probe = database_probe or (lambda: check_database(settings))

    async def check(self) -> HealthResponse:
        timeout = httpx.Timeout(5.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            db, vllm, osrm_car, osrm_foot = await asyncio.gather(
                self._check_database(),
                self._check_vllm(client),
                self._check_osrm(client, self.settings.osrm_car_url, "car"),
                self._check_osrm(client, self.settings.osrm_foot_url, "foot"),
            )

        dependencies = HealthDependencies(
            db=db,
            vllm=vllm,
            osrm_car=osrm_car,
            osrm_foot=osrm_foot,
        )
        dependency_results = (db, vllm, osrm_car, osrm_foot)
        for dependency_name, result in zip(
            ("db", "vllm", "osrm_car", "osrm_foot"), dependency_results, strict=True
        ):
            if result.status == "error":
                logging.getLogger("app.health").warning(
                    "dependency_unavailable",
                    extra={"dependency": dependency_name, "detail": result.detail},
                )
        overall = "ok" if all(item.status == "ok" for item in dependency_results) else "degraded"
        return HealthResponse(status=overall, dependencies=dependencies)

    async def _check_database(self) -> DependencyHealth:
        started_at = time.perf_counter()
        try:
            await asyncio.wait_for(self.database_probe(), timeout=5.0)
            return _result("ok", started_at)
        except Exception as exc:  # noqa: BLE001 - healthz 自体は必ず 200 で返す
            return _result("error", started_at, _error_detail(exc))

    async def _check_vllm(self, client: httpx.AsyncClient) -> DependencyHealth:
        started_at = time.perf_counter()
        try:
            response = await client.get(f"{self.settings.inference_server.rstrip('/')}/models")
            response.raise_for_status()
            payload = response.json()
            model_ids = [item.get("id") for item in payload.get("data", [])]
            if self.settings.inference_model and self.settings.inference_model not in model_ids:
                return _result(
                    "error",
                    started_at,
                    f"設定モデルが /models にありません: {self.settings.inference_model}",
                )
            return _result("ok", started_at, self.settings.inference_model or None)
        except Exception as exc:  # noqa: BLE001 - 依存先の全失敗を本文へ変換する
            return _result("error", started_at, _error_detail(exc))

    async def _check_osrm(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        profile: str,
    ) -> DependencyHealth:
        started_at = time.perf_counter()
        try:
            response = await client.get(
                f"{base_url.rstrip('/')}/route/v1/{profile}/{_OSRM_TEST_COORDINATES}",
                params={"overview": "false"},
            )
            response.raise_for_status()
            code = response.json().get("code")
            if code != "Ok":
                return _result("error", started_at, f"OSRM code={code!r}")
            return _result("ok", started_at)
        except Exception as exc:  # noqa: BLE001 - 依存先の全失敗を本文へ変換する
            return _result("error", started_at, _error_detail(exc))


def _result(
    status: str,
    started_at: float,
    detail: str | None = None,
) -> DependencyHealth:
    return DependencyHealth(
        status=status,
        latency_ms=round((time.perf_counter() - started_at) * 1000, 2),
        detail=detail,
    )


def _error_detail(exc: Exception) -> str:
    message = str(exc).strip()
    return f"{type(exc).__name__}: {message}" if message else type(exc).__name__


def get_health_checker(settings: Annotated[Settings, Depends(get_settings)]) -> HealthChecker:
    return HealthChecker(settings)


@router.get("/healthz", response_model=HealthResponse)
async def healthz(
    checker: Annotated[HealthChecker, Depends(get_health_checker)],
) -> HealthResponse:
    return await checker.check()
