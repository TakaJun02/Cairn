"""シード変換は DB やネットワークなしで検証する。"""

import copy
import json

import pytest

from app.seeds import (
    SeedPaths,
    SeedValidationError,
    build_spot_rows,
    count_bundle,
    fold_tags_to_preferences,
    load_seed_bundle,
)


def _load_records(path):
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def test_load_seed_bundle_converts_all_source_rows() -> None:
    bundle = load_seed_bundle()
    counts = count_bundle(bundle)

    assert counts.as_dict() == {
        "spots": 43,
        "poi": 30,
        "facilities": 13,
        "access_points": 33,
        "parking": 32,
        "trailhead": 1,
        "preference_keys": 12,
        "tag_vocabulary": 80,
        "unmapped_tags": 2,
    }

    agariko = next(row for row in bundle.spots if row["spot_id"] == "spot_001")
    assert agariko["kind"] == "poi"
    assert agariko["official_name"]["en"] == "Agariko Daio"
    assert agariko["aliases_ja"] == ["あがりこだいおう"]
    assert agariko["stay_min"] == 20
    assert "stay_min_basis" in agariko["enrichment_meta"]
    assert "md_slug" not in agariko

    travel_village = next(row for row in bundle.spots if row["spot_id"] == "spot_004")
    assert travel_village["kind"] == "facility"
    assert travel_village["tags_i18n"]["zh"]


def test_fold_tags_to_preferences_is_ordered_and_ignores_null_tags() -> None:
    assert fold_tags_to_preferences(["滝", "自然", "観光", "滝", "登山"]) == [
        "water",
        "nature",
        "mountain",
    ]


def test_build_spot_rows_rejects_missing_stay_min() -> None:
    paths = SeedPaths.from_directory()
    poi = _load_records(paths.poi)
    facilities = _load_records(paths.facilities)
    enrichment = copy.deepcopy(_load_records(paths.enrichment))
    enrichment[0]["stay_min"] = None

    with pytest.raises(SeedValidationError, match="stay_min"):
        build_spot_rows(poi, facilities, enrichment)


def test_build_spot_rows_rejects_enrichment_id_mismatch() -> None:
    paths = SeedPaths.from_directory()
    poi = _load_records(paths.poi)
    facilities = _load_records(paths.facilities)
    enrichment = copy.deepcopy(_load_records(paths.enrichment))
    enrichment[0]["spot_id"] = "spot_missing"

    with pytest.raises(SeedValidationError, match="spot_id が一致"):
        build_spot_rows(poi, facilities, enrichment)
