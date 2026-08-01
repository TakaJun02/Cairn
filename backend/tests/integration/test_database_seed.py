"""compose の PostgreSQL に初回 migration とシードを適用する統合テスト。"""

import asyncio
import os
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select

from app.core.config import get_settings
from app.core.db import dispose_engine, session_scope
from app.db_models import (
    Itinerary,
    LoraDownlink,
    Message,
    PackAsset,
    PackJob,
    Profile,
    Thread,
    User,
)
from app.seeds import load_seed_bundle, seed_database, validate_database_against_bundle

alembic_command = pytest.importorskip("alembic.command")
AlembicConfig = pytest.importorskip("alembic.config").Config

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DB_INTEGRATION") != "1",
    reason="RUN_DB_INTEGRATION=1 のとき compose DB に対して実行する",
)

_BACKEND_ROOT = Path(__file__).resolve().parents[2]


def test_init_db_then_seed_is_idempotent() -> None:
    settings = get_settings()
    alembic_config = AlembicConfig(str(_BACKEND_ROOT / "alembic.ini"))
    alembic_config.set_main_option("script_location", str(_BACKEND_ROOT / "alembic"))
    alembic_config.attributes["database_url"] = settings.database_url
    alembic_command.upgrade(alembic_config, "head")

    async def exercise_seed() -> None:
        bundle = load_seed_bundle()
        first = await seed_database(bundle, settings)
        second = await seed_database(bundle, settings)
        validated = await validate_database_against_bundle(bundle, settings)
        assert first == second == validated
        assert validated.as_dict() == {
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
        await dispose_engine()

    asyncio.run(exercise_seed())


def test_reset_user_then_delete_user() -> None:
    from app.cli import _delete_user, _reset_user

    settings = get_settings()
    user_name = "phase1_cli_test_user"
    pack_id = uuid4()
    device_id = f"phase1-cli-device-{uuid4()}"

    async def exercise_user_commands() -> None:
        async with session_scope(settings) as session:
            await session.execute(delete(User).where(User.user_name == user_name))
            user = User(
                user_name=user_name,
                api_token=f"test-{uuid4()}",
                lora_device_id=device_id,
            )
            session.add(user)
            await session.flush()
            thread = Thread(user_id=user.id)
            session.add(thread)
            await session.flush()
            session.add(Message(thread_id=thread.id, seq=1, role="user", content="test"))
            session.add(Profile(user_id=user.id))
            session.add(
                Itinerary(
                    user_id=user.id,
                    version=1,
                    is_current=True,
                    body={"days": []},
                    origin="plan",
                )
            )
            session.add(
                PackJob(
                    pack_id=pack_id,
                    user_id=user.id,
                    itinerary_version=1,
                    epoch=1,
                    state="queued",
                    params={},
                    params_hash=f"test-{uuid4()}",
                )
            )
            session.add(
                PackAsset(
                    pack_id=pack_id,
                    spot_id="spot_001",
                    variant="base",
                    role="visit",
                    narration_state="pending",
                    audio_state="pending",
                )
            )
            session.add(
                LoraDownlink(
                    device_id=device_id,
                    sent_date=date.today(),
                    count=1,
                    last_sent=datetime.now(UTC),
                )
            )
            user_id = user.id

        assert await _reset_user(user_name) == user_id
        async with session_scope(settings) as session:
            assert await session.scalar(select(func.count()).select_from(User)) >= 1
            assert (
                await session.scalar(
                    select(func.count()).select_from(Thread).where(Thread.user_id == user_id)
                )
                == 0
            )
            assert (
                await session.scalar(
                    select(func.count()).select_from(Profile).where(Profile.user_id == user_id)
                )
                == 0
            )
            assert (
                await session.scalar(
                    select(func.count()).select_from(Itinerary).where(Itinerary.user_id == user_id)
                )
                == 0
            )
            assert (
                await session.scalar(
                    select(func.count()).select_from(PackJob).where(PackJob.user_id == user_id)
                )
                == 1
            )

        assert await _delete_user(user_name) == user_id
        async with session_scope(settings) as session:
            assert await session.scalar(select(User.id).where(User.id == user_id)) is None
            assert (
                await session.scalar(
                    select(func.count()).select_from(PackAsset).where(PackAsset.pack_id == pack_id)
                )
                == 0
            )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(LoraDownlink)
                    .where(LoraDownlink.device_id == device_id)
                )
                == 0
            )
        await dispose_engine()

    asyncio.run(exercise_user_commands())
