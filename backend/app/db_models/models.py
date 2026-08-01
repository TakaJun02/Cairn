"""`Docs/30_design/data_model.md` の全テーブルに対応する ORM モデル。"""

from datetime import date, datetime
from typing import Any
from uuid import UUID

from geoalchemy2 import Geography
from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    ARRAY,
    REAL,
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db_models.base import Base

_NOW = text("now()")
_EMPTY_TEXT_ARRAY = text("'{}'::text[]")
_EMPTY_SMALLINT_ARRAY = text("'{}'::smallint[]")
_EMPTY_OBJECT = text("'{}'::jsonb")
_EMPTY_LIST = text("'[]'::jsonb")


class Spot(Base):
    __tablename__ = "spots"
    __table_args__ = (
        CheckConstraint("kind IN ('poi', 'facility')"),
        CheckConstraint(
            "weather_fit IN ('indoor','rain_ok','rain_fair','rain_poor','rain_unsafe')"
        ),
        CheckConstraint("visit_difficulty IN ('no_walk','short_walk','long_walk','hike')"),
        Index("spots_geom_idx", "geom", postgresql_using="gist"),
        Index("spots_tags_ja_idx", "tags_ja", postgresql_using="gin"),
        Index("spots_kind_idx", "kind"),
        {"schema": "static"},
    )

    spot_id: Mapped[str] = mapped_column(Text, primary_key=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    official_name: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    name_ja: Mapped[str] = mapped_column(
        Text,
        Computed("official_name->>'ja'", persisted=True),
        nullable=False,
    )
    aliases_ja: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=_EMPTY_TEXT_ARRAY
    )
    aliases_i18n: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    description: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    social_proof: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    address: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    tags_ja: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=_EMPTY_TEXT_ARRAY
    )
    tags_i18n: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    geom: Mapped[Any] = mapped_column(Geography("POINT", srid=4326), nullable=False)
    osm_place_id: Mapped[int | None] = mapped_column(BigInteger)
    osm_type: Mapped[str | None] = mapped_column(Text)
    osm_id: Mapped[int | None] = mapped_column(BigInteger)
    stay_min: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    weather_fit: Mapped[str] = mapped_column(Text, nullable=False)
    visit_difficulty: Mapped[str] = mapped_column(Text, nullable=False)
    open_hours: Mapped[Any | None] = mapped_column(JSONB)
    season_closed_months: Mapped[list[int]] = mapped_column(
        ARRAY(SmallInteger), nullable=False, server_default=_EMPTY_SMALLINT_ARRAY
    )
    enrichment_meta: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=_EMPTY_OBJECT
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_NOW
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_NOW
    )


class AccessPoint(Base):
    __tablename__ = "access_points"
    __table_args__ = (
        CheckConstraint("kind IN ('parking','trailhead')"),
        Index("access_points_geom_idx", "geom", postgresql_using="gist"),
        {"schema": "static"},
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    name_ja: Mapped[str | None] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    capacity: Mapped[int | None] = mapped_column(SmallInteger)
    access: Mapped[str | None] = mapped_column(Text)
    geom: Mapped[Any] = mapped_column(Geography("POINT", srid=4326), nullable=False)


class SpotApproach(Base):
    __tablename__ = "spot_approach"
    __table_args__ = ({"schema": "static"},)

    spot_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("static.spots.spot_id", ondelete="CASCADE"),
        primary_key=True,
    )
    direct_by_car: Mapped[bool] = mapped_column(Boolean, nullable=False)
    car_node: Mapped[Any] = mapped_column(Geography("POINT", srid=4326), nullable=False)
    access_point_id: Mapped[str | None] = mapped_column(Text, ForeignKey("static.access_points.id"))
    walk_sec: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    walk_m: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    snap_m: Mapped[int] = mapped_column(Integer, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_NOW
    )


class TravelTime(Base):
    __tablename__ = "travel_times"
    __table_args__ = (
        CheckConstraint("mode IN ('car', 'foot')"),
        {"schema": "static"},
    )

    from_spot_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("static.spots.spot_id", ondelete="CASCADE"),
        primary_key=True,
    )
    to_spot_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("static.spots.spot_id", ondelete="CASCADE"),
        primary_key=True,
    )
    mode: Mapped[str] = mapped_column(Text, primary_key=True)
    duration_sec: Mapped[int] = mapped_column(Integer, nullable=False)
    distance_m: Mapped[int] = mapped_column(Integer, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_NOW
    )


class PreferenceKey(Base):
    __tablename__ = "preference_keys"
    __table_args__ = ({"schema": "static"},)

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    label_ja: Mapped[str] = mapped_column(Text, nullable=False)
    sort_order: Mapped[int] = mapped_column(SmallInteger, nullable=False)


class TagVocabulary(Base):
    __tablename__ = "tag_vocabulary"
    __table_args__ = ({"schema": "static"},)

    tag: Mapped[str] = mapped_column(Text, primary_key=True)
    preference_key: Mapped[str | None] = mapped_column(
        Text, ForeignKey("static.preference_keys.key")
    )


class KnowledgeDocument(Base):
    __tablename__ = "knowledge_documents"
    __table_args__ = (
        Index("knowledge_documents_spot_id_idx", "spot_id"),
        Index("knowledge_documents_category_idx", "category"),
        {"schema": "static"},
    )

    doc_id: Mapped[str] = mapped_column(Text, primary_key=True)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    spot_id: Mapped[str | None] = mapped_column(Text, ForeignKey("static.spots.spot_id"))
    frontmatter: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=_EMPTY_OBJECT
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_NOW
    )


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"
    __table_args__ = ({"schema": "static"},)

    doc_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("static.knowledge_documents.doc_id", ondelete="CASCADE"),
        primary_key=True,
    )
    chunk_index: Mapped[int] = mapped_column(Integer, primary_key=True)
    heading: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    body: Mapped[str] = mapped_column(Text, nullable=False)
    search_text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[Any | None] = mapped_column(Vector(4096))


