"""パック生成の段1: 対象確定、冪等ジョブ、全アセット行の作成。"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.packs import PackAssetRole, PackAssetVariant
from app.core.config import Settings, get_settings
from app.db_models import PackAsset, PackJob, User
from app.domains.geo.along import buffer_for_mode, find_along_pois
from app.domains.geo.osrm import OSRMClient
from app.domains.geo.repo import GeoRepository, RouteRecord
from app.domains.geo.routes import RouteService
from app.domains.itinerary.repo import ItineraryRepository
from app.domains.itinerary.repo_types import ItineraryVersion

DEFAULT_ALONG_POI_LIMIT = 20

VISIT_VARIANTS = (
    PackAssetVariant.BASE,
    PackAssetVariant.WEATHER_CLOUDY,
    PackAssetVariant.WEATHER_RAIN,
    PackAssetVariant.CONGESTION_MID,
    PackAssetVariant.CONGESTION_HIGH,
)
PASS_BY_VARIANTS = (PackAssetVariant.BASE,)

_ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "queued": frozenset({"running"}),
    "running": frozenset({"ready", "partial", "failed"}),
    "ready": frozenset(),
    "partial": frozenset({"running"}),
    "failed": frozenset({"running"}),
}

logger = logging.getLogger("app.packs.planner")


class PackPlanningError(RuntimeError):
    """対象確定に必要な旅程または経路を読み取れない。"""


class PackItineraryNotFoundError(PackPlanningError):
    """要求したユーザーの旅程版が存在しない。"""


class PackStateTransitionError(RuntimeError):
    """ジョブ状態機械にない遷移を拒否する。"""


@dataclass(frozen=True, slots=True)
class PackJobData:
    job_id: UUID
    pack_id: UUID
    user_id: int
    itinerary_version: int
    epoch: int
    state: str
    params: dict[str, Any]
    params_hash: str
    progress: dict[str, Any]

    @classmethod
    def from_row(cls, row: PackJob) -> PackJobData:
        return cls(
            job_id=row.id,
            pack_id=row.pack_id,
            user_id=row.user_id,
            itinerary_version=row.itinerary_version,
            epoch=row.epoch,
            state=row.state,
            params=dict(row.params),
            params_hash=row.params_hash,
            progress=dict(row.progress),
        )


@dataclass(frozen=True, slots=True)
class _PlannedLeg:
    leg_id: str
    day: int
    index: int
    from_spot_id: str
    to_spot_id: str
    route: RouteRecord


def variants_for_role(role: PackAssetRole | str) -> tuple[PackAssetVariant, ...]:
    parsed = PackAssetRole(role)
    return VISIT_VARIANTS if parsed is PackAssetRole.VISIT else PASS_BY_VARIANTS


def calculate_total(
    visit_spot_ids: Sequence[str],
    pass_by_spot_ids: Sequence[str],
) -> int:
    """重複地点を一度だけ数え、role ごとのアセット数を確定する。"""

    visit = set(visit_spot_ids)
    pass_by = set(pass_by_spot_ids) - visit
    return len(visit) * len(VISIT_VARIANTS) + len(pass_by) * len(PASS_BY_VARIANTS)


def normalize_pack_options(options: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """既定値・型・キー順を固定し、冪等キーへ渡せる形にする。"""

    source = dict(options or {})
    unknown = set(source) - {"include_along_poi", "along_poi_limit"}
    if unknown:
        raise ValueError(f"未定義の pack option です: {sorted(unknown)}")
    include_along = source.get("include_along_poi", True)
    limit = source.get("along_poi_limit", DEFAULT_ALONG_POI_LIMIT)
    if not isinstance(include_along, bool):
        raise ValueError("include_along_poi は bool にしてください")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 0 <= limit <= 100:
        raise ValueError("along_poi_limit は 0〜100 の整数にしてください")
    return {
        "along_poi_limit": limit,
        "include_along_poi": include_along,
    }


def pack_params_hash(
    user_id: int,
    itinerary_version: int,
    options: Mapping[str, Any] | None = None,
) -> str:
    normalized = {
        "itinerary_version": int(itinerary_version),
        "options": normalize_pack_options(options),
        "user_id": int(user_id),
    }
    canonical = json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def ensure_job_transition(current: str, target: str) -> None:
    if target not in _ALLOWED_TRANSITIONS.get(current, frozenset()):
        raise PackStateTransitionError(f"未定義のジョブ状態遷移です: {current} -> {target}")


class PackPlanner:
    """旅程版と geo を読み、進捗分母が動かないジョブを作る。"""

    def __init__(
        self,
        session: AsyncSession,
        settings: Settings | None = None,
        *,
        osrm: OSRMClient | None = None,
    ) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self._osrm = osrm

    async def request_pack(
        self,
        *,
        user_id: int,
        itinerary_version: int,
        options: Mapping[str, Any] | None = None,
    ) -> PackJobData:
        normalized_options = normalize_pack_options(options)
        params_hash = pack_params_hash(user_id, itinerary_version, normalized_options)
        existing = await self._job_by_hash(params_hash)
        if existing is not None:
            return await self._reuse(existing)

        itinerary = await ItineraryRepository(self.session).get_version(
            user_id,
            itinerary_version,
        )
        if itinerary is None:
            raise PackItineraryNotFoundError(
                f"旅程版が見つかりません: v{itinerary_version}"
            )

        try:
            plan = await self._build_plan(itinerary, normalized_options)
        except PackPlanningError:
            raise
        except Exception as exc:  # noqa: BLE001 - geo 境界の失敗を API 用に統一する
            raise PackPlanningError(f"パック生成対象を確定できませんでした: {exc}") from exc

        # 同じユーザーの epoch 採番と同一キーの競合を一つのロックで直列化する。
        user_exists = await self.session.scalar(
            select(User.id).where(User.id == user_id).with_for_update()
        )
        if user_exists is None:
            raise PackPlanningError(f"ユーザーが見つかりません: {user_id}")
        existing = await self._job_by_hash(params_hash)
        if existing is not None:
            return await self._reuse(existing)

        latest_epoch = await self.session.scalar(
            select(PackJob.epoch)
            .where(PackJob.user_id == user_id)
            .order_by(PackJob.created_at.desc(), PackJob.id.desc())
            .limit(1)
        )
        epoch = 0 if latest_epoch is None else (int(latest_epoch) + 1) % 256
        visit_ids = list(plan["visit_spot_ids"])
        along = list(plan["along"])
        total = calculate_total(visit_ids, [value["spot_id"] for value in along])
        row = PackJob(
            id=uuid4(),
            pack_id=uuid4(),
            user_id=user_id,
            itinerary_version=itinerary_version,
            epoch=epoch,
            state="queued",
            params={
                "itinerary_version": itinerary_version,
                "options": normalized_options,
                "plan": plan,
            },
            params_hash=params_hash,
            progress={"done": 0, "total": total, "failed": 0},
        )
        self.session.add(row)
        for spot_id in visit_ids:
            for variant in variants_for_role(PackAssetRole.VISIT):
                self.session.add(
                    PackAsset(
                        pack_id=row.pack_id,
                        spot_id=spot_id,
                        variant=variant.value,
                        role=PackAssetRole.VISIT.value,
                        narration_state="pending",
                        audio_state="pending",
                    )
                )
        for value in along:
            self.session.add(
                PackAsset(
                    pack_id=row.pack_id,
                    spot_id=value["spot_id"],
                    variant=PackAssetVariant.BASE.value,
                    role=PackAssetRole.PASS_BY.value,
                    narration_state="pending",
                    audio_state="pending",
                )
            )
        await self.session.flush()
        return PackJobData.from_row(row)

    async def _build_plan(
        self,
        itinerary: ItineraryVersion,
        options: Mapping[str, Any],
    ) -> dict[str, Any]:
        repository = GeoRepository(self.session)
        if self._osrm is not None:
            service = RouteService(repository, self._osrm, self.settings)
            return await self._build_plan_with_service(itinerary, options, service)
        async with OSRMClient(self.settings) as osrm:
            service = RouteService(repository, osrm, self.settings)
            return await self._build_plan_with_service(itinerary, options, service)

    async def _build_plan_with_service(
        self,
        itinerary: ItineraryVersion,
        options: Mapping[str, Any],
        route_service: RouteService,
    ) -> dict[str, Any]:
        visit_ids = _visit_spot_ids(itinerary)
        legs: list[_PlannedLeg] = []
        for day_index, day in enumerate(itinerary.itinerary.days, start=1):
            endpoints = [
                day.origin.spot_id,
                *(item.spot_id for item in day.items),
                day.destination.spot_id,
            ]
            for leg_index, (source, target) in enumerate(
                zip(endpoints, endpoints[1:], strict=False),
                start=1,
            ):
                route = await route_service.get_or_create(
                    {"spot_id": source},
                    {"spot_id": target},
                )
                legs.append(
                    _PlannedLeg(
                        leg_id=f"d{day_index}-l{leg_index}",
                        day=day_index,
                        index=leg_index,
                        from_spot_id=source,
                        to_spot_id=target,
                        route=route,
                    )
                )

        along: list[dict[str, Any]] = []
        if options["include_along_poi"] and legs:
            along = await self._find_along(legs, visit_ids, int(options["along_poi_limit"]))
        return {
            "visit_spot_ids": visit_ids,
            "along": along,
            "legs": [
                {
                    "leg_id": value.leg_id,
                    "day": value.day,
                    "index": value.index,
                    "from_spot_id": value.from_spot_id,
                    "to_spot_id": value.to_spot_id,
                    "route_id": str(value.route.route_id),
                }
                for value in legs
            ],
        }

    async def _find_along(
        self,
        legs: Sequence[_PlannedLeg],
        visit_ids: Sequence[str],
        limit: int,
    ) -> list[dict[str, Any]]:
        total_distance = sum(max(0, leg.route.distance_m) for leg in legs)
        selected: dict[str, dict[str, Any]] = {}
        traversed = 0
        for leg in legs:
            route_line = _route_line(leg.route.geojson)
            for mode in ("car", "foot"):
                mode_geom = _mode_multiline(leg.route.geojson, mode)
                if not mode_geom["coordinates"]:
                    continue
                values = await find_along_pois(
                    self.session,
                    mode_geom=mode_geom,
                    route_line=route_line,
                    buffer_m=buffer_for_mode(self.settings, mode),
                    exclude_spot_ids=list(visit_ids),
                )
                for value in values:
                    route_position = (
                        (traversed + value.route_position * leg.route.distance_m)
                        / total_distance
                        if total_distance > 0
                        else 0.0
                    )
                    candidate = {
                        "spot_id": value.spot_id,
                        "name_ja": value.name_ja,
                        "distance_m": round(value.distance_m, 1),
                        "route_position": round(route_position, 6),
                        "leg_id": leg.leg_id,
                        "mode": mode,
                    }
                    current = selected.get(value.spot_id)
                    if current is None or (
                        candidate["distance_m"], candidate["route_position"], mode
                    ) < (current["distance_m"], current["route_position"], current["mode"]):
                        selected[value.spot_id] = candidate
            traversed += max(0, leg.route.distance_m)

        nearest = sorted(
            selected.values(),
            key=lambda value: (value["distance_m"], value["spot_id"]),
        )
        kept = nearest[:limit]
        dropped = nearest[limit:]
        if dropped:
            logger.warning(
                "pack_along_pois_truncated",
                extra={
                    "dropped_count": len(dropped),
                    "dropped_spot_ids": [value["spot_id"] for value in dropped],
                    "along_poi_limit": limit,
                },
            )
        return sorted(kept, key=lambda value: (value["route_position"], value["spot_id"]))

    async def _job_by_hash(self, params_hash: str) -> PackJob | None:
        return await self.session.scalar(
            select(PackJob).where(PackJob.params_hash == params_hash)
        )

    async def _reuse(self, row: PackJob) -> PackJobData:
        if row.state not in {"partial", "failed"}:
            return PackJobData.from_row(row)
        ensure_job_transition(row.state, "running")
        assets = (
            await self.session.scalars(
                select(PackAsset).where(PackAsset.pack_id == row.pack_id)
            )
        ).all()
        done = 0
        for asset in assets:
            if asset.narration_state == "failed":
                asset.narration_state = "pending"
                asset.audio_state = "pending"
                asset.text_body = None
                asset.duration_s = None
                asset.bytes = None
                asset.error = None
            elif asset.audio_state in {"failed", "skipped"}:
                asset.audio_state = "pending"
                asset.duration_s = None
                asset.bytes = None
                asset.error = None
            elif asset.narration_state == "ok" and asset.audio_state == "ok":
                done += 1
        total = len(assets)
        row.state = "running"
        row.progress = {
            "done": done,
            "total": total,
            "failed": 0,
            "retry_requested": True,
        }
        row.updated_at = datetime.now(UTC)
        await self.session.flush()
        return PackJobData.from_row(row)


def _visit_spot_ids(itinerary: ItineraryVersion) -> list[str]:
    ordered: dict[str, None] = {}
    for day in itinerary.itinerary.days:
        ordered.setdefault(day.origin.spot_id, None)
        for item in day.items:
            ordered.setdefault(item.spot_id, None)
        ordered.setdefault(day.destination.spot_id, None)
    return list(ordered)


def _route_line(geojson: Mapping[str, Any]) -> dict[str, Any]:
    coordinates: list[list[float]] = []
    for feature in geojson.get("features", []):
        values = feature.get("geometry", {}).get("coordinates", [])
        if not values:
            continue
        if coordinates and coordinates[-1] == values[0]:
            coordinates.extend(values[1:])
        else:
            coordinates.extend(values)
    if len(coordinates) < 2:
        raise PackPlanningError("沿道 POI の計算に必要な経路座標が不足しています")
    return {"type": "LineString", "coordinates": coordinates}


def _mode_multiline(geojson: Mapping[str, Any], mode: str) -> dict[str, Any]:
    coordinates = [
        feature.get("geometry", {}).get("coordinates", [])
        for feature in geojson.get("features", [])
        if feature.get("properties", {}).get("mode") == mode
        and feature.get("geometry", {}).get("type") == "LineString"
    ]
    return {
        "type": "MultiLineString",
        "coordinates": [value for value in coordinates if len(value) >= 2],
    }
