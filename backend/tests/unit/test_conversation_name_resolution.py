"""名寄せ本実装(段4)の層1仕様。

`Docs/30_design/agent_react_architecture.md` §5 フロー2 が仕様。
優先順(①現在の旅程内の名前 ②last_candidates ③DB の name_ja 完全一致
④別名・表記ゆれ)・かな/カナ正規化・曖昧時の候補つき報告・
未解決要素の drop-and-report を検査する。

実データ(43件)の spot_id/名称はここでは使わない(Docs/README.md の注意書き:
文書中の spot_id の例は実データと一致しない。テストの期待値は自己完結した
フィクスチャで組み立てる)。
"""

from __future__ import annotations

import json
from pathlib import Path

from app.domains.conversation.name_resolution import (
    build_name_resolution_context,
    describe_ambiguous,
    normalize_name,
    resolve_constraint_target,
    resolve_constraint_target_detailed,
    resolve_names,
)
from app.domains.conversation.state import CandidateReference, SpotFact
from app.domains.itinerary.types import (
    Itinerary,
    ItineraryDay,
    ItineraryItem,
    LegFromPrev,
    SpotEndpoint,
)

_SEEDS_DIRECTORY = Path(__file__).resolve().parents[2] / "data" / "seeds"


def _spot(spot_id: str, name_ja: str, *, aliases: list[str] | None = None) -> SpotFact:
    return SpotFact(spot_id=spot_id, name_ja=name_ja, kind="poi", aliases_ja=aliases or [])


def _load_real_spot_catalog() -> dict[str, SpotFact]:
    """`backend/data/seeds` の実データを `SpotFact` へ読み込む。

    Docs/README.md の注意書き(文書中の spot_id 例は実データと一致しない)は
    フィクスチャの spot_id には当てはまるが、名寄せの実バグ(黄桜温泉/鳥海温泉
    の別名衝突)は実データそのものを読まないと再現・固定できないため、
    ここだけ実シードを読む(2026-08-04、レビュー是正: テストの穴)。
    """

    spots: dict[str, SpotFact] = {}
    for filename in ("POI.json", "facilities.json"):
        data = json.loads((_SEEDS_DIRECTORY / filename).read_text(encoding="utf-8"))
        for item in data:
            spot_id = item["spot_id"]
            name_ja = item["official_name"]["ja"]
            aliases_ja = item.get("aliases", {}).get("ja", [])
            spots[spot_id] = SpotFact(
                spot_id=spot_id, name_ja=name_ja, kind="poi", aliases_ja=list(aliases_ja)
            )
    return spots


def _itinerary_with(spot_id: str) -> Itinerary:
    return Itinerary(
        version=1,
        days=[
            ItineraryDay(
                date="2026-08-10",
                start_min=540,
                end_min=1020,
                origin=SpotEndpoint(spot_id=spot_id),
                destination=SpotEndpoint(spot_id=spot_id),
                items=[
                    ItineraryItem(
                        seq=1,
                        spot_id=spot_id,
                        arrive_min=600,
                        stay_min=30,
                        depart_min=630,
                        leg_from_prev=LegFromPrev(mode="car", min=10),
                    )
                ],
            )
        ],
    )


def test_normalize_name_folds_katakana_hiragana_and_whitespace() -> None:
    assert normalize_name("あがりこ  だいおう") == normalize_name("アガリコダイオウ")
    assert normalize_name("ボツメキ・湧水") == normalize_name("ぼつめき湧水")


def test_priority_current_itinerary_wins_over_last_candidates() -> None:
    """①現在の旅程内の名前は②last_candidatesより優先される。"""

    spots = {
        "spot_a": _spot("spot_a", "地点エー"),
        "spot_b": _spot("spot_b", "地点ビー"),
    }
    context = build_name_resolution_context(
        spot_catalog=spots,
        last_candidates=[CandidateReference(spot_id="spot_b", name_ja="地点エー", rank=1)],
        current_itinerary=_itinerary_with("spot_a"),
    )

    assert context.resolve("地点エー") == "spot_a"


