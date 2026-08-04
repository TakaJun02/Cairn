"""制約検証・revert 排他化・応答クローズドワールド・反復検知と、

`ask_user` 抑制ガード(R4・A1〜A5。`Docs/30_design/agent_react_architecture.md`
§10)の層 1 仕様。旧 P1〜P8(一括プラン検証。`planner.py`)は ReAct 化で
廃止した。旧 G1〜G9(ADR-0018 の中断・復帰方式の抑制ガード)は
`evaluate_ask_user` へ作り替えた(ADR-0019・§7)。
"""

from __future__ import annotations

from app.domains.conversation.guards import (
    ask_user_options_need_resolution_check,
    evaluate_ask_user,
    filter_unresolvable_ask_user_options,
    find_forbidden_internal_terms,
    has_repeated_ngram,
    normalize_revert_ops,
    validate_and_normalize_constraints,
    validate_response_spot_names,
)
from app.domains.conversation.state import ProfileState, SpotFact
from app.domains.conversation.types import AskUserArgs, ConstraintDraft


def _spots() -> dict[str, SpotFact]:
    return {
        f"spot_{number:03d}": SpotFact(
            spot_id=f"spot_{number:03d}",
            name_ja=f"地点{number}",
            kind="facility" if number == 1 else "poi",
            tags_ja=["滝"] if number in {2, 3} else ["自然"],
        )
        for number in range(1, 7)
    }


def _preference_args(slot: str = "pace") -> dict[str, object]:
    return {
        "kind": "preference",
        "slot": slot,
        "reason": "選好を確認します",
        "options": [
            {"label": "ゆったり", "value": "relaxed"},
            {"label": "多め", "value": "packed"},
        ],
    }


def test_repeated_ngram_detector_handles_tokens_and_no_space_text() -> None:
    assert has_repeated_ngram("同じブロックを反復します。" * 8) is True
    assert has_repeated_ngram("普通の短い JSON です") is False


def test_valid_constraint_is_normalized_with_assigned_id() -> None:
    result = validate_and_normalize_constraints(
        [ConstraintDraft(pred="require", args={"target": "spot_002"}, source_text="滝")],
        _spots(),
        created_at_version=1,
    )

    assert len(result.constraints) == 1
    assert result.constraints[0].id.startswith("c_")
    assert result.unmodeled == ()


def test_unknown_predicate_and_invalid_target_become_unmodeled() -> None:
    result = validate_and_normalize_constraints(
        [
            ConstraintDraft(pred="unknown_pred", args={}, source_text="屋台"),
            ConstraintDraft(
                pred="require", args={"target": "spot_999"}, source_text="架空"
            ),
        ],
        _spots(),
        created_at_version=1,
    )

    assert result.constraints == ()
    assert [value.text for value in result.unmodeled] == ["屋台", "架空"]
    assert all(value.reason for value in result.unmodeled)


def test_constraint_ids_do_not_collide_with_used_ids() -> None:
    result = validate_and_normalize_constraints(
        [ConstraintDraft(pred="require", args={"target": "spot_002"})],
        _spots(),
        created_at_version=1,
        used_ids={"c_001"},
    )

    assert result.constraints[0].id != "c_001"


def test_revert_requires_exclusive_ops() -> None:
    normalized, changed = normalize_revert_ops(
        [{"op": "add", "targets": ["spot_002"]}, {"op": "revert"}]
    )

    assert changed is True
    assert normalized == [{"op": "revert"}]

    normalized_alone, changed_alone = normalize_revert_ops([{"op": "revert"}])
    assert changed_alone is False
    assert normalized_alone == [{"op": "revert"}]


def test_validate_response_spot_names_rejects_unpresented_names() -> None:
    all_names = {"spot_001": "鶴間池", "spot_002": "元滝伏流水"}

    accepted = validate_response_spot_names(
        "鶴間池をご案内します。",
        all_spot_names=all_names,
        allowed_spot_ids={"spot_001"},
    )
    rejected = validate_response_spot_names(
        "元滝伏流水も良いですよ。",
        all_spot_names=all_names,
        allowed_spot_ids={"spot_001"},
    )

    assert accepted.accepted is True
    assert rejected.accepted is False
    assert rejected.rule == "closed_world_response"


def test_r4_and_a1_a2_a3_reject_preference_questions() -> None:
    base = AskUserArgs.model_validate(_preference_args())

    assert evaluate_ask_user(
        base, ask_user_count=2, ask_streak=0, asked_slots=[]
    ).rule == "R4"
    assert evaluate_ask_user(
        base, ask_user_count=0, ask_streak=2, asked_slots=[]
    ).rule == "A2"
    assert evaluate_ask_user(
        base, ask_user_count=0, ask_streak=0, asked_slots=["pace"]
    ).rule == "A1"
    one_option = _preference_args()
    one_option["options"] = [{"label": "1つだけ", "value": "one"}]
    assert evaluate_ask_user(
        AskUserArgs.model_validate(one_option),
        ask_user_count=0,
        ask_streak=0,
        asked_slots=[],
    ).rule == "A3"
    # 通常時は受理される。
    assert evaluate_ask_user(
        base, ask_user_count=0, ask_streak=0, asked_slots=[]
    ).accepted is True


