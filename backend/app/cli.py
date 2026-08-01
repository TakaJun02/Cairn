"""DB 初期化・静的シード・ユーザーデータ管理 CLI。"""

import asyncio
import json
from collections.abc import Coroutine
from datetime import date, timedelta
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
from app.domains.geo.approach import ApproachBuildError, build_spot_approaches
from app.domains.geo.matrix import MatrixBuildError, build_travel_time_matrix
from app.domains.geo.osrm import Coordinate, OSRMClient, OSRMError, read_osrm_build
from app.domains.geo.repo import GeoRepository
from app.domains.itinerary.repo import ItineraryRepository
from app.domains.itinerary.solver import (
    PlanningDay,
    SolverConfig,
    SolverInput,
    itinerary_spot_ids,
    solve_itinerary,
    validate_hard_constraints,
)
from app.domains.knowledge import (
    KnowledgeIndexError,
    index_knowledge,
    validate_knowledge,
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


async def _check_osrm() -> dict[str, object]:
    settings = get_settings()
    coordinates = [
        Coordinate(lon=140.0244, lat=39.1594),
        Coordinate(lon=140.0354, lat=39.0342),
    ]
    async with OSRMClient(settings) as osrm:
        car, foot = await asyncio.gather(
            osrm.route("car", coordinates),
            osrm.route("foot", coordinates),
        )
    return {
        "build": read_osrm_build(),
        "profiles": {
            "car": {
                "code": "Ok",
                "distance_m": round(car.distance_m),
                "duration_sec": round(car.duration_sec),
            },
            "foot": {
                "code": "Ok",
                "distance_m": round(foot.distance_m),
                "duration_sec": round(foot.duration_sec),
            },
        },
    }


@cli.command("check-osrm")
def check_osrm_command() -> None:
    """car / foot の実経路と BUILD 識別子を確認する。"""

    try:
        result = _run_async(_check_osrm())
    except OSRMError as exc:
        typer.echo(f"check-osrm failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    _emit({"command": "check-osrm", "status": "ok", **result})


async def _build_geo() -> dict[str, object]:
    settings = get_settings()
    async with session_scope(settings) as session:
        async with OSRMClient(settings) as osrm:
            summary = await build_spot_approaches(GeoRepository(session), osrm, settings)
    return {
        "total": summary.total,
        "direct_by_car": summary.direct_by_car,
        "via_access_point": summary.via_access_point,
        "via_road_snap": summary.via_road_snap,
        "median_walk_sec": summary.median_walk_sec,
        "median_walk_min": round(summary.median_walk_sec / 60, 1),
        "max_walk_sec": summary.max_walk_sec,
        "max_walk_min": round(summary.max_walk_sec / 60, 1),
        "walk_over_60_min_count": summary.walk_over_60_min_count,
    }


@cli.command("build-geo")
def build_geo_command() -> None:
    """全 spot の接近情報を OSRM から再構築する。"""

    try:
        counts = _run_async(_build_geo())
    except (ApproachBuildError, OSRMError) as exc:
        typer.echo(f"build-geo failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    _emit({"command": "build-geo", "status": "ok", "counts": counts})


async def _build_travel_times(force: bool) -> dict[str, object]:
    settings = get_settings()
    async with session_scope(settings) as session:
        async with OSRMClient(settings) as osrm:
            result = await build_travel_time_matrix(
                GeoRepository(session),
                osrm,
                settings,
                force=force,
            )
    return {
        "skipped": result.skipped,
        "car": result.car_rows,
        "foot": result.foot_rows,
    }


@cli.command("build-travel-times")
def build_travel_times_command(
    force: bool = typer.Option(
        False,
        "--force",
        help="既存行を検証済み行列で置き換える",
    ),
) -> None:
    """door-to-door の car 行列と近距離 foot 行列を構築する。"""

    try:
        counts = _run_async(_build_travel_times(force))
    except (MatrixBuildError, OSRMError) as exc:
        typer.echo(f"build-travel-times failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    _emit({"command": "build-travel-times", "status": "ok", "counts": counts})


async def _solve_demo(days: int, origin: str) -> dict[str, object]:
    async with session_scope() as session:
        planning = await ItineraryRepository(session).load_planning_data()
    if origin not in planning.spots:
        raise SeedValidationError(f"起点の spot_id が見つかりません: {origin}")
    first_date = date(2026, 8, 10)
    config = SolverConfig()
    solver_input = SolverInput(
        days=tuple(
            PlanningDay(
                date=(first_date + timedelta(days=index)).isoformat(),
                start_min=540,
                end_min=1020,
                origin_spot_id=origin,
                destination_spot_id=origin,
            )
            for index in range(days)
        ),
        spots=planning.spots,
        travel_times=planning.travel_times,
        config=config,
    )
    solved = solve_itinerary(solver_input)
    return {
        "seed": config.seed,
        "iterations": solved.iterations_run,
        "scores": list(solved.scores),
        "solutions": [
            {
                "spot_ids": itinerary_spot_ids(solution),
                "itinerary": solution.model_dump(mode="json"),
                "hard_errors": validate_hard_constraints(
                    solution,
                    planning.spots,
                    planning.travel_times,
                ),
            }
            for solution in solved.solutions
        ],
    }


@cli.command("solve-demo")
def solve_demo_command(
    days: int = typer.Option(1, min=1, max=7, help="旅程の日数"),
    origin: str = typer.Option("spot_004", help="起点・終点にする spot_id"),
) -> None:
    """固定日時・固定シードで A/B/C の時間割を検証表示する。"""

    try:
        result = _run_async(_solve_demo(days, origin))
    except (SeedValidationError, ValueError) as exc:
        typer.echo(f"solve-demo failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    _emit({"command": "solve-demo", "status": "ok", **result})


@cli.command("index-knowledge")
def index_knowledge_command() -> None:
    """日本語 Markdown をチャンク化し、差分だけ埋め込んで投入する。"""

    try:
        summary = _run_async(index_knowledge(get_settings()))
    except (KnowledgeIndexError, OSError) as exc:
        typer.echo(f"index-knowledge failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    _emit({"command": "index-knowledge", **summary.as_dict()})


@cli.command("validate-knowledge")
def validate_knowledge_command() -> None:
    """索引件数と spot 参照を検査し、孤児は報告として出力する。"""

    try:
        summary = _run_async(validate_knowledge(get_settings()))
    except (KnowledgeIndexError, OSError) as exc:
        typer.echo(f"validate-knowledge failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    _emit({"command": "validate-knowledge", **summary.as_dict()})


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
