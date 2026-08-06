"""PostGIS・pgvector と全 static/app テーブルを初期作成する。

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-08-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    statements = [
        "CREATE EXTENSION IF NOT EXISTS postgis",
        "CREATE EXTENSION IF NOT EXISTS vector",
        "CREATE SCHEMA IF NOT EXISTS static",
        "CREATE SCHEMA IF NOT EXISTS app",
        """
        CREATE TABLE static.spots (
          spot_id        text PRIMARY KEY,
          kind           text NOT NULL CHECK (kind IN ('poi', 'facility')),
          category       text NOT NULL,
          official_name  jsonb NOT NULL,
          name_ja        text GENERATED ALWAYS AS (official_name->>'ja') STORED NOT NULL,
          aliases_ja     text[] NOT NULL DEFAULT '{}',
          aliases_i18n   jsonb,
          description    jsonb,
          social_proof   jsonb,
          address        jsonb,
          tags_ja        text[] NOT NULL DEFAULT '{}',
          tags_i18n      jsonb,
          geom           geography(Point, 4326) NOT NULL,
          osm_place_id   bigint,
          osm_type       text,
          osm_id         bigint,
          stay_min             smallint NOT NULL,
          weather_fit          text NOT NULL
                               CHECK (weather_fit IN ('indoor','rain_ok','rain_fair',
                                                      'rain_poor','rain_unsafe')),
          visit_difficulty     text NOT NULL
                               CHECK (visit_difficulty IN ('no_walk','short_walk',
                                                           'long_walk','hike')),
          open_hours           jsonb,
          season_closed_months smallint[] NOT NULL DEFAULT '{}',
          enrichment_meta      jsonb NOT NULL DEFAULT '{}',
          created_at     timestamptz NOT NULL DEFAULT now(),
          updated_at     timestamptz NOT NULL DEFAULT now()
        )
        """,
        "CREATE INDEX ON static.spots USING GIST (geom)",
        "CREATE INDEX ON static.spots USING GIN (tags_ja)",
        "CREATE INDEX ON static.spots (kind)",
        """
        CREATE TABLE static.access_points (
          id       text PRIMARY KEY,
          name_ja  text,
          kind     text NOT NULL CHECK (kind IN ('parking','trailhead')),
          capacity smallint,
          access   text,
          geom     geography(Point,4326) NOT NULL
        )
        """,
        "CREATE INDEX ON static.access_points USING GIST (geom)",
        """
        CREATE TABLE static.preference_keys (
          key        text PRIMARY KEY,
          label_ja   text NOT NULL,
          sort_order smallint NOT NULL
        )
        """,
        """
        CREATE TABLE static.tag_vocabulary (
          tag            text PRIMARY KEY,
          preference_key text REFERENCES static.preference_keys(key)
        )
        """,
        """
        CREATE TABLE static.knowledge_documents (
          doc_id      text PRIMARY KEY,
          category    text NOT NULL,
          title       text NOT NULL,
          spot_id     text REFERENCES static.spots(spot_id),
          frontmatter jsonb NOT NULL DEFAULT '{}',
          body        text NOT NULL,
          updated_at  timestamptz NOT NULL DEFAULT now()
        )
        """,
        """
        CREATE TABLE static.knowledge_chunks (
          doc_id      text NOT NULL REFERENCES static.knowledge_documents(doc_id) ON DELETE CASCADE,
          chunk_index integer NOT NULL,
          heading     text NOT NULL DEFAULT '',
          body        text NOT NULL,
          search_text text NOT NULL,
          embedding   vector(4096),
          PRIMARY KEY (doc_id, chunk_index)
        )
        """,
        "CREATE INDEX ON static.knowledge_documents (spot_id)",
        "CREATE INDEX ON static.knowledge_documents (category)",
        """
        CREATE TABLE static.spot_approach (
          spot_id         text PRIMARY KEY REFERENCES static.spots(spot_id) ON DELETE CASCADE,
          direct_by_car   boolean NOT NULL,
          car_node        geography(Point,4326) NOT NULL,
          access_point_id text REFERENCES static.access_points(id),
          walk_sec        integer NOT NULL DEFAULT 0,
          walk_m          integer NOT NULL DEFAULT 0,
          snap_m          integer NOT NULL,
          computed_at     timestamptz NOT NULL DEFAULT now()
        )
        """,
        """
        CREATE TABLE static.travel_times (
          from_spot_id text NOT NULL REFERENCES static.spots(spot_id) ON DELETE CASCADE,
          to_spot_id   text NOT NULL REFERENCES static.spots(spot_id) ON DELETE CASCADE,
          mode         text NOT NULL CHECK (mode IN ('car', 'foot')),
          duration_sec integer NOT NULL,
          distance_m   integer NOT NULL,
          computed_at  timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (from_spot_id, to_spot_id, mode)
        )
        """,
        """
        CREATE TABLE app.users (
          id             bigserial PRIMARY KEY,
          user_name      text UNIQUE NOT NULL,
          api_token      text UNIQUE NOT NULL,
          lora_device_id text UNIQUE,
          created_at     timestamptz NOT NULL DEFAULT now(),
          updated_at     timestamptz NOT NULL DEFAULT now()
        )
        """,
        """
        CREATE TABLE app.threads (
          id                    bigserial PRIMARY KEY,
          user_id               bigint NOT NULL UNIQUE REFERENCES app.users(id) ON DELETE CASCADE,
          presented_spot_ids    text[] NOT NULL DEFAULT '{}',
          last_candidates       jsonb NOT NULL DEFAULT '[]',
          asked_slots           text[] NOT NULL DEFAULT '{}',
          ask_streak            smallint NOT NULL DEFAULT 0,
          pending_clarification jsonb,
          resolved_ambiguities  jsonb NOT NULL DEFAULT '[]',
          clarify_streak        smallint NOT NULL DEFAULT 0,
          pending_constraints   jsonb NOT NULL DEFAULT '[]',
          created_at            timestamptz NOT NULL DEFAULT now(),
          updated_at            timestamptz NOT NULL DEFAULT now()
        )
        """,
        """
        CREATE TABLE app.messages (
          id         bigserial PRIMARY KEY,
          thread_id  bigint NOT NULL REFERENCES app.threads(id) ON DELETE CASCADE,
          seq        integer NOT NULL,
          role       text NOT NULL CHECK (role IN ('user', 'assistant')),
          content    text NOT NULL,
          status     text NOT NULL DEFAULT 'complete'
                     CHECK (status IN ('complete', 'partial', 'failed')),
          meta       jsonb NOT NULL DEFAULT '{}',
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (thread_id, seq)
        )
        """,
        "CREATE INDEX ON app.messages (thread_id, seq DESC)",
        """
        CREATE TABLE app.profiles (
          user_id        bigint PRIMARY KEY REFERENCES app.users(id) ON DELETE CASCADE,
          interests      jsonb NOT NULL DEFAULT '{}',
          party          text CHECK (party IN ('family_kids','couple','solo','senior','group')),
          mobility       text CHECK (mobility IN ('avoid_walk','short_walk_ok','hike_ok')),
          pace           text CHECK (pace IN ('packed','relaxed')),
          avoid          text[] NOT NULL DEFAULT '{}',
          liked_spots    text[] NOT NULL DEFAULT '{}',
          rejected_spots jsonb NOT NULL DEFAULT '[]',
          notes          text,
          updated_at     timestamptz NOT NULL DEFAULT now()
        )
        """,
        """
        CREATE TABLE app.itineraries (
          user_id        bigint NOT NULL REFERENCES app.users(id) ON DELETE CASCADE,
          version        integer NOT NULL,
          parent_version integer,
          is_current     boolean NOT NULL DEFAULT false,
          body           jsonb NOT NULL,
          constraints    jsonb NOT NULL DEFAULT '[]',
          origin         text NOT NULL CHECK (origin IN ('plan', 'edit', 'revert')),
          created_by_message_id bigint REFERENCES app.messages(id) ON DELETE SET NULL,
          created_at     timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (user_id, version)
        )
        """,
        """
        CREATE UNIQUE INDEX itineraries_one_current
          ON app.itineraries (user_id) WHERE is_current
        """,
        """
        CREATE TABLE app.routes (
          id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          params_hash  text UNIQUE NOT NULL,
          params       jsonb NOT NULL,
          mode_summary text NOT NULL CHECK (mode_summary IN ('car','foot','car+foot')),
          distance_m   integer NOT NULL,
          duration_sec integer NOT NULL,
          segments     jsonb NOT NULL,
          geojson      jsonb NOT NULL,
          created_at   timestamptz NOT NULL DEFAULT now()
        )
        """,
        """
        CREATE TABLE app.pack_jobs (
          id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          pack_id           uuid NOT NULL,
          user_id           bigint NOT NULL REFERENCES app.users(id) ON DELETE CASCADE,
          itinerary_version integer NOT NULL,
          epoch             smallint NOT NULL,
          state             text NOT NULL
                            CHECK (state IN ('queued','running','ready','partial','failed')),
          params            jsonb NOT NULL,
          params_hash       text UNIQUE NOT NULL,
          progress          jsonb NOT NULL DEFAULT '{}',
          created_at        timestamptz NOT NULL DEFAULT now(),
          updated_at        timestamptz NOT NULL DEFAULT now()
        )
        """,
        """
        CREATE TABLE app.pack_assets (
          pack_id         uuid NOT NULL,
          spot_id         text NOT NULL REFERENCES static.spots(spot_id),
          variant         text NOT NULL
                          CHECK (variant IN ('base','weather_cloudy','weather_rain',
                                             'congestion_mid','congestion_high')),
          role            text NOT NULL CHECK (role IN ('visit','pass_by')),
          narration_state text NOT NULL CHECK (narration_state IN ('pending','ok','failed')),
          audio_state     text NOT NULL CHECK (audio_state IN ('pending','ok','failed','skipped')),
          text_body       text,
          duration_s      real,
          bytes           integer,
          error           text,
          PRIMARY KEY (pack_id, spot_id, variant)
        )
        """,
        """
        CREATE TABLE app.spot_realtime (
          spot_id    text PRIMARY KEY REFERENCES static.spots(spot_id) ON DELETE CASCADE,
          weather    smallint,
          congestion smallint,
          source     text NOT NULL CHECK (source IN ('sensor','simulated')),
          updated_at timestamptz NOT NULL DEFAULT now()
        )
        """,
        """
        CREATE TABLE app.lora_downlinks (
          device_id text NOT NULL,
          sent_date date NOT NULL,
          count     integer NOT NULL DEFAULT 0,
          last_sent timestamptz,
          PRIMARY KEY (device_id, sent_date)
        )
        """,
    ]
    for statement in statements:
        op.execute(sa.text(statement))


def downgrade() -> None:
    op.execute(sa.text("DROP SCHEMA IF EXISTS app CASCADE"))
    op.execute(sa.text("DROP SCHEMA IF EXISTS static CASCADE"))
    op.execute(sa.text("DROP EXTENSION IF EXISTS vector"))
    op.execute(sa.text("DROP EXTENSION IF EXISTS postgis CASCADE"))
