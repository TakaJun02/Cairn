"""静的シードの検証・変換・冪等投入。"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.config import Settings

DEFAULT_SEED_DIRECTORY = Path(__file__).resolve().parents[1] / "data" / "seeds"

PREFERENCE_TAGS: dict[str | None, tuple[str, ...]] = {
    "nature": (
        "自然",
        "景勝地",
        "天然記念物",
        "森",
        "木",
        "高山植物",
        "湿原",
        "癒やし",
        "神秘的",
        "桜",
        "梅花藻",
    ),
    "mountain": (
        "登山",
        "ハイキング",
        "山小屋",
        "避難小屋",
        "鳥海山",
        "祓川",
        "県境",
        "アウトドア",
    ),
    "water": (
        "滝",
        "湧水",
        "川",
        "湖",
        "火口湖",
        "伏流水",
        "名水",
        "水源",
        "日本の滝百選",
        "池",
        "鳥海湖",
        "水遊び",
        "鮭",
    ),
    "lodging": ("宿泊施設", "ホテル", "キャンプ場", "コテージ", "リゾート"),
    "shrine_temple": (
        "神社",
        "寺",
        "一之宮",
        "仏教",
        "仏像",
        "大仏",
        "神道",
        "礼拝所",
        "パワースポット",
    ),
    "onsen": ("温泉", "日帰り温泉", "露天風呂", "公衆浴場"),
    "park": ("公園", "ピクニック", "展望", "展望台", "牧場"),
    "coast": ("海岸", "海水浴", "夕日"),
    "history": (
        "史跡",
        "歴史",
        "文化",
        "古民家",
        "歴史的建造物",
        "松尾芭蕉",
        "博物館",
        "資料館",
    ),
    "food": ("レストラン", "お土産", "ソフトクリーム"),
    "rest_stop": ("道の駅", "休憩所", "観光案内所", "複合施設"),
    "family": ("レジャー", "体験", "ゴーカート", "動物", "スポーツ施設"),
    None: ("観光", "無人"),
}

PREFERENCE_KEYS: tuple[tuple[str, str, int], ...] = (
    ("nature", "自然・景観", 1),
    ("mountain", "登山・トレッキング", 2),
    ("water", "滝・湧水・湖", 3),
    ("lodging", "宿泊", 4),
    ("shrine_temple", "神社仏閣・信仰", 5),
    ("onsen", "温泉", 6),
    ("park", "公園・展望・散策", 7),
    ("coast", "海・海岸", 8),
    ("history", "歴史・文化", 9),
    ("food", "食事・お土産", 10),
    ("rest_stop", "休憩・立ち寄り", 11),
    ("family", "子ども・体験", 12),
)

WEATHER_FIT_VALUES = {"indoor", "rain_ok", "rain_fair", "rain_poor", "rain_unsafe"}
VISIT_DIFFICULTY_VALUES = {"no_walk", "short_walk", "long_walk", "hike"}


class SeedValidationError(ValueError):
    """シードの欠損や参照不整合を表す。"""


def _build_tag_mapping() -> dict[str, str | None]:
    mapping: dict[str, str | None] = {}
    for preference_key, tags in PREFERENCE_TAGS.items():
        for tag in tags:
            if tag in mapping:
                raise RuntimeError(f"生タグが複数の選好キーに割り当てられています: {tag}")
            mapping[tag] = preference_key
    return mapping


TAG_TO_PREFERENCE = _build_tag_mapping()


@dataclass(frozen=True)
class SeedPaths:
    poi: Path
    facilities: Path
    enrichment: Path
    access_points: Path

    @classmethod
    def from_directory(cls, directory: Path = DEFAULT_SEED_DIRECTORY) -> "SeedPaths":
        return cls(
            poi=directory / "POI.json",
            facilities=directory / "facilities.json",
            enrichment=directory / "enrichment" / "enrichment.json",
            access_points=directory / "access_points.geojson",
        )


@dataclass(frozen=True)
class SeedBundle:
    spots: list[dict[str, Any]]
    access_points: list[dict[str, Any]]
    preference_keys: list[dict[str, Any]]
    tag_vocabulary: list[dict[str, Any]]


@dataclass(frozen=True)
class SeedCounts:
    spots: int
    poi: int
    facilities: int
    access_points: int
    parking: int
    trailhead: int
    preference_keys: int
    tag_vocabulary: int
    unmapped_tags: int

    def as_dict(self) -> dict[str, int]:
        return {
            "spots": self.spots,
            "poi": self.poi,
            "facilities": self.facilities,
            "access_points": self.access_points,
            "parking": self.parking,
            "trailhead": self.trailhead,
            "preference_keys": self.preference_keys,
            "tag_vocabulary": self.tag_vocabulary,
            "unmapped_tags": self.unmapped_tags,
        }


def fold_tags_to_preferences(tags: list[str]) -> list[str]:
    """生タグを重複のない選好キー列へ畳み込む。"""

    folded: list[str] = []
    for tag in tags:
        if tag not in TAG_TO_PREFERENCE:
            raise SeedValidationError(f"未定義の生タグです: {tag}")
        preference_key = TAG_TO_PREFERENCE[tag]
        if preference_key is not None and preference_key not in folded:
            folded.append(preference_key)
    return folded


def build_spot_rows(
    poi_records: list[dict[str, Any]],
    facility_records: list[dict[str, Any]],
    enrichment_records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """旧 JSON 2 本と拡充データを `static.spots` 行へ統合する。"""

    source_records = [(record, "poi", "POI.json") for record in poi_records]
    source_records.extend((record, "facility", "facilities.json") for record in facility_records)
    source_ids = [record.get("spot_id") for record, _, _ in source_records]
    _require_unique_nonempty_ids(source_ids, "POI.json / facilities.json")

    enrichment_ids = [record.get("spot_id") for record in enrichment_records]
    _require_unique_nonempty_ids(enrichment_ids, "enrichment.json")
    source_id_set = set(source_ids)
    enrichment_id_set = set(enrichment_ids)
    if source_id_set != enrichment_id_set:
        missing = sorted(source_id_set - enrichment_id_set)
        extra = sorted(enrichment_id_set - source_id_set)
        raise SeedValidationError(
            f"enrichment の spot_id が一致しません (missing={missing}, extra={extra})"
        )

    enrichments = {record["spot_id"]: record for record in enrichment_records}
    rows: list[dict[str, Any]] = []
    raw_tags: set[str] = set()
    for record, kind, expected_source in source_records:
        spot_id = record["spot_id"]
        enrichment = enrichments[spot_id]
        if enrichment.get("source_file") != expected_source:
            raise SeedValidationError(f"{spot_id}: source_file が {expected_source} ではありません")

        official_name = _require_mapping(record.get("official_name"), spot_id, "official_name")
        name_ja = official_name.get("ja")
        if not isinstance(name_ja, str) or not name_ja:
            raise SeedValidationError(f"{spot_id}: official_name.ja がありません")
        if enrichment.get("name_ja") != name_ja:
            raise SeedValidationError(f"{spot_id}: enrichment.name_ja が元データと一致しません")

        stay_min = enrichment.get("stay_min")
        if not isinstance(stay_min, int) or isinstance(stay_min, bool):
            raise SeedValidationError(f"{spot_id}: stay_min が欠損または整数ではありません")
        if stay_min <= 0 or stay_min > 32767:
            raise SeedValidationError(f"{spot_id}: stay_min が smallint の有効範囲外です")

        weather_fit = enrichment.get("weather_fit")
        if weather_fit not in WEATHER_FIT_VALUES:
            raise SeedValidationError(f"{spot_id}: weather_fit={weather_fit!r} は未定義です")
        visit_difficulty = enrichment.get("visit_difficulty")
        if visit_difficulty not in VISIT_DIFFICULTY_VALUES:
            raise SeedValidationError(
                f"{spot_id}: visit_difficulty={visit_difficulty!r} は未定義です"
            )

        coordinates = _require_mapping(record.get("coordinates"), spot_id, "coordinates")
        latitude = _require_number(coordinates.get("latitude"), spot_id, "latitude")
        longitude = _require_number(coordinates.get("longitude"), spot_id, "longitude")
        _validate_coordinates(longitude, latitude, spot_id)

        aliases = _require_mapping(record.get("aliases"), spot_id, "aliases")
        tags = _require_mapping(record.get("tags"), spot_id, "tags")
        aliases_ja = _require_string_list(aliases.get("ja"), spot_id, "aliases.ja")
        tags_ja = _require_string_list(tags.get("ja"), spot_id, "tags.ja")
        raw_tags.update(tags_ja)

        season_closed_months = enrichment.get("season_closed_months")
        if not isinstance(season_closed_months, list) or any(
            not isinstance(month, int) or isinstance(month, bool) or month < 1 or month > 12
            for month in season_closed_months
        ):
            raise SeedValidationError(f"{spot_id}: season_closed_months が不正です")

        osm_ids = _require_mapping(record.get("osm_ids"), spot_id, "osm_ids")
        enrichment_meta = {
            key: value
            for key, value in enrichment.items()
            if key.endswith(("_basis", "_source")) or key == "confidence"
        }
        rows.append(
            {
                "spot_id": spot_id,
                "kind": kind,
                "category": record.get("category"),
                "official_name": official_name,
                "aliases_ja": aliases_ja,
                "aliases_i18n": aliases,
                "description": record.get("description"),
                "social_proof": record.get("social_proof"),
                "address": record.get("address"),
                "tags_ja": tags_ja,
                "tags_i18n": tags,
                "longitude": longitude,
                "latitude": latitude,
                "osm_place_id": osm_ids.get("place_id"),
                "osm_type": osm_ids.get("osm_type"),
                "osm_id": osm_ids.get("osm_id"),
                "stay_min": stay_min,
                "weather_fit": weather_fit,
                "visit_difficulty": visit_difficulty,
                "open_hours": enrichment.get("open_hours"),
                "season_closed_months": season_closed_months,
                "enrichment_meta": enrichment_meta,
            }
        )

    expected_tags = set(TAG_TO_PREFERENCE)
    if raw_tags != expected_tags:
        missing = sorted(expected_tags - raw_tags)
        extra = sorted(raw_tags - expected_tags)
        raise SeedValidationError(
            f"生タグ語彙が data_model.md §2.3 と一致しません (missing={missing}, extra={extra})"
        )
    return rows


def build_access_point_rows(feature_collection: dict[str, Any]) -> list[dict[str, Any]]:
    """GeoJSON の Point feature を `static.access_points` 行へ変換する。"""

    if feature_collection.get("type") != "FeatureCollection":
        raise SeedValidationError("access_points.geojson は FeatureCollection ではありません")
    features = feature_collection.get("features")
    if not isinstance(features, list):
        raise SeedValidationError("access_points.geojson.features が配列ではありません")

    rows: list[dict[str, Any]] = []
    ids: list[str | None] = []
    for feature in features:
        properties = _require_mapping(feature.get("properties"), "access point", "properties")
        access_point_id = feature.get("id") or properties.get("@id")
        ids.append(access_point_id)
        if access_point_id != properties.get("@id"):
            raise SeedValidationError(
                f"{access_point_id}: feature.id と properties.@id が不一致です"
            )

        if properties.get("amenity") == "parking":
            kind = "parking"
        elif properties.get("highway") == "trailhead":
            kind = "trailhead"
        else:
            raise SeedValidationError(f"{access_point_id}: parking/trailhead を判定できません")

        geometry = _require_mapping(feature.get("geometry"), access_point_id, "geometry")
        if geometry.get("type") != "Point":
            raise SeedValidationError(f"{access_point_id}: geometry は Point ではありません")
        coordinates = geometry.get("coordinates")
        if not isinstance(coordinates, list) or len(coordinates) != 2:
            raise SeedValidationError(
                f"{access_point_id}: coordinates が [lon, lat] ではありません"
            )
        longitude = _require_number(coordinates[0], access_point_id, "longitude")
        latitude = _require_number(coordinates[1], access_point_id, "latitude")
        _validate_coordinates(longitude, latitude, access_point_id)

        capacity_raw = properties.get("capacity")
        try:
            capacity = int(capacity_raw) if capacity_raw is not None else None
        except (TypeError, ValueError) as exc:
            raise SeedValidationError(f"{access_point_id}: capacity が整数ではありません") from exc
        if capacity is not None and (capacity < 0 or capacity > 32767):
            raise SeedValidationError(f"{access_point_id}: capacity が smallint の範囲外です")

        rows.append(
            {
                "id": access_point_id,
                "name_ja": properties.get("name:ja") or properties.get("name"),
                "kind": kind,
                "capacity": capacity,
                "access": properties.get("access"),
                "longitude": longitude,
                "latitude": latitude,
            }
        )
    _require_unique_nonempty_ids(ids, "access_points.geojson")
    return rows


def load_seed_bundle(paths: SeedPaths | None = None) -> SeedBundle:
    resolved_paths = paths or SeedPaths.from_directory()
    poi = _load_json(resolved_paths.poi)
    facilities = _load_json(resolved_paths.facilities)
    enrichment = _load_json(resolved_paths.enrichment)
    access_points = _load_json(resolved_paths.access_points)
    if not all(isinstance(records, list) for records in (poi, facilities, enrichment)):
        raise SeedValidationError("POI / facilities / enrichment は JSON 配列である必要があります")

    spots = build_spot_rows(poi, facilities, enrichment)
    access_rows = build_access_point_rows(access_points)
    preference_rows = [
        {"key": key, "label_ja": label_ja, "sort_order": sort_order}
        for key, label_ja, sort_order in PREFERENCE_KEYS
    ]
    vocabulary_rows = [
        {"tag": tag, "preference_key": preference_key}
        for tag, preference_key in TAG_TO_PREFERENCE.items()
    ]
    bundle = SeedBundle(
        spots=spots,
        access_points=access_rows,
        preference_keys=preference_rows,
        tag_vocabulary=vocabulary_rows,
    )
    _validate_expected_counts(bundle)
    return bundle


def count_bundle(bundle: SeedBundle) -> SeedCounts:
    return SeedCounts(
        spots=len(bundle.spots),
        poi=sum(row["kind"] == "poi" for row in bundle.spots),
        facilities=sum(row["kind"] == "facility" for row in bundle.spots),
        access_points=len(bundle.access_points),
        parking=sum(row["kind"] == "parking" for row in bundle.access_points),
        trailhead=sum(row["kind"] == "trailhead" for row in bundle.access_points),
        preference_keys=len(bundle.preference_keys),
        tag_vocabulary=len(bundle.tag_vocabulary),
        unmapped_tags=sum(row["preference_key"] is None for row in bundle.tag_vocabulary),
    )


async def seed_database(bundle: SeedBundle, settings: Settings | None = None) -> SeedCounts:
    """4 テーブルへ TRUNCATE なしで upsert し、実 DB 件数を返す。"""

    from geoalchemy2.elements import WKTElement
    from sqlalchemy import func
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from app.core.db import session_scope
    from app.db_models import AccessPoint, PreferenceKey, Spot, TagVocabulary

    spot_rows = []
    for row in bundle.spots:
        database_row = dict(row)
        longitude = database_row.pop("longitude")
        latitude = database_row.pop("latitude")
        database_row["geom"] = WKTElement(f"POINT({longitude} {latitude})", srid=4326)
        spot_rows.append(database_row)

    access_rows = []
    for row in bundle.access_points:
        database_row = dict(row)
        longitude = database_row.pop("longitude")
        latitude = database_row.pop("latitude")
        database_row["geom"] = WKTElement(f"POINT({longitude} {latitude})", srid=4326)
        access_rows.append(database_row)

    async with session_scope(settings) as session:
        preference_insert = pg_insert(PreferenceKey).values(bundle.preference_keys)
        await session.execute(
            preference_insert.on_conflict_do_update(
                index_elements=[PreferenceKey.key],
                set_={
                    "label_ja": preference_insert.excluded.label_ja,
                    "sort_order": preference_insert.excluded.sort_order,
                },
            )
        )

        vocabulary_insert = pg_insert(TagVocabulary).values(bundle.tag_vocabulary)
        await session.execute(
            vocabulary_insert.on_conflict_do_update(
                index_elements=[TagVocabulary.tag],
                set_={"preference_key": vocabulary_insert.excluded.preference_key},
            )
        )

        spot_insert = pg_insert(Spot).values(spot_rows)
        spot_update_columns = {
            column.name: getattr(spot_insert.excluded, column.name)
            for column in Spot.__table__.columns
            if column.name not in {"spot_id", "name_ja", "created_at", "updated_at"}
        }
        spot_update_columns["updated_at"] = func.now()
        await session.execute(
            spot_insert.on_conflict_do_update(
                index_elements=[Spot.spot_id],
                set_=spot_update_columns,
            )
        )

        access_insert = pg_insert(AccessPoint).values(access_rows)
        await session.execute(
            access_insert.on_conflict_do_update(
                index_elements=[AccessPoint.id],
                set_={
                    column.name: getattr(access_insert.excluded, column.name)
                    for column in AccessPoint.__table__.columns
                    if column.name != "id"
                },
            )
        )

    return await read_database_counts(settings)


async def read_database_counts(settings: Settings | None = None) -> SeedCounts:
    from sqlalchemy import func, select

    from app.core.db import session_scope
    from app.db_models import AccessPoint, PreferenceKey, Spot, TagVocabulary

    async with session_scope(settings) as session:
        spot_count = await session.scalar(select(func.count()).select_from(Spot))
        poi_count = await session.scalar(
            select(func.count()).select_from(Spot).where(Spot.kind == "poi")
        )
        facility_count = await session.scalar(
            select(func.count()).select_from(Spot).where(Spot.kind == "facility")
        )
        access_count = await session.scalar(select(func.count()).select_from(AccessPoint))
        parking_count = await session.scalar(
            select(func.count()).select_from(AccessPoint).where(AccessPoint.kind == "parking")
        )
        trailhead_count = await session.scalar(
            select(func.count()).select_from(AccessPoint).where(AccessPoint.kind == "trailhead")
        )
        preference_count = await session.scalar(select(func.count()).select_from(PreferenceKey))
        vocabulary_count = await session.scalar(select(func.count()).select_from(TagVocabulary))
        unmapped_count = await session.scalar(
            select(func.count())
            .select_from(TagVocabulary)
            .where(TagVocabulary.preference_key.is_(None))
        )
    return SeedCounts(
        spots=int(spot_count or 0),
        poi=int(poi_count or 0),
        facilities=int(facility_count or 0),
        access_points=int(access_count or 0),
        parking=int(parking_count or 0),
        trailhead=int(trailhead_count or 0),
        preference_keys=int(preference_count or 0),
        tag_vocabulary=int(vocabulary_count or 0),
        unmapped_tags=int(unmapped_count or 0),
    )


async def validate_database_against_bundle(
    bundle: SeedBundle, settings: Settings | None = None
) -> SeedCounts:
    from sqlalchemy import select

    from app.core.db import session_scope
    from app.db_models import AccessPoint, PreferenceKey, Spot, TagVocabulary

    expected = count_bundle(bundle)
    actual = await read_database_counts(settings)
    if actual != expected:
        raise SeedValidationError(
            "DB 件数がシードと一致しません "
            f"(expected={expected.as_dict()}, actual={actual.as_dict()})"
        )

    async with session_scope(settings) as session:
        database_spot_ids = set((await session.scalars(select(Spot.spot_id))).all())
        database_access_ids = set((await session.scalars(select(AccessPoint.id))).all())
        database_preference_keys = set((await session.scalars(select(PreferenceKey.key))).all())
        database_tag_mapping = dict(
            (await session.execute(select(TagVocabulary.tag, TagVocabulary.preference_key))).all()
        )

    expected_spot_ids = {row["spot_id"] for row in bundle.spots}
    expected_access_ids = {row["id"] for row in bundle.access_points}
    expected_preference_keys = {row["key"] for row in bundle.preference_keys}
    expected_tag_mapping = {row["tag"]: row["preference_key"] for row in bundle.tag_vocabulary}
    identity_checks = {
        "spot_id": database_spot_ids == expected_spot_ids,
        "access_point_id": database_access_ids == expected_access_ids,
        "preference_key": database_preference_keys == expected_preference_keys,
        "tag_vocabulary": database_tag_mapping == expected_tag_mapping,
    }
    failed_checks = [name for name, valid in identity_checks.items() if not valid]
    if failed_checks:
        raise SeedValidationError(f"DB の識別子・対応表がシードと一致しません: {failed_checks}")
    return actual


def _validate_expected_counts(bundle: SeedBundle) -> None:
    expected = SeedCounts(43, 30, 13, 33, 32, 1, 12, 80, 2)
    actual = count_bundle(bundle)
    if actual != expected:
        raise SeedValidationError(
            "設計上の期待件数と一致しません "
            f"(expected={expected.as_dict()}, actual={actual.as_dict()})"
        )


def _load_json(path: Path) -> Any:
    try:
        with path.open(encoding="utf-8") as stream:
            return json.load(stream)
    except (OSError, json.JSONDecodeError) as exc:
        raise SeedValidationError(f"{path} を読み込めません: {exc}") from exc


def _require_unique_nonempty_ids(values: list[Any], source: str) -> None:
    if any(not isinstance(value, str) or not value for value in values):
        raise SeedValidationError(f"{source}: spot/access id の欠損があります")
    duplicates = sorted({value for value in values if values.count(value) > 1})
    if duplicates:
        raise SeedValidationError(f"{source}: id が重複しています: {duplicates}")


def _require_mapping(value: Any, item_id: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SeedValidationError(f"{item_id}: {field} がオブジェクトではありません")
    return value


def _require_string_list(value: Any, item_id: Any, field: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise SeedValidationError(f"{item_id}: {field} が文字列配列ではありません")
    return value


def _require_number(value: Any, item_id: Any, field: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise SeedValidationError(f"{item_id}: {field} が数値ではありません")
    return float(value)


def _validate_coordinates(longitude: float, latitude: float, item_id: Any) -> None:
    if not -180 <= longitude <= 180 or not -90 <= latitude <= 90:
        raise SeedValidationError(f"{item_id}: 経緯度が範囲外です")
