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
from app.domains.itinerary.types import (
    Constraint,
    Itinerary,
    ItineraryDay,
    ItineraryItem,
    LegFromPrev,
    PredEnum,
    SpotEndpoint,
)


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


def test_relaxed_solution_never_drops_required_spot_ids() -> None:
    """レビュー是正 H-1: 解 C(ゆったり版)は `required_spot_ids`(must_visit)を

    除去対象にしない。旧実装は `locked`/`protected`/`removal_protected` しか
    見ておらず、`required_spot_ids` を丸ごと除去候補にしていた。
    """

    data = replace(
        _planning_data(),
        required_spot_ids=frozenset({"spot_a", "spot_b"}),
    )
    solved = solve_itinerary(data)
    solution_a, _, solution_c = solved.solutions

    assert {"spot_a", "spot_b"} <= set(itinerary_spot_ids(solution_a))
    assert {"spot_a", "spot_b"} <= set(itinerary_spot_ids(solution_c))


def test_relaxed_solution_with_single_required_spot_and_no_optional_pool_is_not_emptied() -> None:
    """H-1 の報告そのもの: 挿入プールが must_visit 1 件だけ(任意候補ゼロ)の

    とき、旧実装は解 C がその 1 件を落として空旅程になっていた
    (`removable` に required も含めていたため)。是正後は除去対象が無く
    なり、`if not removable` の分岐で解 A のまま(譲歩の再計算のみ)返る。
    """

    data = replace(
        _planning_data(),
        required_spot_ids=frozenset({"spot_a"}),
        insertion_pool=frozenset({"spot_a"}),
    )
    solved = solve_itinerary(data)
    solution_a, _, solution_c = solved.solutions

    assert itinerary_spot_ids(solution_a) == ["spot_a"]
    assert itinerary_spot_ids(solution_c) == ["spot_a"]


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


def test_solve_itinerary_alternatives_false_skips_b_and_c_and_returns_solution_a() -> None:
    """ADR-0021: 集合固定の編集は `alternatives=False` で B/C の生成(2 回目の

    ILS・緩和解)を省略し、解 A を 3 スロットへそのまま詰めて返す。
    """

    data = _planning_data()

    solved = solve_itinerary(data, alternatives=False)

    solution_a, solution_b, solution_c = solved.solutions
    assert solution_a.model_dump(mode="json") == solution_b.model_dump(mode="json")
    assert solution_a.model_dump(mode="json") == solution_c.model_dump(mode="json")
    assert solved.scores[0] == solved.scores[1] == solved.scores[2]
    assert solved.iterations_run[1] == 0
    assert not validate_hard_constraints(solution_a, data.spots, data.travel_times)


def test_insertion_pool_empty_blocks_refill_but_none_allows_it() -> None:
    """ADR-0021: `insertion_pool=frozenset()`(編集ターンの既定「集合固定」)は

    削除で空いた時間へ新規スポットを詰め直さない。`insertion_pool=None`
    (allow_refill 相当・従来挙動)なら空いた時間へ詰め直される。頼んでいない
    スポットの入れ替え([25 §1-2])を塞ぐ実装の直接の検証。
    """

    data = _planning_data()
    baseline = solve_itinerary(data).solutions[0]
    baseline_ids = itinerary_spot_ids(baseline)
    assert len(baseline_ids) >= 2, "この規模なら 2 件以上入るはず"

    removed_id = baseline_ids[0]
    remaining_ids = frozenset(baseline_ids[1:])
    initial = baseline.model_copy(deep=True)
    initial.days[0].items = [
        item for item in initial.days[0].items if item.spot_id != removed_id
    ]

    locked_data = replace(
        data,
        previous=baseline,
        initial=initial,
        removal_protected_spot_ids=remaining_ids,
        insertion_pool=frozenset(),
    )
    locked_result = solve_itinerary(locked_data, alternatives=False).solutions[0]
    assert frozenset(itinerary_spot_ids(locked_result)) == remaining_ids
    assert not validate_hard_constraints(locked_result, data.spots, data.travel_times)

    refill_data = replace(
        data,
        previous=baseline,
        initial=initial,
        removal_protected_spot_ids=remaining_ids,
        insertion_pool=None,
    )
    refilled_result = solve_itinerary(refill_data).solutions[0]
    refilled_ids = frozenset(itinerary_spot_ids(refilled_result))
    assert remaining_ids <= refilled_ids
    assert refilled_ids != remaining_ids


# ---------------------------------------------------------------------------
# C-1(2026-08-04 レビュー是正・Critical): removal_protected_spot_ids は
# 削除保護専用であり、並び替え(2-opt/or-opt)まで止めてはいけない。
# 「全項目 locked 扱い」(ADR-0021 が明示的に却下した案)になっていないことを
# 直接検証する。
# ---------------------------------------------------------------------------


def _line_planning() -> tuple[dict[str, PlanningSpot], TravelTimeMatrix, PlanningDay]:
    """一直線上に並んだ地点(travel(i,j) = |pos(i)-pos(j)| 分)。

    交差した訪問順は 2-opt/or-opt で明確に改善できる、古典的な検証用配置。
    """

    spots = {
        "spot_origin": PlanningSpot("spot_origin", ("宿泊施設",), 0, kind="facility"),
        "spot_a": PlanningSpot("spot_a", ("滝",), 10),
        "spot_b": PlanningSpot("spot_b", ("神社",), 10),
        "spot_c": PlanningSpot("spot_c", ("自然",), 10),
        "spot_d": PlanningSpot("spot_d", ("温泉",), 10),
    }
    positions = {"spot_origin": 0, "spot_a": 10, "spot_b": 20, "spot_c": 30, "spot_d": 40}
    legs = {
        (source, target, "car"): abs(positions[source] - positions[target]) * 60
        for source in positions
        for target in positions
        if source != target
    }
    travel_times = TravelTimeMatrix(legs)
    day = PlanningDay("2026-08-10", 0, 600, "spot_origin", "spot_origin")
    return spots, travel_times, day


