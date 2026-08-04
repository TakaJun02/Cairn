"""DB に保存したシナリオを lifespan タスクから決定的に進める。"""

from __future__ import annotations

import asyncio
import json
import logging
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.db import session_scope

if TYPE_CHECKING:
    from app.db_models import RealtimeSimulatorState

logger = logging.getLogger("app.realtime.simulator")
_SINGLETON_ID = 1


class SimulatorError(RuntimeError):
    """シナリオまたは進行状態が不正。"""


class ScenarioEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    at_min: int = Field(ge=0)
    spot_id: str = Field(min_length=1)
    weather: int | None = None
    congestion: int | None = None

    @field_validator("weather", "congestion")
    @classmethod
    def validate_code(cls, value: int | None) -> int | None:
        if value is not None and (isinstance(value, bool) or value not in {0, 1, 2}):
            raise ValueError("0, 1, 2, null のいずれかにしてください")
        return value


class RealtimeScenario(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1)
    base_time: datetime
    events: tuple[ScenarioEvent, ...]

    @field_validator("base_time")
    @classmethod
    def validate_base_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("base_time は timezone-aware にしてください")
        return value

    @field_validator("events")
    @classmethod
    def order_events(cls, events: tuple[ScenarioEvent, ...]) -> tuple[ScenarioEvent, ...]:
        return tuple(sorted(events, key=lambda event: event.at_min))


class SimulatorStateData(BaseModel):
    model_config = ConfigDict(frozen=True)

    loaded: bool
    name: str | None
    running: bool
    speed: float
    elapsed_min: float
    next_event_index: int
    event_count: int
    virtual_time: datetime | None


def load_scenario_file(path: Path) -> RealtimeScenario:
    """JSON ファイルを読み、時刻・code 値まで検証する。"""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return RealtimeScenario.model_validate(payload)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise SimulatorError(f"シナリオを読み込めません: {path}: {exc}") from exc


def elapsed_minutes(
    *,
    carried_min: float,
    started_at: datetime | None,
    speed: float,
    now: datetime,
) -> float:
    """実時間倍率からシナリオ上の経過分を求める純関数。"""

    if carried_min < 0 or not math.isfinite(carried_min):
        raise ValueError("carried_min は有限な 0 以上の値にしてください")
    if speed <= 0 or not math.isfinite(speed):
        raise ValueError("speed は有限な正数にしてください")
    current = _aware(now, "now")
    if started_at is None:
        return carried_min
    started = _aware(started_at, "started_at")
    real_seconds = max(0.0, (current - started).total_seconds())
    return carried_min + real_seconds * speed / 60.0


def due_events(
    scenario: RealtimeScenario,
    *,
    next_event_index: int,
    elapsed_min: float,
) -> tuple[tuple[ScenarioEvent, ...], int]:
    """指定時刻までに未適用の event と次 index を返す純関数。"""

    if not 0 <= next_event_index <= len(scenario.events):
        raise ValueError("next_event_index が event 数の範囲外です")
    index = next_event_index
    while index < len(scenario.events) and scenario.events[index].at_min <= elapsed_min:
        index += 1
    return scenario.events[next_event_index:index], index


