"""OSRM の route / table / nearest を呼ぶ非同期クライアント。"""

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import httpx

from app.core.config import Settings

logger = logging.getLogger("app.geo.osrm")

Profile = Literal["car", "foot"]
_DEFAULT_BUILD_PATH = Path(__file__).resolve().parents[3] / "data" / "map" / "BUILD"


class OSRMError(RuntimeError):
    """OSRM の通信失敗または不正応答。"""


class OSRMNoRouteError(OSRMError):
    """指定した座標間に経路が無い。"""


@dataclass(frozen=True, slots=True)
class Coordinate:
    """外部 API と DB の双方で使う経度・緯度。"""

    lon: float
    lat: float

    def path_value(self) -> str:
        return f"{self.lon:.8f},{self.lat:.8f}"

    def geojson_value(self, precision: int | None = None) -> list[float]:
        if precision is None:
            return [self.lon, self.lat]
        return [round(self.lon, precision), round(self.lat, precision)]


@dataclass(frozen=True, slots=True)
class NearestResult:
    location: Coordinate
    distance_m: float


@dataclass(frozen=True, slots=True)
class RouteResult:
    distance_m: float
    duration_sec: float
    geometry: tuple[Coordinate, ...]


@dataclass(frozen=True, slots=True)
class TableResult:
    durations: tuple[tuple[float | None, ...], ...]
    distances: tuple[tuple[float | None, ...], ...]


def read_osrm_build(path: Path | None = None) -> str:
    """経路キャッシュの世代を分ける BUILD 識別子を読む。"""

    build_path = path or _DEFAULT_BUILD_PATH
    try:
        value = build_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise OSRMError(f"OSRM BUILD を読めません: {build_path}: {exc}") from exc
    if not value:
        raise OSRMError(f"OSRM BUILD が空です: {build_path}")
    return value


