"""依存先を実際に呼ぶヘルスチェック。"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends

from app.api.schemas.health import (
    DependencyHealth,
    GeoDataHealth,
    HealthDependencies,
    HealthResponse,
    OSRMDependencyHealth,
)
from app.core.config import Settings, get_settings
from app.core.db import check_database
from app.domains.geo.osrm import read_osrm_build
from app.domains.geo.repo import GeoDataCounts, read_geo_data_counts

router = APIRouter(tags=["health"])

DatabaseProbe = Callable[[], Awaitable[None]]
GeoDataProbe = Callable[[], Awaitable[GeoDataCounts]]
_OSRM_TEST_COORDINATES = "140.0244,39.1594;140.0354,39.0342"


class HealthChecker:
    """DB、生成 vLLM、OSRM 2 系統を並行して検査する。"""

    def __init__(
        self,
        settings: Settings,
        database_probe: DatabaseProbe | None = None,
        geo_data_probe: GeoDataProbe | None = None,
    ) -> None:
        self.settings = settings
        self.database_probe = database_probe or (lambda: check_database(settings))
        self.geo_data_probe = geo_data_probe or (lambda: read_geo_data_counts(settings))

    async def check(self) -> HealthResponse:
        timeout = httpx.Timeout(5.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            db, vllm, osrm_car, osrm_foot, geo_result = await asyncio.gather(
                self._check_database(),
                self._check_vllm(client),
                self._check_osrm(client, self.settings.osrm_car_url, "car"),
                self._check_osrm(client, self.settings.osrm_foot_url, "foot"),
                self._check_geo_data(),
            )
        geo_data, geo_data_ok, geo_data_detail = geo_result

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
        if not geo_data_ok:
            logging.getLogger("app.health").warning(
                "geo_data_unavailable",
                extra={"detail": geo_data_detail},
            )
        overall = (
            "ok"
            if all(item.status == "ok" for item in dependency_results) and geo_data_ok
            else "degraded"
        )
        return HealthResponse(status=overall, dependencies=dependencies, geo_data=geo_data)

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
                    "設定モデルが /models にありません: "
                    f"{self.settings.inference_model}",
                )
            return _result("ok", started_at, self.settings.inference_model or None)
        except Exception as exc:  # noqa: BLE001 - 依存先の全失敗を本文へ変換する
            return _result("error", started_at, _error_detail(exc))

    async def _check_osrm(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        profile: str,
    ) -> OSRMDependencyHealth:
        started_at = time.perf_counter()
        build: str | None = None
        try:
            build = read_osrm_build()
            response = await client.get(
                f"{base_url.rstrip('/')}/route/v1/{profile}/{_OSRM_TEST_COORDINATES}",
                params={"overview": "false"},
            )
            response.raise_for_status()
            code = response.json().get("code")
            if code != "Ok":
                return _osrm_result(
                    "error", started_at, build=build, detail=f"OSRM code={code!r}"
                )
            return _osrm_result("ok", started_at, build=build)
        except Exception as exc:  # noqa: BLE001 - 依存先の全失敗を本文へ変換する
            return _osrm_result("error", started_at, build=build, detail=_error_detail(exc))

    async def _check_geo_data(self) -> tuple[GeoDataHealth, bool, str | None]:
        try:
            counts = await asyncio.wait_for(self.geo_data_probe(), timeout=5.0)
            return (
                GeoDataHealth(
                    spot_approach=counts.spot_approach,
                    travel_times_car=counts.travel_times_car,
                    travel_times_foot=counts.travel_times_foot,
                ),
                True,
                None,
            )
        except Exception as exc:  # noqa: BLE001 - healthz 自体は必ず 200 で返す
            return GeoDataHealth(
                spot_approach=0,
                travel_times_car=0,
                travel_times_foot=0,
            ), False, _error_detail(exc)


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


def _osrm_result(
    status: str,
    started_at: float,
    *,
    build: str | None,
    detail: str | None = None,
) -> OSRMDependencyHealth:
    return OSRMDependencyHealth(
        status=status,
        latency_ms=round((time.perf_counter() - started_at) * 1000, 2),
        detail=detail,
        build=build,
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