def _build_itinerary(
    order: list[str],
    spots: dict[str, PlanningSpot],
    travel_times: TravelTimeMatrix,
    day: PlanningDay,
) -> Itinerary:
    items: list[ItineraryItem] = []
    clock = 0
    previous = day.origin_spot_id
    for spot_id in order:
        leg = travel_times.best_leg(previous, spot_id)
        assert leg is not None
        clock += leg.min
        arrive = clock
        stay = spots[spot_id].stay_min
        clock += stay
        items.append(
            ItineraryItem(
                seq=len(items) + 1,
                spot_id=spot_id,
                arrive_min=arrive,
                stay_min=stay,
                depart_min=arrive + stay,
                leg_from_prev=LegFromPrev(mode=leg.mode, min=leg.min, route_id=None),
            )
        )
        previous = spot_id
    return Itinerary(
        days=[
            ItineraryDay(
                date=day.date,
                start_min=day.start_min,
                end_min=day.end_min,
                origin=SpotEndpoint(spot_id=day.origin_spot_id),
                destination=SpotEndpoint(spot_id=day.destination_spot_id),
                items=items,
            )
        ],
        version=1,
    )


def _route_travel_minutes(
    order: list[str], travel_times: TravelTimeMatrix, day: PlanningDay
) -> int:
    total = 0
    previous = day.origin_spot_id
    for spot_id in order:
        leg = travel_times.best_leg(previous, spot_id)
        assert leg is not None
        total += leg.min
        previous = spot_id
    leg = travel_times.best_leg(previous, day.destination_spot_id)
    assert leg is not None
    total += leg.min
    return total


def test_removal_protection_allows_two_opt_to_improve_a_crossing_order() -> None:
    """(a)(b) 集合固定の編集でも並びは改善し得る。集合(顔ぶれ)は変わらない。

    わざと交差した(移動時間が長い)順序を `initial` として与え、
    `removal_protected_spot_ids` で全訪問を削除保護しつつ `insertion_pool`
    を空にした編集相当の入力で解いても、2-opt/or-opt が並びを改善できる
    ことを確認する(C-1: `protected_spot_ids` を誤用すると並び替えまで
    止まってしまっていたバグの回帰テスト)。
    """

    spots, travel_times, day = _line_planning()
    bad_order = ["spot_c", "spot_a", "spot_d", "spot_b"]
    initial = _build_itinerary(bad_order, spots, travel_times, day)
    all_ids = frozenset(bad_order)

    data = SolverInput(
        days=(day,),
        spots=spots,
        travel_times=travel_times,
        previous=initial,
        initial=initial,
        removal_protected_spot_ids=all_ids,
        insertion_pool=frozenset(),
        config=SolverConfig(iterations=80, minimum_iterations=80, time_limit_ms=2_000),
    )

    solved = solve_itinerary(data, alternatives=False).solutions[0]
    solved_order = itinerary_spot_ids(solved)

    # (b) 集合(訪問の顔ぶれ)は変わらない。
    assert frozenset(solved_order) == all_ids
    # (a) 並びが改善し得る(交差した悪い順序より総移動時間が短くなる)。
    bad_travel = _route_travel_minutes(bad_order, travel_times, day)
    solved_travel = _route_travel_minutes(solved_order, travel_times, day)
    assert solved_travel < bad_travel
    # 参考: 直線上の最短巡回(昇順で往復)にまで改善できる規模設定。
    optimal_order = ["spot_a", "spot_b", "spot_c", "spot_d"]
    optimal_travel = _route_travel_minutes(optimal_order, travel_times, day)
    assert solved_travel == optimal_travel


def test_removal_protection_keeps_locked_items_relative_order() -> None:
    """(c) 集合固定の編集でも、locked 項目の相対順序は保たれる。"""

    spots, travel_times, day = _line_planning()
    bad_order = ["spot_c", "spot_a", "spot_d", "spot_b"]
    initial = _build_itinerary(bad_order, spots, travel_times, day)
    locked_index = 1  # spot_a
    locked_id = initial.days[0].items[locked_index].spot_id
    initial.days[0].items[locked_index] = initial.days[0].items[locked_index].model_copy(
        update={"locked": True}
    )
    before = [item.spot_id for item in initial.days[0].items[:locked_index]]
    after = [item.spot_id for item in initial.days[0].items[locked_index + 1 :]]

    data = SolverInput(
        days=(day,),
        spots=spots,
        travel_times=travel_times,
        previous=initial,
        initial=initial,
        removal_protected_spot_ids=frozenset(bad_order),
        insertion_pool=frozenset(),
        config=SolverConfig(iterations=80, minimum_iterations=80, time_limit_ms=2_000),
    )

    solved = solve_itinerary(data, alternatives=False).solutions[0]
    result_ids = itinerary_spot_ids(solved)

    assert frozenset(result_ids) == frozenset(bad_order)
    assert locked_id in result_ids
    result_position = result_ids.index(locked_id)
    assert all(
        result_ids.index(spot_id) < result_position for spot_id in before if spot_id in result_ids
    )
    assert all(
        result_ids.index(spot_id) > result_position for spot_id in after if spot_id in result_ids
    )
