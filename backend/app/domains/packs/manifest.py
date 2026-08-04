"""`manifest.json` と全日ぶんの `route.geojson` を決定的に組み立てる。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.api.schemas.packs import PackAssetRole

PLAYBACK_RULES = {
    "weather": {"1": "weather_cloudy", "2": "weather_rain"},
    "congestion": {"1": "congestion_mid", "2": "congestion_high"},
    "order": ["base", "weather", "congestion"],
}


class ManifestBuildError(RuntimeError):
    """必須の経路・地点情報がなく、オフライン成果物を作れない。"""


def assemble_route_geojson(legs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """各レッグの Feature に順序非依存の `leg_id` と `mode` を付ける。"""

    features: list[dict[str, Any]] = []
    for leg in legs:
        leg_id = str(leg["leg_id"])
        route = leg["geojson"]
        for source in route.get("features", []):
            feature = deepcopy(source)
            properties = dict(feature.get("properties") or {})
            mode = properties.get("mode")
            if mode not in {"car", "foot"}:
                raise ManifestBuildError(f"経路 Feature の mode が不正です: {mode}")
            feature["properties"] = {**properties, "leg_id": leg_id, "mode": mode}
            features.append(feature)
    if not features:
        raise ManifestBuildError("route.geojson に書ける経路 Feature がありません")
    return {"type": "FeatureCollection", "features": features}


def build_manifest(
    *,
    pack_id: str,
    itinerary_version: int,
    pack_epoch: int,
    state: str,
    itinerary: Mapping[str, Any],
    visit_spot_ids: Sequence[str],
    along: Sequence[Mapping[str, Any]],
    spot_details: Mapping[str, Mapping[str, Any]],
    assets: Sequence[Mapping[str, Any]],
    route_geojson: Mapping[str, Any],
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """DB の確定状態だけから manifest を作る純粋関数。"""

    if state not in {"ready", "partial"}:
        raise ManifestBuildError("manifest の state は ready または partial にしてください")
    bbox = route_bbox(route_geojson)
    grouped_assets, missing, total_bytes = _group_assets(pack_id, assets)
    detail_ids = set(spot_details)
    required_ids = set(visit_spot_ids) | {str(value["spot_id"]) for value in along}
    unknown = required_ids - detail_ids
    if unknown:
        raise ManifestBuildError(f"地点情報がありません: {sorted(unknown)}")

    visit_rows = [
        _visit_row(
            spot_id,
            spot_details[spot_id],
            grouped_assets.get(spot_id, {}),
        )
        for spot_id in visit_spot_ids
    ]
    along_rows = [
        _along_row(
            value,
            spot_details[str(value["spot_id"])],
            grouped_assets.get(str(value["spot_id"]), {}),
        )
        for value in along
    ]
    timestamp = generated_at or datetime.now(ZoneInfo("Asia/Tokyo"))
    return {
        "pack_version": 1,
        "pack_id": str(pack_id),
        "pack_epoch": int(pack_epoch),
        "itinerary_version": int(itinerary_version),
        "generated_at": timestamp.isoformat(),
        "lang": "ja",
        "state": state,
        "playback_rules": deepcopy(PLAYBACK_RULES),
        "tiles": {"bbox": bbox, "min_zoom": 10, "max_zoom": 16},
        "days": _manifest_days(itinerary),
        "spots": visit_rows,
        "along": along_rows,
        "missing": missing,
        "total_bytes": total_bytes,
    }


def route_bbox(route_geojson: Mapping[str, Any]) -> list[float]:
    points: list[tuple[float, float]] = []
    for feature in route_geojson.get("features", []):
        geometry = feature.get("geometry") or {}
        if geometry.get("type") != "LineString":
            continue
        for coordinate in geometry.get("coordinates", []):
            if len(coordinate) >= 2:
                points.append((float(coordinate[0]), float(coordinate[1])))
    if not points:
        raise ManifestBuildError("tiles.bbox を計算できる経路座標がありません")
    lons, lats = zip(*points, strict=True)
    return [
        round(min(lons), 6),
        round(min(lats), 6),
        round(max(lons), 6),
        round(max(lats), 6),
    ]


def trigger_radius_m(role: PackAssetRole | str, mode: str | None = None) -> int:
    parsed = PackAssetRole(role)
    if parsed is PackAssetRole.VISIT:
        return 150
    return 50 if mode == "foot" else 300


def _group_assets(
    pack_id: str,
    assets: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]], int]:
    grouped: dict[str, dict[str, Any]] = {}
    missing: list[dict[str, Any]] = []
    total_bytes = 0
    for row in sorted(assets, key=lambda value: (str(value["spot_id"]), str(value["variant"]))):
        spot_id = str(row["spot_id"])
        variant = str(row["variant"])
        narration_state = str(row["narration_state"])
        audio_state = str(row["audio_state"])
        error = str(row.get("error") or "")
        if narration_state != "ok":
            missing.append(
                {
                    "spot_id": spot_id,
                    "variant": variant,
                    "reason": _error_reason(error, "narration_failed"),
                }
            )
            continue

        value: dict[str, Any] = {"text": str(row.get("text_body") or "")}
        if audio_state == "ok":
            byte_count = int(row.get("bytes") or 0)
            value.update(
                {
                    "file": f"audio/{spot_id}.{variant}.ja.mp3",
                    "duration_s": round(float(row.get("duration_s") or 0.0), 3),
                    "bytes": byte_count,
                }
            )
            total_bytes += byte_count
        else:
            missing.append(
                {
                    "spot_id": spot_id,
                    "variant": variant,
                    "reason": _error_reason(error, "tts_failed"),
                }
            )
        grouped.setdefault(spot_id, {})[variant] = value
    return grouped, missing, total_bytes


def _visit_row(
    spot_id: str,
    detail: Mapping[str, Any],
    assets: Mapping[str, Any],
) -> dict[str, Any]:
    value = {
        "spot_id": spot_id,
        "role": PackAssetRole.VISIT.value,
        "name_ja": str(detail["name_ja"]),
        "lat": float(detail["lat"]),
        "lon": float(detail["lon"]),
        "trigger_radius_m": trigger_radius_m(PackAssetRole.VISIT),
        "assets": dict(assets),
    }
    approach = detail.get("approach")
    if approach is not None:
        value["approach"] = {
            "walk_sec": int(approach.get("walk_sec", 0)),
            "walk_m": int(approach.get("walk_m", 0)),
        }
    return value


def _along_row(
    planned: Mapping[str, Any],
    detail: Mapping[str, Any],
    assets: Mapping[str, Any],
) -> dict[str, Any]:
    mode = str(planned.get("mode") or "car")
    return {
        "spot_id": str(planned["spot_id"]),
        "role": PackAssetRole.PASS_BY.value,
        "name_ja": str(detail["name_ja"]),
        "lat": float(detail["lat"]),
        "lon": float(detail["lon"]),
        "leg_id": str(planned["leg_id"]),
        "mode": mode,
        "route_position": float(planned["route_position"]),
        "distance_m": float(planned["distance_m"]),
        "trigger_radius_m": trigger_radius_m(PackAssetRole.PASS_BY, mode),
        "assets": dict(assets),
    }


def _manifest_days(itinerary: Mapping[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for day_index, day in enumerate(itinerary.get("days", []), start=1):
        items: list[dict[str, Any]] = []
        for item_index, item in enumerate(day.get("items", []), start=1):
            leg = item.get("leg_from_prev") or {}
            value = {
                "seq": int(item["seq"]),
                "spot_id": str(item["spot_id"]),
                "arrive_min": int(item["arrive_min"]),
                "stay_min": int(item["stay_min"]),
                "depart_min": int(item["depart_min"]),
                "leg_from_prev": {
                    "mode": str(leg["mode"]),
                    "min": int(leg["min"]),
                    "leg_id": f"d{day_index}-l{item_index}",
                },
                "locked": bool(item.get("locked", False)),
            }
            if item.get("note") is not None:
                value["note"] = item["note"]
            items.append(value)
        result.append(
            {
                "date": str(day["date"]),
                "start_min": int(day["start_min"]),
                "end_min": int(day["end_min"]),
                "origin": deepcopy(day["origin"]),
                "destination": deepcopy(day["destination"]),
                "items": items,
            }
        )
    return result


def _error_reason(error: str, fallback: str) -> str:
    reason = error.partition(":")[0].strip()
    return reason if reason else fallback