def test_a6_rejects_preference_question_when_profile_already_has_the_slot() -> None:
    """裁定17(2026-08-04レビュー是正): A6 の最小コード判定。

    `profile` を渡したときだけ検査する(渡さない呼び出し元は従来どおり)。
    `dates`/`origin`/`onboarding` は永続プロフィールに対応する列が無いため
    常に許可する。
    """

    common = {"ask_user_count": 0, "ask_streak": 0, "asked_slots": []}

    mobility_known = ProfileState(mobility="short_walk_ok")
    assert evaluate_ask_user(
        AskUserArgs.model_validate(_preference_args("mobility")),
        **common,
        profile=mobility_known,
    ).rule == "A6"

    party_known = ProfileState(party="family_kids")
    assert evaluate_ask_user(
        AskUserArgs.model_validate(_preference_args("party")),
        **common,
        profile=party_known,
    ).rule == "A6"

    interests_known = ProfileState(interests={"water": 0.5})
    assert evaluate_ask_user(
        AskUserArgs.model_validate(_preference_args("interests")),
        **common,
        profile=interests_known,
    ).rule == "A6"

    # 未知(値が無い)なら通常どおり受理される。
    assert evaluate_ask_user(
        AskUserArgs.model_validate(_preference_args("mobility")),
        **common,
        profile=ProfileState(),
    ).accepted is True

    # profile を渡さない呼び出し元は A6 を検査しない(後方互換)。
    assert evaluate_ask_user(
        AskUserArgs.model_validate(_preference_args("mobility")), **common
    ).accepted is True

    # dates/origin/onboarding は永続プロフィールに列が無いため常に許可する。
    assert evaluate_ask_user(
        AskUserArgs.model_validate(_preference_args("dates")),
        **common,
        profile=ProfileState(mobility="short_walk_ok", party="family_kids"),
    ).accepted is True


def _clarification(*, invalid_value: str | None = None) -> AskUserArgs:
    return AskUserArgs.model_validate(
        {
            "kind": "clarify",
            "surface": "2番目",
            "reason": "候補が複数あります",
            "options": [
                {
                    "label": f"地点{index}",
                    "value": (
                        invalid_value
                        if index == 2 and invalid_value
                        else f"spot_{index:03d}"
                    ),
                }
                for index in range(1, 3)
            ],
        }
    )


def test_a4_and_a5_apply_to_clarify_only() -> None:
    existing = {"spot_001", "spot_002", "spot_003", "spot_004", "spot_005"}
    common = {"ask_user_count": 0, "ask_streak": 0, "asked_slots": []}

    assert evaluate_ask_user(
        _clarification(invalid_value="spot_999"),
        **common,
        allowed_spot_ids=existing,
        existing_spot_ids=existing,
    ).rule == "A4"
    assert evaluate_ask_user(
        _clarification(),
        **common,
        resolved_ambiguities=[{"surface": "2番目"}],
    ).rule == "A5"
    # allowed/existing_spot_ids を渡さない呼び出し元(recommend SA・知識検索
    # SA)では A4 を検査しない(値の意味が spot_id とは限らないため)。
    assert evaluate_ask_user(
        _clarification(invalid_value="not-a-spot-id"), **common
    ).accepted is True
    # 正常系は受理される。
    assert evaluate_ask_user(
        _clarification(), **common, allowed_spot_ids=existing, existing_spot_ids=existing
    ).accepted is True


# ---------------------------------------------------------------------------
# A7(dialogue_style.md §3 論点 C・2026-08-04 決定): ask_user 選択肢の
# 送出前の名寄せ検査(解決不能な選択肢の除去のみ。質問ごとの差し戻しはしない)。
# ---------------------------------------------------------------------------


def _origin_question(options: list[dict[str, str]]) -> AskUserArgs:
    return AskUserArgs.model_validate(
        {
            "kind": "preference",
            "slot": "origin",
            "reason": "どこから出発しますか",
            "options": options,
        }
    )


def test_ask_user_options_need_resolution_check_targets_clarify_and_origin_only() -> None:
    clarify = _clarification()
    origin = _origin_question(
        [
            {"label": "道の駅象潟", "value": "道の駅象潟"},
            {"label": "にかほ市役所", "value": "にかほ市役所"},
        ]
    )
    mobility = AskUserArgs.model_validate(_preference_args("mobility"))
    interests = AskUserArgs.model_validate(_preference_args("interests"))

    assert ask_user_options_need_resolution_check(clarify) is True
    assert ask_user_options_need_resolution_check(origin) is True
    # 選好 enum(mobility/interests 等。値が avoid_walk・nature のような
    # 固定語彙)は検査対象外(dialogue_style.md §5 実装方針の注記どおり)。
    assert ask_user_options_need_resolution_check(mobility) is False
    assert ask_user_options_need_resolution_check(interests) is False