def test_priority_last_candidates_wins_over_db_name_exact() -> None:
    """②last_candidatesは③DBのname_ja完全一致より優先される。"""

    spots = {
        "spot_a": _spot("spot_a", "地点エー"),
        "spot_b": _spot("spot_b", "地点ビー"),
    }
    context = build_name_resolution_context(
        spot_catalog=spots,
        last_candidates=[CandidateReference(spot_id="spot_b", name_ja="地点エー", rank=1)],
    )

    assert context.resolve("地点エー") == "spot_b"


def test_priority_db_name_exact_wins_over_alias() -> None:
    """③DBのname_ja完全一致は④別名より優先される。"""

    spots = {
        "spot_a": _spot("spot_a", "地点エー"),
        "spot_b": _spot("spot_b", "地点ビー", aliases=["地点エー"]),
    }
    context = build_name_resolution_context(spot_catalog=spots)

    assert context.resolve("地点エー") == "spot_a"


def test_alias_tier_resolves_via_katakana_normalization() -> None:
    """④別名・表記ゆれ: ひらがな別名にカタカナ表記で照合できる。"""

    spots = {
        "spot_a": _spot("spot_a", "あがりこ大王", aliases=["あがりこだいおう"]),
    }
    context = build_name_resolution_context(spot_catalog=spots)

    assert context.resolve("アガリコダイオウ") == "spot_a"


def test_alias_tier_resolves_via_partial_match() -> None:
    """43件規模なので正規化 + 部分一致で表記ゆれを拾う。"""

    spots = {
        "spot_a": _spot("spot_a", "ボツメキ湧水", aliases=["ボツメキ農村公園"]),
    }
    context = build_name_resolution_context(spot_catalog=spots)

    assert context.resolve("ボツメキ") == "spot_a"


def test_ambiguous_match_reports_candidates_and_is_not_resolved() -> None:
    """曖昧(複数候補に解ける)場合は resolve() は None、resolve_detailed は候補つき。"""

    spots = {
        "spot_a": _spot("spot_a", "湧水地点エー"),
        "spot_b": _spot("spot_b", "湧水地点ビー"),
    }
    context = build_name_resolution_context(spot_catalog=spots)

    match = context.resolve_detailed("湧水")
    assert match.status == "ambiguous"
    assert set(match.candidates) == {"湧水地点エー", "湧水地点ビー"}
    assert context.resolve("湧水") is None


def test_unresolved_name_has_no_candidates() -> None:
    spots = {"spot_a": _spot("spot_a", "地点エー")}
    context = build_name_resolution_context(spot_catalog=spots)

    match = context.resolve_detailed("架空スポット")
    assert match.status == "unresolved"
    assert match.candidates == ()
    assert context.resolve("架空スポット") is None


def test_resolve_names_splits_resolved_dropped_ambiguous() -> None:
    spots = {
        "spot_a": _spot("spot_a", "地点エー"),
        "spot_b": _spot("spot_b", "湧水地点ビー"),
        "spot_c": _spot("spot_c", "湧水地点シー"),
    }
    context = build_name_resolution_context(spot_catalog=spots)

    outcome = resolve_names(context, ["地点エー", "架空スポット", "湧水"])

    assert outcome.resolved == ["spot_a"]
    assert outcome.dropped == ["架空スポット"]
    assert len(outcome.ambiguous) == 1
    assert "湧水(候補: 湧水地点シー / 湧水地点ビー)" == outcome.ambiguous[0]


def test_describe_ambiguous_caps_shown_candidates() -> None:
    spots = {f"spot_{i:03d}": _spot(f"spot_{i:03d}", f"湧水地点{i}") for i in range(7)}
    context = build_name_resolution_context(spot_catalog=spots)

    match = context.resolve_detailed("湧水")
    text = describe_ambiguous("湧水", match)

    assert text.startswith("湧水(候補: ")
    assert "他2件" in text


def test_resolve_constraint_target_passes_through_unresolved_raw_tag() -> None:
    """target には生タグも書けるため、未解決はそのまま通す。"""

    spots = {"spot_a": _spot("spot_a", "地点エー")}
    context = build_name_resolution_context(spot_catalog=spots)

    assert resolve_constraint_target(context, "滝") == "滝"


