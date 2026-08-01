"""シミュレータの倍率時刻と event 進行。"""

from datetime import UTC, datetime, timedelta

from app.domains.realtime.simulator import (
    RealtimeScenario,
    due_events,
    elapsed_minutes,
)


def _scenario() -> RealtimeScenario:
    return RealtimeScenario.model_validate(
        {
            "name": "rainy_afternoon",
            "base_time": "2026-08-10T09:00:00+09:00",
            "events": [
                {
                    "at_min": 180,
                    "spot_id": "spot_007",
                    "weather": 2,
                    "congestion": 2,
                },
                {
                    "at_min": 0,
                    "spot_id": "spot_007",
                    "weather": 0,
                    "congestion": 1,
                },
            ],
        }
    )


def test_speed_60_advances_one_scenario_minute_per_real_second() -> None:
    started = datetime(2026, 8, 2, 0, tzinfo=UTC)

    assert elapsed_minutes(
        carried_min=0,
        started_at=started,
        speed=60,
        now=started + timedelta(seconds=1),
    ) == 1


def test_due_events_advance_once_and_do_not_invent_other_spots() -> None:
    scenario = _scenario()

    first, next_index = due_events(scenario, next_event_index=0, elapsed_min=0)
    none_due, same_index = due_events(
        scenario,
        next_event_index=next_index,
        elapsed_min=179.9,
    )
    second, completed_index = due_events(
        scenario,
        next_event_index=same_index,
        elapsed_min=180,
    )

    assert [(event.at_min, event.spot_id) for event in first] == [(0, "spot_007")]
    assert none_due == ()
    assert same_index == next_index == 1
    assert [(event.at_min, event.spot_id) for event in second] == [(180, "spot_007")]
    assert completed_index == 2
