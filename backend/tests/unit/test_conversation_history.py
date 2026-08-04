"""会話履歴の 4 層ビルダー（`Docs/30_design/agent_react_architecture.md` §8）。

① threads.history_summary + ② 機械要約(未畳み込みターン) + ③ 候補提示リストの
機械要約(直近3リストまで) + ④ 直近2ターンの生テキスト、という構成と、
予算超過時に②③の古い方から落ちること・④は絶対に落ちないことを検査する。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.domains.conversation.history import (
    build_conversation_history,
    first_unfolded_turn_index,
    group_turns,
)
from app.domains.conversation.state import MessageState


def _message(
    id_: int,
    role: str,
    content: str,
    meta: dict[str, Any] | None = None,
) -> MessageState:
    return MessageState(
        id=id_,
        seq=id_,
        role=role,
        content=content,
        status="complete",
        meta=meta or {},
        created_at=datetime(2026, 8, 4, tzinfo=UTC),
    )


def test_group_turns_falls_back_to_a_new_turn_per_user_message_without_turn_id() -> None:
    """`meta.turn_id` が無い(移行前の)行は旧ヒューリスティックにフォールバック

    する(2026-08-04、レビュー是正・裁定11で `turn_id` ベースへ変更した際の
    後方互換パス)。
    """

    messages = [
        _message(1, "user", "u1"),
        _message(2, "assistant", "a1"),
        _message(3, "user", "u2"),
        _message(4, "assistant", "a2-1"),
        _message(5, "assistant", "a2-2"),
    ]

    turns = group_turns(messages)

    assert [turn.index for turn in turns] == [0, 1]
    assert [message.id for message in turns[1].messages] == [3, 4, 5]
    assert turns[1].last_message_id == 5


def test_group_turns_keeps_same_turn_id_rows_in_one_turn() -> None:
    """裁定11(2026-08-04レビュー是正): `ask_user` への回答は同一ターン内で

    複数の user 行として persist される(`repository.py:persist_turn`)。
    同じ `meta.turn_id` を持つ行はすべて同一ターンにまとまる。
    """

    messages = [
        _message(1, "user", "おすすめは？", {"turn_id": "t1"}),
        _message(2, "user", "30分程度なら", {"turn_id": "t1", "answer_to": {"reason": "歩けますか"}}),
        _message(3, "assistant", "ご案内します", {"turn_id": "t1"}),
        _message(4, "user", "1日目に入れて", {"turn_id": "t2"}),
        _message(5, "assistant", "入れました", {"turn_id": "t2"}),
    ]

    turns = group_turns(messages)

    assert [turn.index for turn in turns] == [0, 1]
    assert [message.id for message in turns[0].messages] == [1, 2, 3]
    assert [message.id for message in turns[1].messages] == [4, 5]


def test_group_turns_starts_a_new_turn_when_turn_id_changes_from_legacy_rows() -> None:
    """移行境界: turn_id 無し行の直後に turn_id 付き行が来たら新ターンにする。"""

    messages = [
        _message(1, "user", "旧形式の発話"),
        _message(2, "assistant", "旧形式の応答"),
        _message(3, "user", "新形式の発話", {"turn_id": "t1"}),
        _message(4, "assistant", "新形式の応答", {"turn_id": "t1"}),
    ]

    turns = group_turns(messages)

    assert [turn.index for turn in turns] == [0, 1]
    assert [message.id for message in turns[0].messages] == [1, 2]
    assert [message.id for message in turns[1].messages] == [3, 4]


def test_raw_layer_includes_the_question_text_for_ask_user_answer_rows() -> None:
    """裁定11: ④(直近生テキスト)は `ask_user` への回答行に質問文も出す。

    回答単独(例: 「30分程度なら」)では何を聞かれたか分からず、指示語も
    解けない。
    """

    messages = [
        _message(1, "user", "おすすめは？", {"turn_id": "t1"}),
        _message(
            2,
            "user",
            "30分程度なら",
            {"turn_id": "t1", "answer_to": {"reason": "どのくらい歩けますか"}},
        ),
        _message(3, "assistant", "ご案内します", {"turn_id": "t1"}),
    ]

    built = build_conversation_history(messages)

    assert "u: [質問: どのくらい歩けますか] → 回答: 30分程度なら" in built.text
    assert "u: おすすめは？" in built.text


def test_first_unfolded_turn_index_skips_turns_already_in_the_summary() -> None:
    messages = [
        _message(1, "user", "u1"),
        _message(2, "assistant", "a1"),
        _message(3, "user", "u2"),
        _message(4, "assistant", "a2"),
    ]
    turns = group_turns(messages)

    assert first_unfolded_turn_index(turns, None) == 0
    assert first_unfolded_turn_index(turns, 2) == 1
    assert first_unfolded_turn_index(turns, 4) == 2


def test_four_layers_are_assembled_with_headings_in_order() -> None:
    messages = [
        # ① へ畳み込み済み（summarized_until_message_id=2）。
        _message(1, "user", "鶴間池について知りたい"),
        _message(2, "assistant", "紹介しました", {"candidate_names": ["鶴間池"]}),
        # ② 要約未反映（生層より前）。
        _message(3, "user", "他にもある？"),
        _message(
            4,
            "assistant",
            "とても長い推薦の説明文" * 20,
            {"candidate_names": ["元滝伏流水", "奈曽の白滝"]},
        ),
        # ④ 直近2ターンは生テキストのまま。
        _message(5, "user", "1日目に元滝を入れて"),
        _message(6, "assistant", "旅程へ入れました", {"itinerary_version": 2}),
        _message(7, "user", "昼休憩も入れて"),
        _message(8, "assistant", "昼休憩を入れました"),
    ]

    built = build_conversation_history(
        messages,
        history_summary="鶴間池に関心があると発言した。",
        summarized_until_message_id=2,
    )

    assert "これまでの要約:\n鶴間池に関心があると発言した。" in built.text
    assert "その後の出来事" in built.text
    assert "[推薦2件: 元滝伏流水 / 奈曽の白滝]" in built.text
    assert "とても長い推薦の説明文" not in built.text  # ②は生本文を持たない
    assert "これまでに提示した候補:" in built.text
    assert "u: 1日目に元滝を入れて" in built.text
    assert "a: 旅程へ入れました" in built.text
    assert "u: 昼休憩も入れて" in built.text
    assert "a: 昼休憩を入れました" in built.text
    assert built.raw_turns == 2
    assert built.summarized_turns == 1
    assert "spot_001" not in built.mentioned_spot_ids  # メタに無ければ出ない

    # ①②③④ の並び順が保たれている。
    assert (
        built.text.index("これまでの要約")
        < built.text.index("その後の出来事")
        < built.text.index("これまでに提示した候補")
        < built.text.index("直近のやり取り")
    )


def test_candidate_lists_are_capped_at_three_and_oldest_drops_out_entirely() -> None:
    """④(直近2ターン)より前の候補は直近3リストまで。4件目より古いものは消える。"""

    messages = [
        item
        for index, name in enumerate(["名所A", "名所B", "名所C", "名所D", "名所E"])
        for item in (
            _message(index * 2 + 1, "user", f"発話{index}"),
            _message(
                index * 2 + 2,
                "assistant",
                f"応答{index}",
                {"candidate_names": [name]},
            ),
        )
    ]

    built = build_conversation_history(
        messages,
        history_summary="以前の会話の要約。",
        # turn0（名所A）はすでに①へ畳み込み済みとして扱う。
        summarized_until_message_id=2,
    )

    assert built.candidate_lists == 3
    # 名所A は①(要約文に含まれない)・②(fold_start より前)・
    # ③(直近3件からあふれる)・④(生層ではない) のどこにも現れない。
    assert "名所A" not in built.text
    assert "名所B" in built.text  # ②の機械要約としては残る
    assert "名所C" in built.text
    assert "名所D" in built.text
    assert "名所E" in built.text


def test_budget_overflow_drops_layer2_and_candidates_oldest_first_but_keeps_raw() -> None:
    messages = [
        item
        for turn in range(8)
        for item in (
            _message(turn * 2 + 1, "user", f"古い発話{turn}" * 20),
            _message(
                turn * 2 + 2,
                "assistant",
                f"応答{turn}" * 20,
                {"tools": ["recommend"]},
            ),
        )
    ]

    built = build_conversation_history(
        messages,
        history_summary="要約本体。",
        max_tokens=150,
        token_counter=len,
    )

    assert built.dropped_sections > 0
    # ④ 直近2ターンの生テキストはどれだけ予算が厳しくても残る。
    assert "古い発話6" in built.text
    assert "応答6" in built.text
    assert "古い発話7" in built.text
    assert "応答7" in built.text
    # ① 要約はここでは切り詰めの対象にしない。
    assert "要約本体。" in built.text