def test_filter_unresolvable_ask_user_options_drops_only_the_unresolvable_ones() -> None:
    """実測(known_issues §6-2)の再現: 起点の選択肢に解決不能なカテゴリ語が

    混ざっていても、解決できる施設名だけを残して送出できる。
    """

    question = _origin_question(
        [
            {"label": "道の駅象潟", "value": "道の駅象潟"},
            {"label": "鳥海山麓の宿", "value": "鳥海山麓の宿"},
            {"label": "その他", "value": "その他"},
        ]
    )

    filtered, removed = filter_unresolvable_ask_user_options(
        question, is_resolvable=lambda value: value == "道の駅象潟"
    )

    assert [option.value for option in filtered.options] == ["道の駅象潟"]
    assert removed == ["鳥海山麓の宿", "その他"]


def test_filter_unresolvable_ask_user_options_keeps_all_when_all_resolve() -> None:
    question = _origin_question(
        [
            {"label": "道の駅象潟", "value": "道の駅象潟"},
            {"label": "にかほ市役所", "value": "にかほ市役所"},
        ]
    )

    filtered, removed = filter_unresolvable_ask_user_options(
        question, is_resolvable=lambda value: True
    )

    assert filtered.options == question.options
    assert removed == []


def test_filter_unresolvable_ask_user_options_skips_preference_enum_slots() -> None:
    """選好 enum の選択肢(interests 等)は名寄せ対象外(解決不要の値)。"""

    question = AskUserArgs.model_validate(_preference_args("mobility"))

    filtered, removed = filter_unresolvable_ask_user_options(
        question, is_resolvable=lambda value: False
    )

    assert filtered is question
    assert removed == []


def test_filter_unresolvable_ask_user_options_applies_to_clarify() -> None:
    question = _clarification(invalid_value="鳥海山麓のどこか")

    filtered, removed = filter_unresolvable_ask_user_options(
        question, is_resolvable=lambda value: value.startswith("spot_")
    )

    assert [option.value for option in filtered.options] == ["spot_001"]
    assert removed == ["鳥海山麓のどこか"]


# ---------------------------------------------------------------------------
# find_forbidden_internal_terms(dialogue_style.md §3 論点 E・§4「必須規則」)
# ---------------------------------------------------------------------------


def test_find_forbidden_internal_terms_detects_enum_leak_and_spot_id_and_self_reference() -> None:
    assert find_forbidden_internal_terms(
        "移動手段の制限(mobility)は指定せずに抽出しました。"
    ) == ["mobility"]
    assert find_forbidden_internal_terms("spot_017 を旅程に追加しました。") == ["spot_id"]
    # L-1(2026-08-04 レビュー是正): 「を実施しました」単独は禁止語から削除
    # したので、複合語「検索を実施」だけが検出される。
    assert find_forbidden_internal_terms("ナレッジ検索を実施しました。") == ["検索を実施"]


def test_find_forbidden_internal_terms_accepts_clean_japanese_text() -> None:
    assert find_forbidden_internal_terms(
        "鶴間池は美しい湖沼です。次に丸池様もご覧になりますか?"
    ) == []


def test_find_forbidden_internal_terms_does_not_flag_legitimate_use_of_jisshi() -> None:
    """L-1 の回帰: 「実施しました」を含む正当な文(例大祭など)は誤検知しない。"""

    assert find_forbidden_internal_terms(
        "毎年 8 月に例大祭を実施しました。"
    ) == []


def test_find_forbidden_internal_terms_detects_preference_key_enum_values() -> None:
    """M-2 の回帰: `PreferenceKey`(interests のキー語彙)の英語コードも検出する。

    ASCII 単語境界つきで検出するため、英語圏の固有名詞への部分一致
    (「Watergate」等)では誤検知しない。
    """

    assert find_forbidden_internal_terms(
        "interests は nature と water を中心に抽出しました。"
    ) == ["interests", "nature", "water"]
    # ASCII 単語境界つきの部分一致で誤検知しないこと(「water」は
    # 「underwater」のような英語圏の複合語の一部に当たり得る)。
    assert find_forbidden_internal_terms("underwaterな景色が楽しめます。") == []
    # 日本語に直接続く場合でも検出できること(spot_id と同じ理由)。
    assert find_forbidden_internal_terms("natureを中心に選びました。") == ["nature"]


def test_find_forbidden_internal_terms_detects_spot_id_immediately_after_japanese() -> None:
    """L-2 の回帰: 日本語に直接続く spot_id も検出する(`\\b` は Unicode 対応の

    Python 正規表現では日本語文字も単語構成文字とみなすため、
    「地点spot_017を」のような直前が日本語の場合に検出漏れがあった。
    """

    assert find_forbidden_internal_terms("地点spot_017を旅程に追加しました。") == ["spot_id"]