class User(Base):
    __tablename__ = "users"
    __table_args__ = ({"schema": "app"},)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    api_token: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    lora_device_id: Mapped[str | None] = mapped_column(Text, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_NOW
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_NOW
    )


class Thread(Base):
    __tablename__ = "threads"
    __table_args__ = ({"schema": "app"},)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("app.users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    presented_spot_ids: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=_EMPTY_TEXT_ARRAY
    )
    last_candidates: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=_EMPTY_LIST
    )
    asked_slots: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=_EMPTY_TEXT_ARRAY
    )
    ask_streak: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("0"))
    pending_clarification: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    resolved_ambiguities: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=_EMPTY_LIST
    )
    clarify_streak: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("0")
    )
    pending_constraints: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=_EMPTY_LIST
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_NOW
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_NOW
    )


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        CheckConstraint("role IN ('user', 'assistant')"),
        CheckConstraint("status IN ('complete', 'partial', 'failed')"),
        UniqueConstraint("thread_id", "seq"),
        {"schema": "app"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    thread_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("app.threads.id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'complete'"))
    meta: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=_EMPTY_OBJECT
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_NOW
    )


Index("messages_thread_id_seq_idx", Message.thread_id, Message.seq.desc())


class Profile(Base):
    __tablename__ = "profiles"
    __table_args__ = (
        CheckConstraint("party IN ('family_kids','couple','solo','senior','group')"),
        CheckConstraint("mobility IN ('avoid_walk','short_walk_ok','hike_ok')"),
        CheckConstraint("pace IN ('packed','relaxed')"),
        {"schema": "app"},
    )

    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("app.users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    interests: Mapped[dict[str, float]] = mapped_column(
        JSONB, nullable=False, server_default=_EMPTY_OBJECT
    )
    party: Mapped[str | None] = mapped_column(Text)
    mobility: Mapped[str | None] = mapped_column(Text)
    pace: Mapped[str | None] = mapped_column(Text)
    avoid: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=_EMPTY_TEXT_ARRAY
    )
    liked_spots: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=_EMPTY_TEXT_ARRAY
    )
    rejected_spots: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=_EMPTY_LIST
    )
    notes: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_NOW
    )


class Itinerary(Base):
    __tablename__ = "itineraries"
    __table_args__ = (
        CheckConstraint("origin IN ('plan', 'edit', 'revert')"),
        Index(
            "itineraries_one_current",
            "user_id",
            unique=True,
            postgresql_where=text("is_current"),
        ),
        {"schema": "app"},
    )

    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("app.users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    parent_version: Mapped[int | None] = mapped_column(Integer)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    body: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    constraints: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=_EMPTY_LIST
    )
    origin: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_message_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("app.messages.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_NOW
    )


class Route(Base):
    __tablename__ = "routes"
    __table_args__ = (
        CheckConstraint("mode_summary IN ('car','foot','car+foot')"),
        {"schema": "app"},
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    params_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    mode_summary: Mapped[str] = mapped_column(Text, nullable=False)
    distance_m: Mapped[int] = mapped_column(Integer, nullable=False)
    duration_sec: Mapped[int] = mapped_column(Integer, nullable=False)
    segments: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    geojson: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_NOW
    )


class PackJob(Base):
    __tablename__ = "pack_jobs"
    __table_args__ = (
        CheckConstraint("state IN ('queued','running','ready','partial','failed')"),
        {"schema": "app"},
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    pack_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("app.users.id", ondelete="CASCADE"), nullable=False
    )
    itinerary_version: Mapped[int] = mapped_column(Integer, nullable=False)
    epoch: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    params_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    progress: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=_EMPTY_OBJECT
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_NOW
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_NOW
    )


class PackAsset(Base):
    __tablename__ = "pack_assets"
    __table_args__ = (
        CheckConstraint(
            "variant IN ('base','weather_cloudy','weather_rain','congestion_mid','congestion_high')"
        ),
        CheckConstraint("role IN ('visit','pass_by')"),
        CheckConstraint("narration_state IN ('pending','ok','failed')"),
        CheckConstraint("audio_state IN ('pending','ok','failed','skipped')"),
        {"schema": "app"},
    )

    pack_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    spot_id: Mapped[str] = mapped_column(Text, ForeignKey("static.spots.spot_id"), primary_key=True)
    variant: Mapped[str] = mapped_column(Text, primary_key=True)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    narration_state: Mapped[str] = mapped_column(Text, nullable=False)
    audio_state: Mapped[str] = mapped_column(Text, nullable=False)
    text_body: Mapped[str | None] = mapped_column(Text)
    duration_s: Mapped[float | None] = mapped_column(REAL)
    bytes: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)


class SpotRealtime(Base):
    __tablename__ = "spot_realtime"
    __table_args__ = (
        CheckConstraint("source IN ('sensor','simulated')"),
        {"schema": "app"},
    )

    spot_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("static.spots.spot_id", ondelete="CASCADE"),
        primary_key=True,
    )
    weather: Mapped[int | None] = mapped_column(SmallInteger)
    congestion: Mapped[int | None] = mapped_column(SmallInteger)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_NOW
    )


class LoraDownlink(Base):
    __tablename__ = "lora_downlinks"
    __table_args__ = ({"schema": "app"},)

    device_id: Mapped[str] = mapped_column(Text, primary_key=True)
    sent_date: Mapped[date] = mapped_column(Date, primary_key=True)
    count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    last_sent: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
