"""ADR-0020: `app.routes` への書き込みが、ターンの主 session の commit を

待たずに専用の一時 session で即時反映されることを compose DB で検証する。

`_FallbackRouteProvider.route_id_for_leg`(tool_adapters.py)が実際に使う
パターン(専用の `session_scope` で `GeoRepository.save_route` を呼ぶ)を
そのまま模し、「主トランザクションが開いたまま(未 commit)でも、別 session
から route が見える」ことを確認する。これが `state:itinerary`(final)送出
直後に `GET /api/v1/routes/{route_id}` が 200 を返す契約
([chat_sse.md §1.2](../../../Docs/40_api/chat_sse.md))の土台である。
"""

import os
from uuid import uuid4

import pytest
from sqlalchemy import select, text

from app.core.db import dispose_engine, session_scope
from app.domains.geo.repo import GeoRepository

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DB_INTEGRATION") != "1",
    reason="RUN_DB_INTEGRATION=1 のとき compose DB に対して実行する",
)


async def test_route_saved_via_dedicated_session_is_visible_before_caller_transaction_commits() -> (
    None
):
    params_hash = f"test-routes-early-commit-{uuid4()}"
    try:
        # ターンの主 session 役: 何かを読んでトランザクションを開始させ、
        # 最後まで commit しない(実際の POST /chat は persist まで commit しない)。
        async with session_scope() as main_session:
            await main_session.execute(select(1))

            # ADR-0020: route の永続化は専用の一時 session で行い、その場で
            # commit する(`_FallbackRouteProvider.route_id_for_leg` と同じ形)。
            async with session_scope() as route_session:
                saved = await GeoRepository(route_session).save_route(
                    params_hash=params_hash,
                    params={
                        "from": {"spot_id": "test_spot_a"},
                        "to": {"spot_id": "test_spot_b"},
                        "osrm_build": "test-build",
                    },
                    mode_summary="car",
                    distance_m=1234,
                    duration_sec=567,
                    segments=[
                        {
                            "mode": "car",
                            "distance_m": 1234,
                            "duration_sec": 567,
                            "from_idx": 0,
                            "to_idx": 1,
                        }
                    ],
                    geojson={"type": "FeatureCollection", "features": []},
                )

            # 主トランザクション(main_session)がまだ開いたまま(未 commit)の
            # 状態で、別セッションから route を読む。READ COMMITTED では、
            # 別セッション経由で既に commit された行は見える。
            async with session_scope() as reader_session:
                observed = await GeoRepository(reader_session).get_route_by_id(
                    saved.route_id
                )
                assert observed is not None
                assert observed.params_hash == params_hash
                assert observed.distance_m == 1234

            # main_session はここで初めて commit される(with を抜けるとき)。
    finally:
        async with session_scope() as cleanup_session:
            await cleanup_session.execute(
                text("DELETE FROM app.routes WHERE params_hash = :params_hash"),
                {"params_hash": params_hash},
            )
        await dispose_engine()


async def test_route_is_idempotent_across_dedicated_sessions() -> None:
    """`params_hash` が同じ route を 2 回保存しても、既存行がそのまま返る

    (`ON CONFLICT DO NOTHING` の冪等キャッシュ。geo.md §3.2)。専用 session を
    レッグごとに開閉する実装でも、同じ id に解決されることを確認する。
    """

    params_hash = f"test-routes-idempotent-{uuid4()}"
    try:
        async with session_scope() as first_session:
            first = await GeoRepository(first_session).save_route(
                params_hash=params_hash,
                params={"from": {"spot_id": "a"}, "to": {"spot_id": "b"}, "osrm_build": "x"},
                mode_summary="car",
                distance_m=100,
                duration_sec=60,
                segments=[],
                geojson={"type": "FeatureCollection", "features": []},
            )

        async with session_scope() as second_session:
            second = await GeoRepository(second_session).save_route(
                params_hash=params_hash,
                params={"from": {"spot_id": "a"}, "to": {"spot_id": "b"}, "osrm_build": "x"},
                mode_summary="car",
                distance_m=999,  # 異なる値を渡しても、既存行が優先される。
                duration_sec=999,
                segments=[],
                geojson={"type": "FeatureCollection", "features": []},
            )

        assert first.route_id == second.route_id
        assert second.distance_m == 100
    finally:
        async with session_scope() as cleanup_session:
            await cleanup_session.execute(
                text("DELETE FROM app.routes WHERE params_hash = :params_hash"),
                {"params_hash": params_hash},
            )
        await dispose_engine()
