"""パック生成の段2〜5をパイプライン実行するオーケストレーター。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.db import session_scope
from app.core.llm import GenerationError
from app.db_models import PackAsset, PackJob, Route, Spot
from app.domains.itinerary.repo import ItineraryRepository
from app.domains.narration.pack_text import (
    ItineraryPosition,
    PackNarrationRepository,
    PackNarrationRequest,
    PackTextGenerator,
)
from app.domains.packs.manifest import (
    ManifestBuildError,
    assemble_route_geojson,
    build_manifest,
)
from app.domains.packs.planner import PackJobData, ensure_job_transition
from app.domains.packs.storage import PackStorage
from app.domains.voice import GTTSTTS, TTSPort

NARRATE_CONCURRENCY = 8
SPEAK_CONCURRENCY = 3
TTS_CONSECUTIVE_FAILURE_LIMIT = 10

logger = logging.getLogger("app.packs.runner")

TextGeneratorFactory = Callable[[AsyncSession], PackTextGenerator]


@dataclass(frozen=True, slots=True)
class _AssetWork:
    spot_id: str
    variant: str
    role: str
    narration_state: str
    audio_state: str
    text_body: str | None
    itinerary_position: ItineraryPosition | None
    month: int | None


@dataclass(frozen=True, slots=True)
class _WorkBundle:
    itinerary: dict[str, Any]
    assets: list[_AssetWork]
    pack_spot_ids: tuple[str, ...]


class _ProgressTracker:
    def __init__(
        self,
        settings: Settings,
        job_id: UUID,
        *,
        done: int,
        total: int,
        failed: int,
    ) -> None:
        self.settings = settings
        self.job_id = job_id
        self.done = done
        self.total = total
        self.failed = failed
        self._lock = asyncio.Lock()

    async def persist(self) -> None:
        async with self._lock:
            await self._write()

    async def complete(self, *, failed: bool) -> None:
        async with self._lock:
            self.done += 1
            if failed:
                self.failed += 1
            await self._write()

    async def _write(self) -> None:
        async with session_scope(self.settings) as session:
            await session.execute(
                update(PackJob)
                .where(PackJob.id == self.job_id)
                .values(
                    progress={
                        "done": min(self.done, self.total),
                        "total": self.total,
                        "failed": min(self.failed, self.total),
                    },
                    updated_at=datetime.now(UTC),
                )
            )


class _TTSFailureGuard:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.consecutive = 0
        self.stopped = False
        self._lock = asyncio.Lock()

    async def can_run(self) -> bool:
        async with self._lock:
            return not self.stopped

    async def success(self) -> None:
        async with self._lock:
            if not self.stopped:
                self.consecutive = 0

    async def failure(self) -> bool:
        async with self._lock:
            self.consecutive += 1
            if self.consecutive >= self.limit:
                self.stopped = True
            return self.stopped


class PackRunner:
    """原稿1件の完了を待って即 TTS へ渡し、全件待ちの壁を作らない。"""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        storage: PackStorage | None = None,
        text_generator_factory: TextGeneratorFactory | None = None,
        tts: TTSPort | None = None,
        narrate_concurrency: int = NARRATE_CONCURRENCY,
        speak_concurrency: int = SPEAK_CONCURRENCY,
        tts_failure_limit: int = TTS_CONSECUTIVE_FAILURE_LIMIT,
    ) -> None:
        self.settings = settings or get_settings()
        self.storage = storage or PackStorage(self.settings.packs_root)
        self.text_generator_factory = text_generator_factory or (
            lambda session: PackTextGenerator(PackNarrationRepository(session))
        )
        self.tts = tts or GTTSTTS()
        self.narrate_semaphore = asyncio.Semaphore(narrate_concurrency)
        self.speak_semaphore = asyncio.Semaphore(speak_concurrency)
        self.tts_failure_limit = tts_failure_limit

    async def run(self, job_id: UUID) -> None:
        job = await self._load_job(job_id)
        if job is None or job.state != "running":
            return
        self.storage.prepare(job.pack_id)
        try:
            route_geojson = await self._route_geojson(job)
            self.storage.write_route(job.pack_id, route_geojson)
        except Exception as exc:  # noqa: BLE001 - 経路失敗はジョブ全体へ正規化
            await self.fail_job(job_id, "route_failed", str(exc))
            return

        total = int(job.progress.get("total", 0))
        if total == 0:
            await self.fail_job(job_id, "target_empty", "生成対象の地点がありません")
            return

        try:
            bundle = await self._load_work(job)
            bundle = await self._reset_failed_assets(job, bundle)
            initial_done = sum(
                value.narration_state == "ok"
                and value.audio_state == "ok"
                and self.storage.audio_exists(job.pack_id, value.spot_id, value.variant)
                for value in bundle.assets
            )
            tracker = _ProgressTracker(
                self.settings,
                job.job_id,
                done=initial_done,
                total=total,
                failed=0,
            )
            await tracker.persist()
            fatal_generation = asyncio.Event()
            fatal_details: list[str] = []
            tts_guard = _TTSFailureGuard(self.tts_failure_limit)
            tasks = [
                asyncio.create_task(
                    self._run_asset(
                        job,
                        work,
                        bundle.pack_spot_ids,
                        tracker,
                        tts_guard,
                        fatal_generation,
                        fatal_details,
                    )
                )
                for work in bundle.assets
                if not (
                    work.narration_state == "ok"
                    and work.audio_state == "ok"
                    and self.storage.audio_exists(job.pack_id, work.spot_id, work.variant)
                )
            ]
            if tasks:
                await asyncio.gather(*tasks)
            if fatal_generation.is_set():
                await self.fail_job(
                    job_id,
                    "generation_unavailable",
                    fatal_details[0] if fatal_details else "生成 API が応答しませんでした",
                )
                return
            await self._finalize(job, bundle.itinerary, route_geojson, tracker)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 作業境界で failed に閉じ込める
            logger.exception("pack_run_failed", extra={"job_id": str(job_id)})
            await self.fail_job(job_id, "pack_generation_failed", str(exc))

    async def _run_asset(
        self,
        job: PackJobData,
        work: _AssetWork,
        pack_spot_ids: tuple[str, ...],
        tracker: _ProgressTracker,
        tts_guard: _TTSFailureGuard,
        fatal_generation: asyncio.Event,
        fatal_details: list[str],
    ) -> None:
        narration_state = work.narration_state
        text_body = work.text_body
        if narration_state != "ok":
            async with self.narrate_semaphore:
                if fatal_generation.is_set():
                    return
                try:
                    async with session_scope(self.settings) as session:
                        result = await self.text_generator_factory(session).generate(
                            PackNarrationRequest(
                                spot_id=work.spot_id,
                                role=work.role,
                                variant=work.variant,
                                pack_spot_ids=pack_spot_ids,
                                itinerary_position=work.itinerary_position,
                                month=work.month,
                            )
                        )
                        asset = await _asset_row(session, job.pack_id, work)
                        asset.narration_state = result.narration_state
                        asset.audio_state = result.audio_state
                        asset.text_body = result.text
                        asset.error = (
                            f"narration_failed: {result.error}"
                            if result.narration_state == "failed"
                            else None
                        )
                        narration_state = result.narration_state
                        text_body = result.text
                except GenerationError as exc:
                    fatal_details.append(str(exc))
                    fatal_generation.set()
                    return
                except Exception as exc:  # noqa: BLE001 - 1原稿の失敗へ閉じ込める
                    await self._mark_narration_failed(job.pack_id, work, str(exc))
                    narration_state = "failed"
                    text_body = None

        if narration_state != "ok" or not text_body:
            await tracker.complete(failed=True)
            return

        async with self.speak_semaphore:
            if not await tts_guard.can_run():
                await self._mark_audio_failed(
                    job.pack_id,
                    work,
                    "tts_aborted: 連続失敗の上限に達したため合成を打ち切りました",
                )
                await tracker.complete(failed=True)
                return
            try:
                audio = await self.tts.synthesize(text_body, "ja")
                self.storage.write_audio(
                    job.pack_id,
                    work.spot_id,
                    work.variant,
                    audio.bytes,
                )
                async with session_scope(self.settings) as session:
                    asset = await _asset_row(session, job.pack_id, work)
                    asset.audio_state = "ok"
                    asset.duration_s = audio.duration_s
                    asset.bytes = len(audio.bytes)
                    asset.error = None
                await tts_guard.success()
                await tracker.complete(failed=False)
            except Exception as exc:  # noqa: BLE001 - 1音声の失敗へ閉じ込める
                stopped = await tts_guard.failure()
                await self._mark_audio_failed(
                    job.pack_id,
                    work,
                    f"tts_failed: {type(exc).__name__}: {exc}",
                )
                if stopped:
                    logger.warning(
                        "pack_tts_aborted",
                        extra={
                            "job_id": str(job.job_id),
                            "pack_id": str(job.pack_id),
                            "consecutive_failures": self.tts_failure_limit,
                        },
                    )
                await tracker.complete(failed=True)

    async def _route_geojson(self, job: PackJobData) -> dict[str, Any]:
        planned_legs = job.params.get("plan", {}).get("legs", [])
        if not planned_legs:
            raise ManifestBuildError("旅程に経路レッグがありません")
        values: list[dict[str, Any]] = []
        async with session_scope(self.settings) as session:
            for planned in planned_legs:
                route = await session.scalar(
                    select(Route).where(Route.id == UUID(str(planned["route_id"])))
                )
                if route is None:
                    raise ManifestBuildError(
                        f"経路が見つかりません: {planned['route_id']}"
                    )
                values.append({"leg_id": planned["leg_id"], "geojson": route.geojson})
        return assemble_route_geojson(values)

    async def _load_work(self, job: PackJobData) -> _WorkBundle:
        async with session_scope(self.settings) as session:
            version = await ItineraryRepository(session).get_version(
                job.user_id,
                job.itinerary_version,
            )
            if version is None:
                raise RuntimeError(f"旅程版が見つかりません: v{job.itinerary_version}")
            rows = (
                await session.scalars(
                    select(PackAsset)
                    .where(PackAsset.pack_id == job.pack_id)
                    .order_by(PackAsset.spot_id, PackAsset.variant)
                )
            ).all()
            spot_ids = tuple(dict.fromkeys(row.spot_id for row in rows))
            names = dict(
                (
                    await session.execute(
                        select(Spot.spot_id, Spot.name_ja).where(Spot.spot_id.in_(spot_ids))
                    )
                ).all()
            )
            positions, months = _itinerary_context(version.itinerary.model_dump(mode="json"), names)
            first_month = next(iter(months.values()), None)
            assets = [
                _AssetWork(
                    spot_id=row.spot_id,
                    variant=row.variant,
                    role=row.role,
                    narration_state=row.narration_state,
                    audio_state=row.audio_state,
                    text_body=row.text_body,
                    itinerary_position=positions.get(row.spot_id),
                    month=months.get(row.spot_id, first_month),
                )
                for row in rows
            ]
            return _WorkBundle(
                itinerary=version.itinerary.model_dump(mode="json", by_alias=True),
                assets=assets,
                pack_spot_ids=spot_ids,
            )

    async def _reset_failed_assets(
        self,
        job: PackJobData,
        bundle: _WorkBundle,
    ) -> _WorkBundle:
        changed = False
        normalized: list[_AssetWork] = []
        async with session_scope(self.settings) as session:
            for work in bundle.assets:
                narration_state = work.narration_state
                audio_state = work.audio_state
                text_body = work.text_body
                asset = await _asset_row(session, job.pack_id, work)
                if narration_state == "failed":
                    narration_state = "pending"
                    audio_state = "pending"
                    text_body = None
                    asset.narration_state = narration_state
                    asset.audio_state = audio_state
                    asset.text_body = None
                    asset.duration_s = None
                    asset.bytes = None
                    asset.error = None
                    changed = True
                elif narration_state == "ok" and audio_state in {"failed", "skipped"}:
                    audio_state = "pending"
                    asset.audio_state = audio_state
                    asset.duration_s = None
                    asset.bytes = None
                    asset.error = None
                    changed = True
                elif (
                    narration_state == "ok"
                    and audio_state == "ok"
                    and not self.storage.audio_exists(job.pack_id, work.spot_id, work.variant)
                ):
                    audio_state = "pending"
                    asset.audio_state = audio_state
                    asset.duration_s = None
                    asset.bytes = None
                    asset.error = None
                    changed = True
                normalized.append(
                    _AssetWork(
                        spot_id=work.spot_id,
                        variant=work.variant,
                        role=work.role,
                        narration_state=narration_state,
                        audio_state=audio_state,
                        text_body=text_body,
                        itinerary_position=work.itinerary_position,
                        month=work.month,
                    )
                )
        if changed:
            logger.info("pack_failed_assets_reset", extra={"pack_id": str(job.pack_id)})
        return _WorkBundle(bundle.itinerary, normalized, bundle.pack_spot_ids)

    async def _finalize(
        self,
        job: PackJobData,
        itinerary: Mapping[str, Any],
        route_geojson: Mapping[str, Any],
        tracker: _ProgressTracker,
    ) -> None:
        async with session_scope(self.settings) as session:
            asset_rows = (
                await session.scalars(
                    select(PackAsset)
                    .where(PackAsset.pack_id == job.pack_id)
                    .order_by(PackAsset.spot_id, PackAsset.variant)
                )
            ).all()
            asset_values = [
                {
                    "spot_id": row.spot_id,
                    "variant": row.variant,
                    "role": row.role,
                    "narration_state": row.narration_state,
                    "audio_state": row.audio_state,
                    "text_body": row.text_body,
                    "duration_s": row.duration_s,
                    "bytes": row.bytes,
                    "error": row.error,
                }
                for row in asset_rows
            ]
            failed = sum(
                row.narration_state != "ok" or row.audio_state != "ok"
                for row in asset_rows
            )
            spot_ids = list(dict.fromkeys(row.spot_id for row in asset_rows))
            details = await _spot_details(session, spot_ids)
        state = "partial" if failed else "ready"
        plan = job.params.get("plan", {})
        manifest = build_manifest(
            pack_id=str(job.pack_id),
            itinerary_version=job.itinerary_version,
            pack_epoch=job.epoch,
            state=state,
            itinerary=itinerary,
            visit_spot_ids=plan.get("visit_spot_ids", []),
            along=plan.get("along", []),
            spot_details=details,
            assets=asset_values,
            route_geojson=route_geojson,
        )
        self.storage.write_manifest(job.pack_id, manifest)
        self.storage.publish(job.pack_id)
        async with session_scope(self.settings) as session:
            row = await session.scalar(select(PackJob).where(PackJob.id == job.job_id))
            if row is None:
                raise RuntimeError(f"ジョブが見つかりません: {job.job_id}")
            ensure_job_transition(row.state, state)
            row.state = state
            row.progress = {"done": tracker.total, "total": tracker.total, "failed": failed}
            row.updated_at = datetime.now(UTC)

    async def _mark_narration_failed(
        self,
        pack_id: UUID,
        work: _AssetWork,
        detail: str,
    ) -> None:
        async with session_scope(self.settings) as session:
            asset = await _asset_row(session, pack_id, work)
            asset.narration_state = "failed"
            asset.audio_state = "skipped"
            asset.text_body = None
            asset.error = f"narration_failed: {detail}"

    async def _mark_audio_failed(
        self,
        pack_id: UUID,
        work: _AssetWork,
        error: str,
    ) -> None:
        async with session_scope(self.settings) as session:
            asset = await _asset_row(session, pack_id, work)
            asset.audio_state = "failed"
            asset.duration_s = None
            asset.bytes = None
            asset.error = error

    async def _load_job(self, job_id: UUID) -> PackJobData | None:
        async with session_scope(self.settings) as session:
            row = await session.scalar(select(PackJob).where(PackJob.id == job_id))
            return PackJobData.from_row(row) if row is not None else None

    async def fail_job(self, job_id: UUID, reason: str, detail: str) -> None:
        async with session_scope(self.settings) as session:
            row = await session.scalar(select(PackJob).where(PackJob.id == job_id))
            if row is None:
                return
            if row.state != "failed":
                if row.state != "running":
                    return
                ensure_job_transition(row.state, "failed")
                row.state = "failed"
            progress = dict(row.progress)
            progress.pop("retry_requested", None)
            progress["job_failure"] = {"reason": reason, "detail": detail}
            row.progress = progress
            row.updated_at = datetime.now(UTC)


async def _asset_row(
    session: AsyncSession,
    pack_id: UUID,
    work: _AssetWork,
) -> PackAsset:
    row = await session.scalar(
        select(PackAsset).where(
            PackAsset.pack_id == pack_id,
            PackAsset.spot_id == work.spot_id,
            PackAsset.variant == work.variant,
        )
    )
    if row is None:
        raise RuntimeError(
            f"pack_asset が見つかりません: {pack_id}/{work.spot_id}/{work.variant}"
        )
    return row


async def _spot_details(
    session: AsyncSession,
    spot_ids: Sequence[str],
) -> dict[str, dict[str, Any]]:
    result = await session.execute(
        text(
            """
            SELECT s.spot_id,
                   s.name_ja,
                   ST_X(s.geom::geometry) AS lon,
                   ST_Y(s.geom::geometry) AS lat,
                   a.walk_sec,
                   a.walk_m
              FROM static.spots s
              LEFT JOIN static.spot_approach a ON a.spot_id = s.spot_id
             WHERE s.spot_id = ANY(CAST(:spot_ids AS text[]))
            """
        ),
        {"spot_ids": list(spot_ids)},
    )
    return {
        str(row["spot_id"]): {
            "name_ja": str(row["name_ja"]),
            "lat": float(row["lat"]),
            "lon": float(row["lon"]),
            "approach": (
                {"walk_sec": int(row["walk_sec"]), "walk_m": int(row["walk_m"])}
                if row["walk_sec"] is not None
                else None
            ),
        }
        for row in result.mappings()
    }


def _itinerary_context(
    itinerary: Mapping[str, Any],
    spot_names: Mapping[str, str],
) -> tuple[dict[str, ItineraryPosition], dict[str, int]]:
    positions: dict[str, ItineraryPosition] = {}
    months: dict[str, int] = {}
    for day_index, day in enumerate(itinerary.get("days", []), start=1):
        try:
            month = date.fromisoformat(str(day["date"])).month
        except ValueError:
            month = None
        origin_id = str(day["origin"]["spot_id"])
        if month is not None:
            months.setdefault(origin_id, month)
        items = list(day.get("items", []))
        for index, item in enumerate(items, start=1):
            spot_id = str(item["spot_id"])
            next_id = (
                str(items[index]["spot_id"])
                if index < len(items)
                else str(day["destination"]["spot_id"])
            )
            positions.setdefault(
                spot_id,
                ItineraryPosition(
                    day=day_index,
                    position=index,
                    next_spot_name=spot_names.get(next_id, next_id),
                ),
            )
            if month is not None:
                months.setdefault(spot_id, month)
        destination_id = str(day["destination"]["spot_id"])
        if month is not None:
            months.setdefault(destination_id, month)
    return positions, months