class SimulatorService:
    """CLI/API/coordinator が共有する DB 完結の操作。"""

    def __init__(self, session: AsyncSession) -> None:
        from app.domains.realtime.store import RealtimeStore

        self.session = session
        self.store = RealtimeStore(session)

    async def load(
        self,
        scenario: RealtimeScenario,
        *,
        now: datetime | None = None,
    ) -> SimulatorStateData:
        from app.db_models import RealtimeSimulatorState, Spot

        spot_ids = {event.spot_id for event in scenario.events}
        known = set(
            (
                await self.session.scalars(
                    select(Spot.spot_id).where(Spot.spot_id.in_(spot_ids))
                )
            ).all()
        )
        unknown = sorted(spot_ids - known)
        if unknown:
            raise SimulatorError(f"存在しない spot_id です: {unknown}")
        current = _aware(now or datetime.now(UTC), "now")
        row = await self._locked_state()
        if row is None:
            row = RealtimeSimulatorState(singleton_id=_SINGLETON_ID)
            self.session.add(row)
        row.scenario = scenario.model_dump(mode="json")
        row.running = False
        row.speed = 1.0
        row.started_at = None
        row.elapsed_min = 0.0
        row.next_event_index = 0
        row.updated_at = current
        await self.session.flush()
        return _state_data(row, now=current)

    async def start(
        self,
        *,
        speed: float = 1.0,
        now: datetime | None = None,
    ) -> SimulatorStateData:
        if speed <= 0 or not math.isfinite(speed):
            raise SimulatorError("speed は有限な正数にしてください")
        current = _aware(now or datetime.now(UTC), "now")
        row = await self._require_state()
        if row.running:
            await self._apply_due(row, current)
            row.elapsed_min = elapsed_minutes(
                carried_min=row.elapsed_min,
                started_at=row.started_at,
                speed=row.speed,
                now=current,
            )
        row.running = True
        row.speed = speed
        row.started_at = current
        row.updated_at = current
        await self.session.flush()
        return _state_data(row, now=current)

    async def stop(self, *, now: datetime | None = None) -> SimulatorStateData:
        current = _aware(now or datetime.now(UTC), "now")
        row = await self._require_state()
        if row.running:
            await self._apply_due(row, current)
            row.elapsed_min = elapsed_minutes(
                carried_min=row.elapsed_min,
                started_at=row.started_at,
                speed=row.speed,
                now=current,
            )
        row.running = False
        row.started_at = None
        row.updated_at = current
        await self.session.flush()
        return _state_data(row, now=current)

    async def tick(self, *, now: datetime | None = None) -> SimulatorStateData:
        current = _aware(now or datetime.now(UTC), "now")
        row = await self._locked_state()
        if row is None or row.scenario is None:
            return _empty_state()
        if row.running:
            await self._apply_due(row, current)
            await self.session.flush()
        return _state_data(row, now=current)

    async def status(self, *, now: datetime | None = None) -> SimulatorStateData:
        current = _aware(now or datetime.now(UTC), "now")
        row = await self._locked_state()
        if row is None or row.scenario is None:
            return _empty_state()
        return _state_data(row, now=current)

    async def _apply_due(self, row: RealtimeSimulatorState, now: datetime) -> None:
        if row.scenario is None:
            return
        scenario = RealtimeScenario.model_validate(row.scenario)
        elapsed = elapsed_minutes(
            carried_min=row.elapsed_min,
            started_at=row.started_at,
            speed=row.speed,
            now=now,
        )
        events, next_index = due_events(
            scenario,
            next_event_index=row.next_event_index,
            elapsed_min=elapsed,
        )
        if events:
            await self.store.set_events(
                (
                    (event.spot_id, event.weather, event.congestion)
                    for event in events
                ),
                updated_at=now,
            )
            row.next_event_index = next_index
            row.updated_at = now

    async def _require_state(self) -> RealtimeSimulatorState:
        row = await self._locked_state()
        if row is None or row.scenario is None:
            raise SimulatorError("先にシナリオを load してください")
        return row

    async def _locked_state(self) -> RealtimeSimulatorState | None:
        from app.db_models import RealtimeSimulatorState

        return await self.session.scalar(
            select(RealtimeSimulatorState)
            .where(RealtimeSimulatorState.singleton_id == _SINGLETON_ID)
            .with_for_update()
        )


class RealtimeSimulatorCoordinator:
    """lifespan 内で 1 つだけ動く DB ポーリングタスク。"""

    def __init__(self, settings: Settings, *, poll_interval_sec: float = 0.25) -> None:
        self.settings = settings
        self.poll_interval_sec = poll_interval_sec
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run(), name="realtime-simulator")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop_event.set()
        await self._task
        self._task = None

    async def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                async with session_scope(self.settings) as session:
                    await SimulatorService(session).tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("simulator_tick_failed")
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self.poll_interval_sec,
                )
            except TimeoutError:
                pass


def _state_data(row: RealtimeSimulatorState, *, now: datetime) -> SimulatorStateData:
    if row.scenario is None:
        return _empty_state()
    scenario = RealtimeScenario.model_validate(row.scenario)
    elapsed = elapsed_minutes(
        carried_min=row.elapsed_min,
        started_at=row.started_at if row.running else None,
        speed=row.speed,
        now=now,
    )
    return SimulatorStateData(
        loaded=True,
        name=scenario.name,
        running=row.running,
        speed=row.speed,
        elapsed_min=elapsed,
        next_event_index=row.next_event_index,
        event_count=len(scenario.events),
        virtual_time=scenario.base_time + timedelta(minutes=elapsed),
    )


def _empty_state() -> SimulatorStateData:
    return SimulatorStateData(
        loaded=False,
        name=None,
        running=False,
        speed=1.0,
        elapsed_min=0.0,
        next_event_index=0,
        event_count=0,
        virtual_time=None,
    )


def _aware(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} は timezone-aware にしてください")
    return value