class OSRMClient:
    """同時実行数、タイムアウト、再試行を一か所で管理する。"""

    def __init__(
        self,
        settings: Settings,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings
        self._client = http_client or httpx.AsyncClient()
        self._owns_client = http_client is None
        self._semaphore = asyncio.Semaphore(settings.osrm_concurrency)

    async def __aenter__(self) -> "OSRMClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def nearest(self, profile: Profile, coordinate: Coordinate) -> NearestResult:
        url = f"{self._base_url(profile)}/nearest/v1/{profile}/{coordinate.path_value()}"
        payload = await self._request_json(
            url,
            params={"number": 1},
            timeout_sec=self._settings.osrm_request_timeout_sec,
            retries=self._settings.osrm_request_retries,
        )
        self._require_ok(payload, operation="nearest")
        waypoints = payload.get("waypoints")
        if not isinstance(waypoints, list) or not waypoints:
            raise OSRMError("OSRM nearest 応答に waypoints がありません")
        waypoint = waypoints[0]
        if not isinstance(waypoint, dict):
            raise OSRMError("OSRM nearest waypoint が object ではありません")
        location = self._parse_coordinate(waypoint.get("location"), "nearest.location")
        distance = self._parse_number(waypoint.get("distance"), "nearest.distance")
        return NearestResult(location=location, distance_m=distance)

    async def route(
        self,
        profile: Profile,
        coordinates: list[Coordinate] | tuple[Coordinate, ...],
    ) -> RouteResult:
        if len(coordinates) < 2:
            raise ValueError("route には2点以上が必要です")
        coordinate_path = ";".join(item.path_value() for item in coordinates)
        url = f"{self._base_url(profile)}/route/v1/{profile}/{coordinate_path}"
        payload = await self._request_json(
            url,
            params={
                "overview": "full",
                "geometries": "geojson",
                "steps": "false",
            },
            timeout_sec=self._settings.osrm_request_timeout_sec,
            retries=self._settings.osrm_request_retries,
        )
        self._require_ok(payload, operation="route")
        routes = payload.get("routes")
        if not isinstance(routes, list) or not routes or not isinstance(routes[0], dict):
            raise OSRMError("OSRM route 応答に routes がありません")
        route = routes[0]
        geometry = route.get("geometry")
        if not isinstance(geometry, dict) or geometry.get("type") != "LineString":
            raise OSRMError("OSRM route geometry が LineString ではありません")
        raw_coordinates = geometry.get("coordinates")
        if not isinstance(raw_coordinates, list) or len(raw_coordinates) < 2:
            raise OSRMError("OSRM route geometry の座標が不足しています")
        parsed_geometry = tuple(
            self._parse_coordinate(item, "route.geometry.coordinates")
            for item in raw_coordinates
        )
        return RouteResult(
            distance_m=self._parse_number(route.get("distance"), "route.distance"),
            duration_sec=self._parse_number(route.get("duration"), "route.duration"),
            geometry=parsed_geometry,
        )

    async def table(
        self,
        profile: Profile,
        coordinates: list[Coordinate] | tuple[Coordinate, ...],
    ) -> TableResult:
        if len(coordinates) < 2:
            raise ValueError("table には2点以上が必要です")
        coordinate_path = ";".join(item.path_value() for item in coordinates)
        url = f"{self._base_url(profile)}/table/v1/{profile}/{coordinate_path}"
        payload = await self._request_json(
            url,
            params={"annotations": "duration,distance"},
            timeout_sec=self._settings.osrm_table_timeout_sec,
            retries=self._settings.osrm_table_retries,
        )
        self._require_ok(payload, operation="table")
        return TableResult(
            durations=self._parse_matrix(payload.get("durations"), "table.durations"),
            distances=self._parse_matrix(payload.get("distances"), "table.distances"),
        )

    def _base_url(self, profile: Profile) -> str:
        if profile == "car":
            return self._settings.osrm_car_url.rstrip("/")
        if profile == "foot":
            return self._settings.osrm_foot_url.rstrip("/")
        raise ValueError(f"未対応の OSRM profile です: {profile}")

    async def _request_json(
        self,
        url: str,
        *,
        params: dict[str, str | int],
        timeout_sec: float,
        retries: int,
    ) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(retries + 1):
            try:
                async with self._semaphore:
                    response = await self._client.get(
                        url,
                        params=params,
                        timeout=httpx.Timeout(timeout_sec),
                    )
                if response.status_code == 429 or response.status_code >= 500:
                    raise httpx.HTTPStatusError(
                        f"再試行可能な OSRM HTTP {response.status_code}",
                        request=response.request,
                        response=response,
                    )
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise OSRMError("OSRM 応答が JSON object ではありません")
                return payload
            except (httpx.HTTPError, ValueError, OSRMError) as exc:
                last_error = exc
                retryable = not isinstance(exc, httpx.HTTPStatusError) or (
                    exc.response.status_code == 429 or exc.response.status_code >= 500
                )
                if attempt >= retries or not retryable:
                    break
                delay = self._settings.osrm_retry_backoff_sec * (2**attempt)
                logger.warning(
                    "OSRM 呼び出しを再試行します",
                    extra={"attempt": attempt + 1, "delay_sec": delay, "url": url},
                )
                await asyncio.sleep(delay)
        detail = str(last_error).strip() if last_error is not None else "原因不明"
        raise OSRMError(f"OSRM 呼び出しに失敗しました: {detail}") from last_error

    @staticmethod
    def _require_ok(payload: dict[str, Any], *, operation: str) -> None:
        code = payload.get("code")
        if code == "Ok":
            return
        message = payload.get("message")
        detail = f"code={code!r}" + (f", message={message}" if message else "")
        if code in {"NoRoute", "NoSegment"}:
            raise OSRMNoRouteError(f"OSRM {operation}: {detail}")
        raise OSRMError(f"OSRM {operation}: {detail}")

    @staticmethod
    def _parse_coordinate(value: Any, field: str) -> Coordinate:
        if not isinstance(value, list) or len(value) < 2:
            raise OSRMError(f"{field} が [lon, lat] ではありません")
        return Coordinate(
            lon=OSRMClient._parse_number(value[0], f"{field}[0]"),
            lat=OSRMClient._parse_number(value[1], f"{field}[1]"),
        )

    @staticmethod
    def _parse_number(value: Any, field: str) -> float:
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise OSRMError(f"{field} が数値ではありません")
        return float(value)

    @classmethod
    def _parse_matrix(
        cls,
        value: Any,
        field: str,
    ) -> tuple[tuple[float | None, ...], ...]:
        if not isinstance(value, list):
            raise OSRMError(f"{field} が配列ではありません")
        matrix: list[tuple[float | None, ...]] = []
        for row_index, row in enumerate(value):
            if not isinstance(row, list):
                raise OSRMError(f"{field}[{row_index}] が配列ではありません")
            parsed_row: list[float | None] = []
            for column_index, item in enumerate(row):
                if item is None:
                    parsed_row.append(None)
                else:
                    parsed_row.append(
                        cls._parse_number(item, f"{field}[{row_index}][{column_index}]")
                    )
            matrix.append(tuple(parsed_row))
        return tuple(matrix)
