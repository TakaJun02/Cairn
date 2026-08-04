"""パックドメインの対象規則、冪等キー、状態機械、manifest の単体テスト。"""

from datetime import UTC, datetime

import pytest

from app.api.schemas.packs import PackAssetRole, PackAssetVariant
from app.domains.packs.manifest import assemble_route_geojson, build_manifest
from app.domains.packs.planner import (
    PackStateTransitionError,
    calculate_total,
    ensure_job_transition,
    normalize_pack_options,
    pack_params_hash,
    variants_for_role,
)


def test_role_mechanically_selects_variants_and_total_deduplicates_spots() -> None:
    assert variants_for_role(PackAssetRole.VISIT) == tuple(PackAssetVariant)
    assert variants_for_role(PackAssetRole.PASS_BY) == (PackAssetVariant.BASE,)
    assert calculate_total(
        ["spot_001", "spot_002", "spot_001"],
        ["spot_002", "spot_003", "spot_003"],
    ) == 11


def test_pack_options_are_defaulted_and_hash_is_order_independent() -> None:
    assert normalize_pack_options() == {
        "along_poi_limit": 20,
        "include_along_poi": True,
    }
    first = pack_params_hash(
        7,
        4,
        {"include_along_poi": True, "along_poi_limit": 12},
    )
    second = pack_params_hash(
        7,
        4,
        {"along_poi_limit": 12, "include_along_poi": True},
    )
    assert first == second
    assert first != pack_params_hash(7, 5, {"along_poi_limit": 12})


def test_job_state_machine_accepts_only_documented_transitions() -> None:
    for current, target in (
        ("queued", "running"),
        ("running", "ready"),
        ("running", "partial"),
        ("running", "failed"),
        ("partial", "running"),
        ("failed", "running"),
    ):
        ensure_job_transition(current, target)
    with pytest.raises(PackStateTransitionError):
        ensure_job_transition("queued", "ready")
    with pytest.raises(PackStateTransitionError):
        ensure_job_transition("ready", "running")


def test_manifest_contains_route_rules_tiles_radii_text_and_missing() -> None:
    route = assemble_route_geojson(
        [
            {
                "leg_id": "d1-l1",
                "geojson": {
                    "type": "FeatureCollection",
                    "features": [
                        {
                            "type": "Feature",
                            "properties": {"mode": "car", "from_idx": 0, "to_idx": 1},
                            "geometry": {
                                "type": "LineString",
                                "coordinates": [[140.0, 39.0], [140.2, 39.2]],
                            },
                        }
                    ],
                },
            }
        ]
    )
    manifest = build_manifest(
        pack_id="00000000-0000-0000-0000-000000000001",
        itinerary_version=4,
        pack_epoch=9,
        state="partial",
        itinerary={
            "days": [
                {
                    "date": "2026-08-10",
                    "start_min": 540,
                    "end_min": 1020,
                    "origin": {"kind": "spot", "spot_id": "spot_001"},
                    "destination": {"kind": "spot", "spot_id": "spot_001"},
                    "items": [
                        {
                            "seq": 1,
                            "spot_id": "spot_001",
                            "arrive_min": 600,
                            "stay_min": 30,
                            "depart_min": 630,
                            "leg_from_prev": {"mode": "car", "min": 60},
                        }
                    ],
                }
            ]
        },
        visit_spot_ids=["spot_001"],
        along=[
            {
                "spot_id": "spot_002",
                "leg_id": "d1-l1",
                "mode": "foot",
                "route_position": 0.5,
                "distance_m": 12.0,
            }
        ],
        spot_details={
            "spot_001": {
                "name_ja": "訪問地",
                "lat": 39.1,
                "lon": 140.1,
                "approach": {"walk_sec": 30, "walk_m": 20},
            },
            "spot_002": {"name_ja": "沿道地", "lat": 39.15, "lon": 140.15},
        },
        assets=[
            {
                "spot_id": "spot_001",
                "variant": "base",
                "narration_state": "ok",
                "audio_state": "ok",
                "text_body": "案内本文",
                "duration_s": 12.5,
                "bytes": 100,
                "error": None,
            },
            {
                "spot_id": "spot_001",
                "variant": "weather_rain",
                "narration_state": "ok",
                "audio_state": "failed",
                "text_body": "雨の字幕",
                "duration_s": None,
                "bytes": None,
                "error": "tts_failed: rate limit",
            },
            {
                "spot_id": "spot_002",
                "variant": "base",
                "narration_state": "failed",
                "audio_state": "skipped",
                "text_body": None,
                "duration_s": None,
                "bytes": None,
                "error": "narration_failed: invalid",
            },
        ],
        route_geojson=route,
        generated_at=datetime(2026, 8, 1, tzinfo=UTC),
    )

    assert route["features"][0]["properties"]["leg_id"] == "d1-l1"
    assert manifest["playback_rules"]["weather"]["2"] == "weather_rain"
    assert manifest["tiles"] == {
        "bbox": [140.0, 39.0, 140.2, 39.2],
        "min_zoom": 10,
        "max_zoom": 16,
    }
    assert manifest["spots"][0]["trigger_radius_m"] == 150
    assert manifest["along"][0]["trigger_radius_m"] == 50
    assert manifest["spots"][0]["assets"]["weather_rain"] == {"text": "雨の字幕"}
    assert manifest["missing"] == [
        {"spot_id": "spot_001", "variant": "weather_rain", "reason": "tts_failed"},
        {"spot_id": "spot_002", "variant": "base", "reason": "narration_failed"},
    ]
    assert manifest["total_bytes"] == 100
