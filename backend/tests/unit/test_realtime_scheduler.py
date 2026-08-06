"""TTN の 10 通/UTC 日と最短 5 分の純粋判定。"""

from datetime import UTC, datetime, timedelta

from app.domains.realtime.scheduler import evaluate_downlink


def test_tenth_is_allowed_and_eleventh_is_rejected() -> None:
    now = datetime(2026, 8, 2, 12, tzinfo=UTC)

    tenth = evaluate_downlink(count=9, last_sent=now - timedelta(minutes=5), now=now)
    eleventh = evaluate_downlink(count=10, last_sent=now - timedelta(hours=1), now=now)

    assert tenth.allowed is True
    assert tenth.count == 10
    assert eleventh.allowed is False
    assert eleventh.reason == "downlink_budget_exceeded"
    assert eleventh.count == 10


def test_less_than_five_minutes_is_rejected_and_boundary_is_allowed() -> None:
    now = datetime(2026, 8, 2, 12, tzinfo=UTC)

    too_soon = evaluate_downlink(
        count=1,
        last_sent=now - timedelta(minutes=4, seconds=59),
        now=now,
    )
    boundary = evaluate_downlink(
        count=1,
        last_sent=now - timedelta(minutes=5),
        now=now,
    )

    assert too_soon.allowed is False
    assert too_soon.reason == "downlink_too_soon"
    assert boundary.allowed is True
    assert boundary.count == 2
