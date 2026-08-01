"""`/healthz` の外部契約を依存先モックで検証する。"""

from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from app.api.routers.health import HealthChecker, get_health_checker
from app.core.config import Settings
from app.main import create_app


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        POSTGRES_PASSWORD="test",
        INFERENCE_SERVER="http://vllm.test/v1",
        INFERENCE_MODEL="test-model",
        OSRM_CAR_URL="http://osrm-car.test",
        OSRM_FOOT_URL="http://osrm-foot.test",
    )


@pytest.mark.asyncio
@respx.mock
async def test_healthz_returns_each_dependency_status() -> None:
    respx.get("http://vllm.test/v1/models").mock(
        return_value=httpx.Response(200, json={"data": [{"id": "test-model"}]})
    )
    respx.get(url__regex=r"http://osrm-car\.test/route/v1/car/.*").mock(
        return_value=httpx.Response(200, json={"code": "Ok"})
    )
    respx.get(url__regex=r"http://osrm-foot\.test/route/v1/foot/.*").mock(
        return_value=httpx.Response(200, json={"code": "Ok"})
    )

    checker = HealthChecker(_settings(), database_probe=AsyncMock(return_value=None))

    async def override_checker() -> HealthChecker:
        return checker

    app = create_app()
    app.dependency_overrides[get_health_checker] = override_checker
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/healthz")

    assert response.status_code == 200
    assert response.headers["x-request-id"]
    assert response.json()["status"] == "ok"
    assert response.json()["dependencies"] == {
        "db": {"status": "ok", "latency_ms": pytest.approx(0, abs=50), "detail": None},
        "vllm": {
            "status": "ok",
            "latency_ms": pytest.approx(0, abs=50),
            "detail": "test-model",
        },
        "osrm_car": {
            "status": "ok",
            "latency_ms": pytest.approx(0, abs=50),
            "detail": None,
        },
        "osrm_foot": {
            "status": "ok",
            "latency_ms": pytest.approx(0, abs=50),
            "detail": None,
        },
    }


@pytest.mark.asyncio
@respx.mock
async def test_healthz_stays_200_when_dependencies_are_down() -> None:
    async def unavailable_database() -> None:
        raise ConnectionError("database unavailable")

    respx.get("http://vllm.test/v1/models").mock(return_value=httpx.Response(503))
    respx.get(url__regex=r"http://osrm-car\.test/route/v1/car/.*").mock(
        return_value=httpx.Response(200, json={"code": "Ok"})
    )
    respx.get(url__regex=r"http://osrm-foot\.test/route/v1/foot/.*").mock(
        return_value=httpx.Response(200, json={"code": "NoRoute"})
    )

    checker = HealthChecker(_settings(), database_probe=unavailable_database)

    async def override_checker() -> HealthChecker:
        return checker

    app = create_app()
    app.dependency_overrides[get_health_checker] = override_checker
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/healthz")

    body = response.json()
    assert response.status_code == 200
    assert body["status"] == "degraded"
    assert body["dependencies"]["db"]["status"] == "error"
    assert body["dependencies"]["vllm"]["status"] == "error"
    assert body["dependencies"]["osrm_car"]["status"] == "ok"
    assert body["dependencies"]["osrm_foot"]["status"] == "error"
