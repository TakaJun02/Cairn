"""固定シード ILS の再現性、物理整合、譲歩、別解を検証する。"""

from dataclasses import replace

from app.domains.itinerary.solver import (
    PlanningDay,
    PlanningSpot,
    SolverConfig,
    SolverInput,
    TravelTimeMatrix,
    itinerary_spot_ids,
    solve_itinerary,
    validate_hard_constraints,
)
from app.domains.itinerary.types import Constraint, PredEnum


def _planning_data(*, missing: tuple[str, str] | None = None) -> SolverInput:
    spots = {
        "spot_origin": PlanningSpot("spot_origin", ("宿泊施設",), 15, kind="facility"),
        "spot_a": PlanningSpot("spot_a", ("滝",), 25),
        "spot_b": PlanningSpot("spot_b", ("神社",), 30),
        "spot_c": PlanningSpot("spot_c", ("自然",), 35),
        "spot_d": PlanningSpot("spot_d", ("温泉",), 40),
        "spot_e": PlanningSpot("spot_e", ("公園",), 30),
        "spot_f": PlanningSpot("spot_f", ("海岸",), 25),
        "spot_closed": PlanningSpot("spot_closed", ("登山",), 20, season_closed_months=(8,)),
    }
    identifiers = sorted(spots)
    legs = {
        (source, target, "car"): (8 + abs(index - target_index) * 2) * 60
        for index, source in enumerate(identifiers)
        for target_index, target in enumerate(identifiers)
        if source != target and (source, target) != missing
    }
    return SolverInput(
        days=(PlanningDay("2026-08-10", 540, 840, "spot_origin", "spot_origin"),),
        spots=spots,
        travel_times=TravelTimeMatrix(legs),
        config=SolverConfig(iterations=40, minimum_iterations=40, time_limit_ms=2_000),
    )


def test_fixed_seed_same_input_returns_byte_equivalent_solutions() -> None:
    data = _planning_data()

    first = solve_itinerary(data)
    second = solve_itinerary(data)

    assert [value.model_dump(mode="json") for value in first.solutions] == [
        value.model_dump(mode="json") for value in second.solutions
    ]
    assert first.scores == second.scores
    for solution in first.solutions:
        assert not validate_hard_constraints(solution, data.spots, data.travel_times)
        assert len(itinerary_spot_ids(solution)) == len(set(itinerary_spot_ids(solution)))
        assert all(
            isinstance(value, int)
            for day in solution.days
            for item in day.items
            for value in (item.arrive_min, item.stay_min, item.depart_min)
        )


def test_a_b_c_are_returned_and_c_has_one_fewer_poi() -> None:
    data = _planning_data()
    solved = solve_itinerary(data)
    solution_a, solution_b, solution_c = solved.solutions

    assert len(solved.solutions) == 3
    assert len(itinerary_spot_ids(solution_c)) == len(itinerary_spot_ids(solution_a)) - 1
    assert len(set(itinerary_spot_ids(solution_a)) & set(itinerary_spot_ids(solution_b))) < len(
        itinerary_spot_ids(solution_a)
    )
    assert (
        sum(item.stay_min for day in solution_c.days for item in day.items)
        >= sum(item.stay_min for day in solution_a.days for item in day.items)
        - data.spots[itinerary_spot_ids(solution_a)[0]].stay_min
    )


def test_impossible_soft_requirement_returns_concession_instead_of_exception() -> None:
    constraint = Constraint(
        id="c_001",
        pred=PredEnum.REQUIRE,
        args={"target": "spot_closed"},
        weight=12,
    )
    data = replace(
        _planning_data(),
        constraints=(constraint,),
        required_spot_ids=frozenset({"spot_closed"}),
    )

    solution = solve_itinerary(data).solutions[0]

    assert "spot_closed" not in itinerary_spot_ids(solution)
    assert [(item.constraint_id, item.pred) for item in solution.concessions] == [
        ("c_001", PredEnum.REQUIRE)
    ]
    assert not validate_hard_constraints(solution, data.spots, data.travel_times)


def test_missing_matrix_pair_is_unreachable_and_logs_degraded(caplog) -> None:
    data = _planning_data(missing=("spot_origin", "spot_a"))
    constraint = Constraint(
        id="c_001",
        pred=PredEnum.REQUIRE,
        args={"target": "spot_a"},
        weight=12,
    )
    data = replace(
        data,
        constraints=(constraint,),
        required_spot_ids=frozenset({"spot_a"}),
        excluded_spot_ids=frozenset(set(data.spots) - {"spot_origin", "spot_a"}),
    )

    solution = solve_itinerary(data).solutions[0]

    assert itinerary_spot_ids(solution) == []
    records = [record for record in caplog.records if record.msg == "itinerary_travel_time_missing"]
    assert records
    assert records[0].degraded is True


def test_empty_day_can_travel_directly_between_different_endpoints() -> None:
    data = _planning_data()
    direct = replace(
        data,
        days=(PlanningDay("2026-08-10", 540, 600, "spot_a", "spot_b"),),
        excluded_spot_ids=frozenset(set(data.spots) - {"spot_a", "spot_b"}),
    )

    solution = solve_itinerary(direct).solutions[0]

    assert itinerary_spot_ids(solution) == []
    assert not validate_hard_constraints(solution, direct.spots, direct.travel_times)


def test_locked_item_is_not_removed_or_crossed_by_existing_items() -> None:
    data = _planning_data()
    previous = solve_itinerary(data).solutions[0]
    assert len(previous.days[0].items) >= 3
    locked_index = 1
    locked_id = previous.days[0].items[locked_index].spot_id
    previous.days[0].items[locked_index] = (
        previous.days[0].items[locked_index].model_copy(update={"locked": True})
    )
    before = [item.spot_id for item in previous.days[0].items[:locked_index]]
    after = [item.spot_id for item in previous.days[0].items[locked_index + 1 :]]
    edited_data = replace(data, previous=previous, initial=previous)

    edited = solve_itinerary(edited_data).solutions[0]
    result_ids = itinerary_spot_ids(edited)

    assert locked_id in result_ids
    result_position = result_ids.index(locked_id)
    assert all(
        result_ids.index(spot_id) < result_position for spot_id in before if spot_id in result_ids
    )
    assert all(
        result_ids.index(spot_id) > result_position for spot_id in after if spot_id in result_ids
    )


def test_unlock_in_initial_draft_overrides_previous_lock() -> None:
    data = _planning_data()
    solved = solve_itinerary(data).solutions[0]
    target = solved.days[0].items[0].spot_id
    locked_item = solved.days[0].items[0].model_copy(update={"locked": True})
    previous = solved.model_copy(
        update={
            "days": [solved.days[0].model_copy(update={"items": [locked_item]})],
        },
        deep=True,
    )
    initial = previous.model_copy(deep=True)
    initial.days[0].items[0] = initial.days[0].items[0].model_copy(update={"locked": False})
    edited_data = replace(
        data,
        previous=previous,
        initial=initial,
        utilities={target: -10.0},
        excluded_spot_ids=frozenset(set(data.spots) - {"spot_origin", target}),
    )

    edited = solve_itinerary(edited_data).solutions[0]

    assert target not in itinerary_spot_ids(edited)
