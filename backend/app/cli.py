"""DB 初期化・静的シード・ユーザーデータ管理 CLI。"""

import asyncio
import json
from collections.abc import Coroutine
from pathlib import Path
from typing import Any, TypeVar

import typer
from alembic import command
from alembic.config import Config
from sqlalchemy import delete, select

from app.core.config import get_settings
from app.core.db import session_scope
from app.db_models import (
    Itinerary,
    LoraDownlink,
    PackAsset,
    PackJob,
    Profile,
    Thread,
    User,
)
from app.seeds import (
    SeedValidationError,
    load_seed_bundle,
    seed_database,
    validate_database_against_bundle,
)

cli = typer.Typer(no_args_is_help=True, add_completion=False)
_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_ResultT = TypeVar("_ResultT")


def _emit(payload: dict[str, object]) -> None:
    typer.echo(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def _run_async(operation: Coroutine[Any, Any, _ResultT]) -> _ResultT:
    return asyncio.run(operation)


@cli.command("init-db")
def init_db() -> None:
    """Alembic の最新 revision まで DB を初期化する。"""

    settings = get_settings()
    alembic_config = Config(str(_BACKEND_ROOT / "alembic.ini"))
    alembic_config.set_main_option("script_location", str(_BACKEND_ROOT / "alembic"))
    alembic_config.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))
    alembic_config.attributes["database_url"] = settings.database_url
    command.upgrade(alembic_config, "head")
    _emit({"command": "init-db", "status": "ok", "revision": "head"})


@cli.command("seed")
def seed_command() -> None:
    """検証済みシードを static テーブルへ冪等 upsert する。"""

    try:
        bundle = load_seed_bundle()
        counts = _run_async(seed_database(bundle, get_settings()))
    except (SeedValidationError, OSError) as exc:
        typer.echo(f"seed failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    _emit({"command": "seed", "status": "ok", "counts": counts.as_dict()})


@cli.command("validate-seeds")
def validate_seeds_command() -> None:
    """ファイル間参照と DB の投入件数を検証する。"""

    try:
        bundle = load_seed_bundle()
        counts = _run_async(validate_database_against_bundle(bundle, get_settings()))
    except SeedValidationError as exc:
        typer.echo(f"validate-seeds failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    _emit({"command": "validate-seeds", "status": "ok", "counts": counts.as_dict()})


async def _reset_user(user_name: str) -> int:
    async with session_scope() as session:
        user_id = await session.scalar(select(User.id).where(User.user_name == user_name))
        if user_id is None:
            raise SeedValidationError(f"ユーザーが見つかりません: {user_name}")
        await session.execute(delete(Thread).where(Thread.user_id == user_id))
        await session.execute(delete(Itinerary).where(Itinerary.user_id == user_id))
        await session.execute(delete(Profile).where(Profile.user_id == user_id))
        return int(user_id)


@cli.command("reset-user")
def reset_user_command(user_name: str) -> None:
    """ユーザー行を残し、会話・旅程・プロファイルを削除する。"""

    try:
        user_id = _run_async(_reset_user(user_name))
    except SeedValidationError as exc:
        typer.echo(f"reset-user failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    _emit({"command": "reset-user", "status": "ok", "user_id": user_id})


async def _delete_user(user_name: str) -> int:
    async with session_scope() as session:
        user = (
            await session.execute(
                select(User.id, User.lora_device_id).where(User.user_name == user_name)
            )
        ).one_or_none()
        if user is None:
            raise SeedValidationError(f"ユーザーが見つかりません: {user_name}")
        user_id, lora_device_id = user
        pack_ids = select(PackJob.pack_id).where(PackJob.user_id == user_id)
        await session.execute(delete(PackAsset).where(PackAsset.pack_id.in_(pack_ids)))
        if lora_device_id is not None:
            await session.execute(
                delete(LoraDownlink).where(LoraDownlink.device_id == lora_device_id)
            )
        await session.execute(delete(User).where(User.id == user_id))
        return int(user_id)


@cli.command("delete-user")
def delete_user_command(user_name: str) -> None:
    """ユーザーと、そのユーザーに属する全データを削除する。"""

    try:
        user_id = _run_async(_delete_user(user_name))
    except SeedValidationError as exc:
        typer.echo(f"delete-user failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    _emit({"command": "delete-user", "status": "ok", "user_id": user_id})


if __name__ == "__main__":
    cli()
