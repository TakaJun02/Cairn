"""`ToolAdapters.ask_user`(§7 の HITL 実体)・`_FallbackRouteProvider`

(ADR-0020)の層 1 仕様。実 DB は使わない。`pending_ask_writer`/
`route_session_scope` を差し替えて、別トランザクション書き込みの
「呼ばれ方」だけを検査する(実際の別コミット可視性は
`tests/integration/test_ask_pending_database.py` /
`tests/integration/test_routes_early_commit_database.py` が compose DB で
検証する)。
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.domains.conversation.ask_registry import AskAnswer, AskUserRegistry
from app.domains.conversation.events import MemoryEventSink
from app.domains.conversation.state import SpotFact
from app.domains.conversation.tool_adapters import ToolAdapters, _FallbackRouteProvider
from app.domains.conversation.types import AskUserArgs, ToolError
from app.domains.geo.osrm import Coordinate, RouteResult
from app.domains.geo.repo import ApproachRecord, RouteRecord, SpotRecord
from app.domains.itinerary.repo_types import ItineraryVersion, PlanningData
from app.domains.itinerary.service import ItineraryService, SolverConfig
from app.domains.itinerary.solver import PlanningSpot, TravelTimeMatrix
from app.domains.itinerary.types import Diff, Itinerary
from app.domains.itinerary.types import ToolError as ItineraryToolError


class RecordingPendingAskWriter:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def __call__(
        self, *, thread_id: int, pending: dict[str, Any] | None, settings: Any
    ) -> None:
        del settings
        self.calls.append({"thread_id": thread_id, "pending": pending})


def _preference_args() -> AskUserArgs:
    return AskUserArgs.model_validate(
        {
            "kind": "preference",
            "slot": "mobility",
            "reason": "どのくらい歩けますか",
            "options": [
                {"label": "あまり歩きたくない", "value": "avoid_walk"},
                {"label": "30分程度なら", "value": "short_walk_ok"},
            ],
        }
    )


def _adapter(*, writer: RecordingPendingAskWriter, timeout_sec: float = 5.0) -> ToolAdapters:
    registry = AskUserRegistry()
    return ToolAdapters(
        cast(AsyncSession, None),
        event_sink=MemoryEventSink(),
        settings=get_settings(),
        thread_id=1,
        user_id=42,
        ask_registry=registry,
        ask_timeout_sec=timeout_sec,
        pending_ask_writer=writer,
    )


async def test_ask_user_waits_for_the_registry_answer_and_returns_ask_result() -> None:
    writer = RecordingPendingAskWriter()
    adapter = _adapter(writer=writer)

    async def answer_soon() -> None:
        await asyncio.sleep(0)
        resolved = adapter.ask_registry.resolve(
            42, AskAnswer(answer="30分程度なら", answered_by="chip")
        )
        assert resolved is True

    asyncio.create_task(answer_soon())
    result = await adapter.ask_user(step_id=1, args=_preference_args())

    assert result.data["answer"] == "30分程度なら"
    assert result.data["answered_by"] == "chip"
    # 1 回目 = 質問の書き込み、2 回目 = 回答受領後の即時クリア。
    assert [call["pending"] is not None for call in writer.calls] == [True, False]
    assert writer.calls[0]["pending"]["kind"] == "preference"
    assert writer.calls[0]["pending"]["slot"] == "mobility"
    assert "asked_at" in writer.calls[0]["pending"]
    assert writer.calls[1]["thread_id"] == 1


async def test_ask_user_timeout_returns_answered_by_timeout_and_still_clears_pending() -> None:
    writer = RecordingPendingAskWriter()
    adapter = _adapter(writer=writer, timeout_sec=0.02)

    result = await adapter.ask_user(step_id=1, args=_preference_args())

    assert result.data["answered_by"] == "timeout"
    assert result.data["answer"]  # 空文字ではない(AskUserResult.answer は min_length=1)
    assert [call["pending"] is not None for call in writer.calls] == [True, False]


async def test_ask_user_registers_waiter_before_emitting_sse_or_writing_pending() -> None:
    """裁定6(2026-08-04レビュー是正): 順序は

    ①レジストリに waiter 登録 → ②SSE送出 → ③pending_ask 書き込み。
    ②③どちらの時点でも `ask_registry.is_waiting` が既に True であること
    (`GET /thread` が「死んだ待機」と誤認して掃除しない条件)を確認する。
    """

    log: list[str] = []
    registry = AskUserRegistry()

    class RecordingSink:
        async def emit(self, event: Any) -> None:
            del event
            log.append(f"sse:is_waiting={registry.is_waiting(42)}")

    class RecordingWriter:
        async def __call__(
            self, *, thread_id: int, pending: Any, settings: Any
        ) -> None:
            del thread_id, settings
            if pending is not None:
                log.append(f"pending_write:is_waiting={registry.is_waiting(42)}")
            else:
                log.append("pending_clear")

    adapter = ToolAdapters(
        cast(AsyncSession, None),
        event_sink=RecordingSink(),
        settings=get_settings(),
        thread_id=1,
        user_id=42,
        ask_registry=registry,
        ask_timeout_sec=5.0,
        pending_ask_writer=RecordingWriter(),
    )

    async def answer_soon() -> None:
        await asyncio.sleep(0)
        registry.resolve(42, AskAnswer(answer="30分程度なら", answered_by="chip"))

    asyncio.create_task(answer_soon())
    result = await adapter.ask_user(step_id=1, args=_preference_args())

    assert log == [
        "sse:is_waiting=True",
        "pending_write:is_waiting=True",
        "pending_clear",
    ]
    assert result.data["answer"] == "30分程度なら"


async def test_ask_user_answer_arriving_during_sse_emit_still_resolves() -> None:
    """表示直後の即答が 409(取りこぼし)にならない。

    旧実装は SSE 送出 → DB 書き込み → レジストリ登録の順だったため、
    フォーム表示直後にユーザーが即答すると waiter が未登録で回答を
    取りこぼしていた(表示直後の回答が失敗する実バグ)。
    """

    registry = AskUserRegistry()

    class ImmediateAnswerSink:
        async def emit(self, event: Any) -> None:
            del event
            resolved = registry.resolve(
                42, AskAnswer(answer="30分程度なら", answered_by="chip")
            )
            assert resolved is True

    adapter = ToolAdapters(
        cast(AsyncSession, None),
        event_sink=ImmediateAnswerSink(),
        settings=get_settings(),
        thread_id=1,
        user_id=42,
        ask_registry=registry,
        ask_timeout_sec=5.0,
        pending_ask_writer=RecordingPendingAskWriter(),
    )

    result = await adapter.ask_user(step_id=1, args=_preference_args())

    assert result.data["answer"] == "30分程度なら"
    assert result.data["answered_by"] == "chip"


async def test_ask_user_skips_pending_write_when_thread_id_is_none() -> None:
    writer = RecordingPendingAskWriter()
    registry = AskUserRegistry()
    adapter = ToolAdapters(
        cast(AsyncSession, None),
        event_sink=MemoryEventSink(),
        settings=get_settings(),
        thread_id=None,
        user_id=None,
        ask_registry=registry,
        ask_timeout_sec=0.02,
        pending_ask_writer=writer,
    )

    result = await adapter.ask_user(step_id=1, args=_preference_args())

    assert writer.calls == []
    assert result.data["answered_by"] == "timeout"  # user_id が無いので待機できない


# ---------------------------------------------------------------------------
# A7(dialogue_style.md §3 論点 C・2026-08-04 決定): 送出前の選択肢の名寄せ。
# ---------------------------------------------------------------------------


def _origin_args(options: list[dict[str, str]]) -> AskUserArgs:
    return AskUserArgs.model_validate(
        {
            "kind": "preference",
            "slot": "origin",
            "reason": "どこから出発しますか",
            "options": options,
        }
    )


async def test_ask_user_drops_unresolvable_origin_options_before_sending() -> None:
    """実測(known_issues §6-2)の再現: 「鳥海山麓の宿」のような解決不能な

    カテゴリ語は送出前に除去され、実在の施設名だけが SSE・pending_ask に残る。
    """

    writer = RecordingPendingAskWriter()
    registry = AskUserRegistry()
    adapter = ToolAdapters(
        cast(AsyncSession, None),
        event_sink=MemoryEventSink(),
        settings=get_settings(),
        spot_names={"spot_101": "道の駅象潟", "spot_102": "にかほ市役所"},
        thread_id=1,
        user_id=42,
        ask_registry=registry,
        pending_ask_writer=writer,
    )

    async def answer_soon() -> None:
        await asyncio.sleep(0)
        registry.resolve(42, AskAnswer(answer="道の駅象潟", answered_by="chip"))

    asyncio.create_task(answer_soon())
    result = await adapter.ask_user(
        step_id=1,
        args=_origin_args(
            [
                {"label": "道の駅象潟", "value": "道の駅象潟"},
                {"label": "にかほ市役所", "value": "にかほ市役所"},
                {"label": "鳥海山麓の宿", "value": "鳥海山麓の宿"},
                {"label": "その他", "value": "その他"},
            ]
        ),
    )

    assert result.data["answer"] == "道の駅象潟"
    assert writer.calls[0]["pending"]["options"] == [
        {"label": "道の駅象潟", "value": "道の駅象潟"},
        {"label": "にかほ市役所", "value": "にかほ市役所"},
    ]


async def test_ask_user_returns_recoverable_error_when_fewer_than_two_options_resolve() -> None:
    """除去の結果 2 個未満なら、質問を送出せず recoverable な ToolError を返す

    (既存 A3 と同じ閾値。waiter 登録・SSE 送出・pending_ask 書き込みのいずれも
    行わない)。
    """

    writer = RecordingPendingAskWriter()
    sink = MemoryEventSink()
    registry = AskUserRegistry()
    adapter = ToolAdapters(
        cast(AsyncSession, None),
        event_sink=sink,
        settings=get_settings(),
        spot_names={"spot_101": "道の駅象潟"},
        thread_id=1,
        user_id=42,
        ask_registry=registry,
        pending_ask_writer=writer,
    )

    result = await adapter.ask_user(
        step_id=1,
        args=_origin_args(
            [
                {"label": "鳥海山麓の宿", "value": "鳥海山麓の宿"},
                {"label": "その他", "value": "その他"},
            ]
        ),
    )

    assert isinstance(result, ToolError)
    assert result.recoverable is True
    assert result.details["removed_options"] == ["鳥海山麓の宿", "その他"]
    assert writer.calls == []
    assert sink.events == []
    assert registry.is_waiting(42) is False


async def test_ask_user_option_guard_does_not_affect_enum_preference_slots() -> None:
    """選好 enum(mobility 等)は名寄せ対象外なので、実在しない値でも素通りする。"""

    writer = RecordingPendingAskWriter()
    adapter = _adapter(writer=writer)

    async def answer_soon() -> None:
        await asyncio.sleep(0)
        adapter.ask_registry.resolve(42, AskAnswer(answer="30分程度なら", answered_by="chip"))

    asyncio.create_task(answer_soon())
    result = await adapter.ask_user(step_id=1, args=_preference_args())

    assert result.data["answer"] == "30分程度なら"
    assert writer.calls[0]["pending"]["options"] == [
        {"label": "あまり歩きたくない", "value": "avoid_walk"},
        {"label": "30分程度なら", "value": "short_walk_ok"},
    ]


async def test_ask_user_option_guard_resolves_real_catalog_aliases() -> None:
    """H-3 の回帰(2026-08-04 レビュー是正): 実カタログ相当の別名は誤除去されない。

    是正前は `_apply_ask_user_option_guard` が `self.spot_names`(表示名の
    みの spot_id → 名前辞書)から `SpotFact` を合成しており `aliases_ja` が
    常に空だったため、実データで実在する別名「ゆらり」(`spot_030`「鳥海温泉
    遊楽里」の別名)・「ねむの丘」(道の駅象潟の別名)が解決不能と誤判定され
    除去されていた。`spot_catalog`(`state.spot_catalog` 相当・別名込み)を
    渡すと、この 2 つの別名がどちらも A7 を通過して送出されることを確認する。
    """

    writer = RecordingPendingAskWriter()
    registry = AskUserRegistry()
    adapter = ToolAdapters(
        cast(AsyncSession, None),
        event_sink=MemoryEventSink(),
        settings=get_settings(),
        spot_names={
            "spot_030": "鳥海温泉 遊楽里",
            "spot_101": "道の駅象潟",
        },
        spot_catalog={
            "spot_030": SpotFact(
                spot_id="spot_030",
                name_ja="鳥海温泉 遊楽里",
                kind="onsen",
                aliases_ja=["ゆらり"],
            ),
            "spot_101": SpotFact(
                spot_id="spot_101",
                name_ja="道の駅象潟",
                kind="roadside_station",
                aliases_ja=["ねむの丘"],
            ),
        },
        thread_id=1,
        user_id=42,
        ask_registry=registry,
        pending_ask_writer=writer,
    )

    async def answer_soon() -> None:
        await asyncio.sleep(0)
        registry.resolve(42, AskAnswer(answer="ゆらり", answered_by="chip"))

    asyncio.create_task(answer_soon())
    result = await adapter.ask_user(
        step_id=1,
        args=_origin_args(
            [
                {"label": "ゆらり", "value": "ゆらり"},
                {"label": "ねむの丘", "value": "ねむの丘"},
            ]
        ),
    )

    assert result.data["answer"] == "ゆらり"
    assert writer.calls[0]["pending"]["options"] == [
        {"label": "ゆらり", "value": "ゆらり"},
        {"label": "ねむの丘", "value": "ねむの丘"},
    ]


async def test_ask_user_option_guard_logs_removed_options(caplog: Any) -> None:
    """M-3(観測性のみ): 除去したとき、差し戻しに至らないケースでもログに残す。"""

    import logging

    writer = RecordingPendingAskWriter()
    registry = AskUserRegistry()
    adapter = ToolAdapters(
        cast(AsyncSession, None),
        event_sink=MemoryEventSink(),
        settings=get_settings(),
        spot_names={"spot_101": "道の駅象潟", "spot_102": "にかほ市役所"},
        thread_id=1,
        user_id=42,
        ask_registry=registry,
        pending_ask_writer=writer,
    )

    async def answer_soon() -> None:
        await asyncio.sleep(0)
        registry.resolve(42, AskAnswer(answer="道の駅象潟", answered_by="chip"))

    asyncio.create_task(answer_soon())
    with caplog.at_level(logging.INFO, logger="app.conversation.tool_adapters"):
        await adapter.ask_user(
            step_id=1,
            args=_origin_args(
                [
                    {"label": "道の駅象潟", "value": "道の駅象潟"},
                    {"label": "にかほ市役所", "value": "にかほ市役所"},
                    {"label": "鳥海山麓の宿", "value": "鳥海山麓の宿"},
                ]
            ),
        )

    records = [
        record
        for record in caplog.records
        if record.message == "ask_user_option_guard_removed_options"
    ]
    assert len(records) == 1
    assert records[0].removed_options == ["鳥海山麓の宿"]
    assert records[0].resolution == "sent"


async def test_fallback_route_provider_opens_a_dedicated_session_per_leg() -> None:
    """ADR-0020: レッグごとに専用の session_scope を開く(ターンの session を

    使い回さない)。実 DB/OSRM は使わず、注入した factory が呼ばれる回数と、
    その中で `RouteService`/`GeoRepository` が失敗しても `degraded` へ
    閉じ込められること(局所縮退)だけを検査する。
    """

    opened: list[object] = []

    @asynccontextmanager
    async def fake_session_scope() -> Any:
        marker = object()
        opened.append(marker)
        # `GeoRepository(None)` を実際に叩くと例外になる(セッションが無い)。
        # これにより「専用セッションを実際に使おうとした」ことも確認できる。
        yield cast(AsyncSession, None)

    provider = _FallbackRouteProvider(
        settings=get_settings(),
        osrm=cast(Any, None),
        osrm_build="test-build",
        session_scope=fake_session_scope,
    )

    first = await provider.route_id_for_leg("spot_a", "spot_b", "car")
    second = await provider.route_id_for_leg("spot_c", "spot_d", "car")

    assert first is None
    assert second is None
    assert provider.degraded is True
    assert len(opened) == 2  # レッグごとに新しい session が開かれた


async def test_fallback_route_provider_does_not_touch_the_turn_session() -> None:
    """`ToolAdapters.session`(ターンの主 session)は route の永続化に

    一切使われない(専用 session だけが使われる)ことを、`session_scope`
    factory への注入で確認する。
    """

    touched_turn_session = False

    class BoomIfTouched:
        def __getattr__(self, name: str) -> Any:  # pragma: no cover - 失敗時のみ通る
            nonlocal touched_turn_session
            touched_turn_session = True
            raise AssertionError(f"ターンの session が使われました: {name}")

    calls: list[str] = []

    @asynccontextmanager
    async def fake_session_scope() -> Any:
        calls.append("opened")
        yield cast(AsyncSession, None)

    adapter = ToolAdapters(
        cast(AsyncSession, BoomIfTouched()),
        event_sink=MemoryEventSink(),
        settings=get_settings(),
        thread_id=1,
        user_id=42,
        route_session_scope=fake_session_scope,
    )

    provider = _FallbackRouteProvider(
        settings=adapter.settings,
        osrm=cast(Any, None),
        osrm_build="test-build",
        session_scope=adapter.route_session_scope,
    )
    result = await provider.route_id_for_leg("spot_a", "spot_b", "car")

    assert result is None
    assert provider.degraded is True
    assert calls == ["opened"]
    assert touched_turn_session is False


# ---------------------------------------------------------------------------
# H-1(2026-08-04 レビュー是正): OSRM への HTTP 往復は session の外で行い、
# レッグの並列度は Semaphore で絞る。
# ---------------------------------------------------------------------------


class _FakeGeoRepository:
    """`RouteRepository` プロトコルを満たす fake(DB を使わない)。

    全 spot が `direct_by_car=True` を返すため、`fetch_route_segments` は
    "car" セグメント 1 本だけを OSRM に要求する(fake OSRM を単純化できる)。
    """

    def __init__(self, _session: Any, *, save_log: list[str]) -> None:
        del _session  # M-1: session の中身は使わない(commit は session_scope 側で記録)。
        self._save_log = save_log

    async def get_route_by_hash(self, params_hash: str) -> RouteRecord | None:
        del params_hash
        return None  # 常に miss(段2 OSRM を通す)。

    async def get_route_by_id(self, route_id: Any) -> RouteRecord | None:  # pragma: no cover
        raise AssertionError("このテストでは呼ばれません")

    async def get_spot(self, spot_id: str) -> SpotRecord:
        return SpotRecord(
            spot_id=spot_id, name_ja=spot_id, coordinate=Coordinate(lon=140.0, lat=39.0)
        )

    async def get_approach(self, spot_id: str) -> ApproachRecord:
        coordinate = Coordinate(lon=140.0, lat=39.0)
        return ApproachRecord(
            spot_id=spot_id,
            spot_coordinate=coordinate,
            direct_by_car=True,
            car_node=coordinate,
            access_point_id=None,
            walk_sec=0,
            walk_m=0,
            snap_m=0,
        )

    async def save_route(self, **values: Any) -> RouteRecord:
        self._save_log.append(str(values["params_hash"]))
        return RouteRecord(
            route_id=uuid4(),
            params_hash=str(values["params_hash"]),
            params=values["params"],
            mode_summary=values["mode_summary"],
            distance_m=values["distance_m"],
            duration_sec=values["duration_sec"],
            segments=values["segments"],
            geojson=values["geojson"],
            created_at=datetime.now(UTC),
        )


class _FakeOSRMClient:
    """`OSRMClient.route` だけを満たす fake。実ネットワークを使わない。"""

    def __init__(self) -> None:
        self.calls = 0
        self.in_flight = 0
        self.max_in_flight = 0

    async def route(self, mode: Any, coordinates: Any) -> RouteResult:
        del mode, coordinates
        self.calls += 1
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        await asyncio.sleep(0)  # 他コルーチンに制御を譲り、並列度を観測可能にする。
        self.in_flight -= 1
        return RouteResult(
            distance_m=100.0,
            duration_sec=60.0,
            geometry=(Coordinate(lon=140.0, lat=39.0), Coordinate(lon=140.001, lat=39.001)),
        )


def _fake_session_scope_with_commit_tracking(
    commit_flags: list[dict[str, bool]],
) -> Any:
    """`session_scope` の契約(with を正常に抜けたら commit する)を、

    実 DB 無しで記録する fake。返す flag オブジェクトは with を抜けた
    (= commit された)瞬間に `committed=True` になる。
    """

    @asynccontextmanager
    async def fake_session_scope() -> Any:
        flag = {"committed": False}
        commit_flags.append(flag)
        yield cast(AsyncSession, None)
        flag["committed"] = True

    return fake_session_scope


def _minimal_planning_data() -> PlanningData:
    spots = {
        "spot_origin": PlanningSpot("spot_origin", ("宿泊施設",), 0, kind="facility"),
        "spot_a": PlanningSpot("spot_a", ("滝",), 30),
        "spot_b": PlanningSpot("spot_b", ("神社",), 30),
    }
    legs = {
        (source, target, "car"): 10 * 60
        for source in spots
        for target in spots
        if source != target
    }
    return PlanningData(spots=spots, travel_times=TravelTimeMatrix(legs))


class _MinimalItineraryRepository:
    """`ItineraryService` が要求するプロトコルの最小実装(plan_itinerary のみ)。"""

    def __init__(self, planning: PlanningData) -> None:
        self.planning = planning

    async def get_current(self, user_id: int, *, for_update: bool = False) -> None:
        del user_id, for_update
        return None

    async def load_planning_data(self) -> PlanningData:
        return self.planning

    async def get_pending_constraints(
        self, user_id: int, *, for_update: bool = False
    ) -> list[dict[str, Any]]:
        del user_id, for_update
        return []

    async def clear_pending_constraints(self, user_id: int) -> None:
        del user_id

    async def append_version(
        self,
        *,
        user_id: int,
        itinerary: Any,
        constraints: Any,
        origin: str,
        created_by_message_id: int | None = None,
        expected_parent_version: int | None = None,
    ) -> ItineraryVersion:
        del expected_parent_version
        stored = itinerary.model_copy(update={"version": 1}, deep=True)
        return ItineraryVersion(
            user_id=user_id,
            version=1,
            parent_version=None,
            is_current=True,
            itinerary=stored,
            constraints=[
                value.model_dump(mode="json") if hasattr(value, "model_dump") else dict(value)
                for value in constraints
            ],
            origin=origin,
            created_by_message_id=created_by_message_id,
        )


async def test_route_provider_commits_every_leg_before_plan_itinerary_returns() -> None:
    """M-1(2026-08-04 レビュー是正): `ItineraryService.plan_itinerary` が

    `route_provider=_FallbackRouteProvider(...)` を通して呼ばれたとき、
    戻り値(= 呼び出し元が `state:itinerary` final を送出する直前の時点)
    までに、使われた全レッグの session が commit 済みであることを検証する。
    実 DB・実 OSRM は使わない(fake session_scope + fake repository +
    fake OSRM を注入)。
    """

    commit_flags: list[dict[str, bool]] = []
    save_log: list[str] = []
    osrm = _FakeOSRMClient()

    provider = _FallbackRouteProvider(
        settings=get_settings(),
        osrm=cast(Any, osrm),
        osrm_build="test-build",
        session_scope=_fake_session_scope_with_commit_tracking(commit_flags),
        repository_factory=lambda session: _FakeGeoRepository(session, save_log=save_log),
    )

    service = ItineraryService(
        cast(Any, _MinimalItineraryRepository(_minimal_planning_data())),
        solver_config=SolverConfig(iterations=20, minimum_iterations=20, time_limit_ms=2_000),
        route_provider=provider,
    )

    result = await service.plan_itinerary(
        user_id=7,
        days=[
            {
                "date": "2026-08-10",
                "start": "09:00",
                "end": "13:00",
                "origin": {"kind": "spot", "id": "spot_origin"},
            }
        ],
        must_visit=["spot_a"],
    )

    assert not isinstance(result, ItineraryToolError)
    # 少なくとも 1 レッグ(origin→spot_a→origin なら 2 レッグ)分の
    # session が実際に使われた。
    assert len(commit_flags) >= 2
    # plan_itinerary が返った時点(= final 送出の直前)で、使われた
    # session は「段1(照会)/段3(保存)いずれも」既に commit 済み。
    assert all(flag["committed"] for flag in commit_flags)
    # 保存(段3)も実際に行われている(冪等キャッシュは常に miss させたため)。
    assert save_log
    # 経路が実際に解決されている(route_id が None のレッグが無い)。
    for day in result.itinerary.days:
        for item in day.items:
            assert item.leg_from_prev.route_id is not None


async def test_route_provider_limits_concurrent_legs_with_semaphore() -> None:
    """H-1: レッグの並列度が `concurrency` で頭打ちになる

    (既定は `ROUTE_LEG_CONCURRENCY=4`。ここではテストを速くするため 2 に
    絞って観測する)。
    """

    commit_flags: list[dict[str, bool]] = []
    osrm = _FakeOSRMClient()
    concurrency = 2

    provider = _FallbackRouteProvider(
        settings=get_settings(),
        osrm=cast(Any, osrm),
        osrm_build="test-build",
        session_scope=_fake_session_scope_with_commit_tracking(commit_flags),
        repository_factory=lambda session: _FakeGeoRepository(session, save_log=[]),
        concurrency=concurrency,
    )

    legs = [
        ("spot_origin", "spot_a"),
        ("spot_a", "spot_b"),
        ("spot_b", "spot_c"),
        ("spot_c", "spot_d"),
    ]
    results = await asyncio.gather(
        *(provider.route_id_for_leg(source, target, "car") for source, target in legs)
    )

    assert all(value is not None for value in results)
    assert osrm.max_in_flight <= concurrency


# ---------------------------------------------------------------------------
# レビュー是正(項目7): `_emit_itinerary_state` が SSE ペイロードに
# assumptions を載せることの検証(tool_adapters 層)。
# ---------------------------------------------------------------------------


async def test_emit_itinerary_state_includes_assumptions_in_sse_payload() -> None:
    """`_provisional_itinerary_sink`(`_emit_itinerary_state` 経由)が

    `Itinerary.assumptions` を state:itinerary のトップレベル `assumptions`
    へ複製することを検証する(chat_sse.md §1.2)。provisional/final いずれも
    同じ `_emit_itinerary_state` を通るため、ここでは provisional 経路
    (`_provisional_itinerary_sink`)で代表させる。
    """

    sink = MemoryEventSink()
    adapter = ToolAdapters(
        cast(AsyncSession, None),
        event_sink=sink,
        settings=get_settings(),
        thread_id=1,
        user_id=42,
    )
    itinerary = Itinerary(
        days=[],
        version=3,
        assumptions=["日付は明日と仮定", "起点は道の駅と仮定"],
    )

    provisional_sink = adapter._provisional_itinerary_sink()
    await provisional_sink(itinerary, Diff())

    assert len(sink.events) == 1
    event = sink.events[0]
    assert event.data["kind"] == "itinerary"
    assert event.data["phase"] == "provisional"
    # トップレベル(chat_sse.md の契約どおり)。
    assert event.data["assumptions"] == ["日付は明日と仮定", "起点は道の駅と仮定"]
    # 入れ子(Itinerary 本体にも同じ内容が載っている)。
    assert event.data["itinerary"]["assumptions"] == [
        "日付は明日と仮定",
        "起点は道の駅と仮定",
    ]


async def test_emit_itinerary_state_with_empty_assumptions() -> None:
    """assumptions が空のときは空配列のまま(前提なし)。"""

    sink = MemoryEventSink()
    adapter = ToolAdapters(
        cast(AsyncSession, None),
        event_sink=sink,
        settings=get_settings(),
        thread_id=1,
        user_id=42,
    )
    itinerary = Itinerary(days=[], version=1, assumptions=[])

    provisional_sink = adapter._provisional_itinerary_sink()
    await provisional_sink(itinerary, Diff())

    assert sink.events[0].data["assumptions"] == []