def test_resolve_constraint_target_detailed_flags_ambiguous_without_resolving() -> None:
    spots = {
        "spot_a": _spot("spot_a", "湧水地点エー"),
        "spot_b": _spot("spot_b", "湧水地点ビー"),
    }
    context = build_name_resolution_context(spot_catalog=spots)

    value, match = resolve_constraint_target_detailed(context, "湧水")

    assert value == "湧水"  # 曖昧なので書き換えない
    assert match.status == "ambiguous"


# ---------------------------------------------------------------------------
# 2026-08-04 レビュー是正: スコア化した名寄せ(裁定9)
# ---------------------------------------------------------------------------


def test_official_name_partial_match_outranks_alias_exact_match() -> None:
    """正式名部分一致(単語単位)は別名完全一致より優先される(裁定9の優先順:

    正式名完全一致 > 正式名部分一致 > 別名完全一致 > 別名部分一致)。
    実データの「黄桜温泉」/「鳥海温泉」の衝突を最小フィクスチャで再現する。
    """

    spots = {
        "spot_a": _spot("spot_a", "黄桜温泉 湯楽里", aliases=["きざくらおんせんゆらり"]),
        "spot_b": _spot("spot_b", "鳥海温泉 遊楽里", aliases=["ゆらり"]),
    }
    context = build_name_resolution_context(spot_catalog=spots)

    assert context.resolve("黄桜温泉ゆらり") == "spot_a"


def test_real_seed_resolves_kizakura_onsen_yurari_to_spot_028_not_spot_030() -> None:
    """実バグの再現・固定テスト(2026-08-04、レビュー是正: High)。

    「黄桜温泉ゆらり」は spot_028(黄桜温泉 湯楽里)を指す口語表現だが、旧実装
    は部分一致を全別名・全正式名についてキー単位でしか見ておらず、
    spot_030(鳥海温泉 遊楽里)の短い別名「ゆらり」が唯一の部分一致ヒットに
    なって誤って spot_030 へ解決していた(C1 違反)。
    """

    spots = _load_real_spot_catalog()
    assert spots["spot_028"].name_ja == "黄桜温泉 湯楽里"
    assert spots["spot_030"].name_ja == "鳥海温泉 遊楽里"
    context = build_name_resolution_context(spot_catalog=spots)

    match = context.resolve_detailed("黄桜温泉ゆらり")

    assert match.status == "resolved"
    assert match.spot_id == "spot_028"


def test_real_seed_still_resolves_official_names_exactly() -> None:
    spots = _load_real_spot_catalog()
    context = build_name_resolution_context(spot_catalog=spots)

    assert context.resolve("黄桜温泉 湯楽里") == "spot_028"
    assert context.resolve("鳥海温泉 遊楽里") == "spot_030"


# ---------------------------------------------------------------------------
# 2026-08-04 レビュー是正: 序数参照(last_candidates.rank から解決。裁定9)
# ---------------------------------------------------------------------------


def test_ordinal_reference_resolves_from_last_candidates_rank() -> None:
    spots = {
        "spot_a": _spot("spot_a", "鶴間池"),
        "spot_b": _spot("spot_b", "元滝伏流水"),
    }
    context = build_name_resolution_context(
        spot_catalog=spots,
        last_candidates=[
            CandidateReference(spot_id="spot_a", name_ja="鶴間池", rank=1),
            CandidateReference(spot_id="spot_b", name_ja="元滝伏流水", rank=2),
        ],
    )

    assert context.resolve("2番目") == "spot_b"
    assert context.resolve("2番目のやつ") == "spot_b"
    assert context.resolve("1つ目") == "spot_a"
    assert context.resolve("1個目") == "spot_a"


def test_ordinal_reference_out_of_range_is_unresolved() -> None:
    spots = {"spot_a": _spot("spot_a", "鶴間池")}
    context = build_name_resolution_context(
        spot_catalog=spots,
        last_candidates=[CandidateReference(spot_id="spot_a", name_ja="鶴間池", rank=1)],
    )

    match = context.resolve_detailed("5番目")

    assert match.status == "unresolved"


def test_ordinal_reference_without_candidates_is_unresolved() -> None:
    spots = {"spot_a": _spot("spot_a", "鶴間池")}
    context = build_name_resolution_context(spot_catalog=spots)

    assert context.resolve_detailed("2番目").status == "unresolved"
