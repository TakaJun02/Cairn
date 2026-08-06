"""`Docs/30_design/dialogue_style.md` 決定稿(2026-08-04)の層 1 様式テスト。

対象: `RESPOND_SYSTEM_PROMPT`(§4)・respond 入力の「⑤ 素材」節(§4・論点 A2)・
禁止語(内部語・自己言及)の事後検査(§3 論点 E)。既存のクローズドワールド
検査(`test_conversation_respond.py`)はそのまま維持し、本ファイルでは
崩さないことだけ最後に 1 件確認する((c))。
"""

from __future__ import annotations

from typing import Any

from app.domains.conversation.events import MemoryEventSink
from app.domains.conversation.guards import find_forbidden_internal_terms
from app.domains.conversation.prompts import (
    RESPOND_MATERIALS_MAX_SPOTS,
    RESPOND_SYSTEM_PROMPT,
    SpotMaterial,
    build_respond_messages,
    format_materials_section,
)
from app.domains.conversation.respond import (
    _load_spot_materials,
    _load_spot_materials_safely,
    _presented_material_spot_ids,
    respond,
)
from app.domains.conversation.state import ProfileState, TurnState
from app.domains.conversation.types import ResponseMode, ToolName, ToolResult


class _RecordingClient:
    """respond に渡る最終メッセージを記録するだけの `generate` 専用モック。"""

    def __init__(self, text: str) -> None:
        self.text = text
        self.captured_messages: list[dict[str, str]] | None = None

    async def generate(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        del kwargs
        self.captured_messages = list(messages)
        return self.text


def _recommend_result(spot_ids: list[str], *, step_id: int = 1) -> ToolResult:
    return ToolResult(
        step_id=step_id,
        tool=ToolName.RECOMMEND,
        data={
            "spot_ids": spot_ids,
            "candidates": [
                {"spot_id": spot_id, "rank": index, "reason_materials": {}}
                for index, spot_id in enumerate(spot_ids, start=1)
            ],
            "provisional_spot_ids": spot_ids,
            "rerank_used": False,
        },
    )


# ---------------------------------------------------------------------------
# (a) respond プロンプトが次の一手の質問を要求していること /
#     応答末尾に関する検査が可能な形での入力テスト
# ---------------------------------------------------------------------------


def test_respond_system_prompt_requires_a_next_step_question() -> None:
    """dialogue_style.md §4 の必須要素 4: 最後に必ず次の一手を質問で提案する。"""

    assert "最後に必ず" in RESPOND_SYSTEM_PROMPT
    assert "質問" in RESPOND_SYSTEM_PROMPT
    assert "提案します" in RESPOND_SYSTEM_PROMPT
    # ガイドペルソナ・様式(絵文字は控えめに可・Markdown 許可)も併せて明文化
    # されていること。
    assert "ツアーガイド" in RESPOND_SYSTEM_PROMPT
    assert "絵文字" in RESPOND_SYSTEM_PROMPT
    assert "内部の実装語を出しません" in RESPOND_SYSTEM_PROMPT


def test_respond_messages_end_with_the_materials_section_after_the_utterance() -> None:
    """入力側の構造テスト: ④ ユーザーの発話 → ⑤ 素材の順で末尾に来る。

    末尾が固定の節見出しになることで、「応答(この場合は respond への入力)の
    末尾」を機械的に検査できる形になっている(次の一手の実際の生成内容は
    LLM 出力なので、生成パラメータ側で強制はできない — 主防御は上のプロンプト
    テストと実機確認)。
    """

    state = TurnState(
        turn_id="turn-shape",
        thread_id=1,
        user_id=1,
        utterance="おすすめを教えて",
        profile=ProfileState(),
    )

    dynamic = build_respond_messages(state, mode=ResponseMode.EXPLANATION)[1]["content"]

    assert dynamic.index("④ ユーザーの発話") < dynamic.index("⑤ 素材")
    assert dynamic.endswith("⑤ 素材(このターンで提示したスポットの説明):\n(なし)")


# ---------------------------------------------------------------------------
# (b) 禁止語(mobility 等の enum、spot_、「を実施しました」等)の不在を
#     検査する仕組み
# ---------------------------------------------------------------------------


async def test_respond_flags_forbidden_internal_terms_in_generated_text() -> None:
    """`find_forbidden_internal_terms` が respond の後処理として実際に働く。

    ストリーミング済みのトークンを遡って書き換えることはできないため、
    クローズドワールド検査(`response_closed_world_violation`)と同じ位置
    づけで `state.degraded` に記録し、`error` イベントを送出する。
    """

    state = TurnState(
        turn_id="turn-forbidden",
        thread_id=1,
        user_id=1,
        utterance="歩くのが苦手です",
        profile=ProfileState(),
    )
    sink = MemoryEventSink()
    client = _RecordingClient("移動手段の制限(mobility)は考慮しませんでした。")

    await respond(state, client=client, event_sink=sink)

    assert "response_forbidden_term" in [value.code for value in state.degraded]
    error_events = [event for event in sink.events if event.event == "error"]
    assert any(event.data.get("code") == "response_forbidden_term" for event in error_events)


async def test_respond_does_not_flag_clean_text() -> None:
    state = TurnState(
        turn_id="turn-clean",
        thread_id=1,
        user_id=1,
        utterance="おすすめを教えて",
        profile=ProfileState(),
    )
    client = _RecordingClient(
        "鶴間池をおすすめします。水辺が美しい場所です。他の候補もご覧になりますか?"
    )

    await respond(state, client=client, event_sink=None)

    assert find_forbidden_internal_terms(state.assistant_text) == []
    assert "response_forbidden_term" not in [value.code for value in state.degraded]


# ---------------------------------------------------------------------------
# (c) 既存クローズドワールド検査の維持(回帰確認: 禁止語検査を追加しても、
#     既存のクローズドワールド検査は独立に働き続ける)
# ---------------------------------------------------------------------------


async def test_forbidden_term_check_does_not_interfere_with_closed_world_check() -> None:
    """禁止語を含まないが未提示 POI 名を含む応答は、従来どおり closed-world

    違反として検知され、`response_forbidden_term` は付かない(2 つの検査は
    独立に働く)。
    """

    state = TurnState(
        turn_id="turn-closed-world-only",
        thread_id=1,
        user_id=1,
        utterance="おすすめを教えて",
        profile=ProfileState(),
        spot_names={"spot_hidden": "架空の滝"},
    )
    client = _RecordingClient("架空の滝もおすすめですよ。")

    await respond(state, client=client, event_sink=None)

    assert [value.code for value in state.degraded] == ["response_closed_world_violation"]


# ---------------------------------------------------------------------------
# (d) ⑤ 素材節が提示スポットに限定されること
# ---------------------------------------------------------------------------


def test_presented_material_spot_ids_are_limited_to_this_turns_candidates() -> None:
    state = TurnState(
        turn_id="turn-materials-candidates",
        thread_id=1,
        user_id=1,
        utterance="",
        profile=ProfileState(),
        step_results={1: _recommend_result(["spot_a", "spot_b", "spot_c"])},
    )

    assert _presented_material_spot_ids(state) == ["spot_a", "spot_b", "spot_c"]


def test_presented_material_spot_ids_include_new_plan_itinerary_items() -> None:
    itinerary = {"days": [{"items": [{"spot_id": "spot_p1"}, {"spot_id": "spot_p2"}]}]}
    state = TurnState(
        turn_id="turn-materials-plan",
        thread_id=1,
        user_id=1,
        utterance="",
        profile=ProfileState(),
        step_results={
            1: ToolResult(step_id=1, tool=ToolName.PLAN_ITINERARY, data={"itinerary": itinerary})
        },
    )

    assert _presented_material_spot_ids(state) == ["spot_p1", "spot_p2"]


def test_presented_material_spot_ids_use_only_added_diff_for_edit_itinerary() -> None:
    state = TurnState(
        turn_id="turn-materials-edit",
        thread_id=1,
        user_id=1,
        utterance="",
        profile=ProfileState(),
        step_results={
            1: ToolResult(
                step_id=1,
                tool=ToolName.EDIT_ITINERARY,
                data={
                    "diff": {
                        "added": ["spot_add"],
                        "removed": ["spot_rm"],
                        "moved": [{"spot_id": "spot_mv"}],
                        "retimed": ["spot_rt"],
                    }
                },
            )
        },
    )

    assert _presented_material_spot_ids(state) == ["spot_add"]


def test_presented_material_spot_ids_prioritize_candidates_and_cap_at_eight() -> None:
    candidate_ids = [f"spot_c{index}" for index in range(3)]
    itinerary_ids = [f"spot_i{index}" for index in range(10)]
    state = TurnState(
        turn_id="turn-materials-cap",
        thread_id=1,
        user_id=1,
        utterance="",
        profile=ProfileState(),
        step_results={
            1: _recommend_result(candidate_ids),
            2: ToolResult(
                step_id=2,
                tool=ToolName.EDIT_ITINERARY,
                data={
                    "diff": {
                        "added": itinerary_ids,
                        "removed": [],
                        "moved": [],
                        "retimed": [],
                    }
                },
            ),
        },
    )

    result = _presented_material_spot_ids(state)

    assert len(result) == RESPOND_MATERIALS_MAX_SPOTS == 8
    assert result == candidate_ids + itinerary_ids[: RESPOND_MATERIALS_MAX_SPOTS - 3]


async def test_respond_materials_section_excludes_unpresented_spots() -> None:
    """respond の実入力(LLM へ渡すメッセージ)を検査し、提示していない

    スポット(spot_x)の素材が漏れ出していないことを確認する。
    """

    state = TurnState(
        turn_id="turn-materials-e2e",
        thread_id=1,
        user_id=1,
        utterance="おすすめを教えて",
        profile=ProfileState(),
        spot_names={
            "spot_a": "地点A",
            "spot_b": "地点B",
            "spot_c": "地点C",
            "spot_x": "地点X",
        },
        step_results={1: _recommend_result(["spot_a", "spot_b", "spot_c"])},
    )
    materials = {
        "spot_a": SpotMaterial(description="Aの説明文", tags_ja=("自然",)),
        "spot_b": SpotMaterial(description="Bの説明文", social_proof="人気です"),
        "spot_c": SpotMaterial(description="Cの説明文"),
        # 未提示スポット。提供元(DB)にはデータがあっても respond の入力には
        # 出てはいけない。
        "spot_x": SpotMaterial(description="Xの説明文(見えてはいけない)"),
    }

    async def provider(spot_ids: list[str]) -> dict[str, SpotMaterial]:
        return {spot_id: materials[spot_id] for spot_id in spot_ids if spot_id in materials}

    client = _RecordingClient("地点A・地点B・地点Cをおすすめします。")

    await respond(state, client=client, spot_materials_provider=provider)

    assert client.captured_messages is not None
    content = client.captured_messages[1]["content"]
    assert "Aの説明文" in content
    assert "Bの説明文" in content
    assert "人気です" in content
    assert "Cの説明文" in content
    assert "Xの説明文" not in content
    assert "地点X" not in content


def test_format_materials_section_renders_the_documented_line_shape() -> None:
    materials = {
        "spot_a": SpotMaterial(
            description="湧水が美しい池です。",
            social_proof="地元で人気です。",
            tags_ja=("水辺", "自然"),
        )
    }

    section = format_materials_section(["spot_a"], materials, {"spot_a": "丸池様"})

    assert section == "- 丸池様: 湧水が美しい池です。 / 地元で人気です。 / タグ: 水辺、自然"


def test_format_materials_section_is_none_when_no_spots_presented() -> None:
    assert format_materials_section([], {}, {}) == "(なし)"


def test_format_materials_section_skips_spots_missing_from_spot_names() -> None:
    """L-6 の回帰: `spot_names` に無い spot_id は行ごと省き、生の spot_id を

    respond の入力に出さない(是正前は `spot_names.get(spot_id, spot_id)` で
    未知の spot_id をそのまま名前欄に出していた)。
    """

    materials = {"spot_known": SpotMaterial(description="説明文")}

    section = format_materials_section(
        ["spot_known", "spot_unknown"], materials, {"spot_known": "既知スポット"}
    )

    assert section == "- 既知スポット: 説明文"
    assert "spot_unknown" not in section


def test_format_materials_section_is_none_when_all_spots_are_unknown() -> None:
    assert format_materials_section(["spot_unknown"], {}, {}) == "(なし)"


# ---------------------------------------------------------------------------
# H-1(2026-08-04 レビュー是正): ⑤ 素材の本文に出る他スポット名も
# closed-world 許可集合に含める
# ---------------------------------------------------------------------------


async def test_material_description_mentioning_another_spot_is_allowed_in_response() -> None:
    """H-1 の回帰: 素材本文(description)に他スポット名が出るケース(実データ:

    spot_010「奈曽の白滝」の description に「金峰神社」が含まれる等、43 件中
    4 件で発生)で、respond がその名前をそのまま使っても closed-world 違反に
    ならないこと。是正前は `_allowed_spot_ids` が軌跡本文(`trajectory_text`)
    しか見ておらず、素材本文だけに出るスポット名は許可集合に入らなかった。
    """

    state = TurnState(
        turn_id="turn-material-mentions-other-spot",
        thread_id=1,
        user_id=1,
        utterance="奈曽の白滝について教えて",
        profile=ProfileState(),
        spot_names={"spot_010": "奈曽の白滝", "spot_shrine": "金峰神社"},
        step_results={1: _recommend_result(["spot_010"])},
    )
    materials = {
        "spot_010": SpotMaterial(
            description="日本の滝百選にも選ばれた名瀑で、近くには金峰神社があります。",
        ),
    }

    async def provider(spot_ids: list[str]) -> dict[str, SpotMaterial]:
        return {spot_id: materials[spot_id] for spot_id in spot_ids if spot_id in materials}

    client = _RecordingClient(
        "奈曽の白滝は日本の滝百選の名瀑です。近くの金峰神社もあわせてどうぞ。"
    )

    await respond(state, client=client, spot_materials_provider=provider)

    assert state.degraded == []


# ---------------------------------------------------------------------------
# H-2(2026-08-04 レビュー是正): 素材取得の DB 例外を局所縮退する
# ---------------------------------------------------------------------------


async def test_load_spot_materials_returns_empty_without_settings_or_provider() -> None:
    """`_load_spot_materials`: settings も provider も無ければ DB に触れず

    {} を返す(NFR-5)。提示スポットがある(spot_ids が空でない)場合でも
    この縮退経路になることを確認する。
    """

    materials = await _load_spot_materials(["spot_a", "spot_b"], settings=None, provider=None)

    assert materials == {}


async def test_load_spot_materials_safely_degrades_on_provider_exception() -> None:
    """H-2 の回帰: provider が例外を送出しても `{}` へ縮退し、

    `DegradedState(code="respond_materials_unavailable")` を記録する。
    是正前は `_load_spot_materials` の呼び出しが `respond` の
    `try/asyncio.timeout` の外にあり、`session_scope` 由来の例外
    (DB 接続プール枯渇・接続断)がそのまま `respond` を貫通してターンが
    丸ごと落ちていた。
    """

    state = TurnState(
        turn_id="turn-materials-db-error",
        thread_id=1,
        user_id=1,
        utterance="",
        profile=ProfileState(),
    )

    async def failing_provider(spot_ids: list[str]) -> dict[str, SpotMaterial]:
        del spot_ids
        raise RuntimeError("connection pool exhausted")

    materials = await _load_spot_materials_safely(
        ["spot_a"],
        settings=None,
        provider=failing_provider,
        state=state,
        event_sink=None,
    )

    assert materials == {}
    assert [value.code for value in state.degraded] == ["respond_materials_unavailable"]


async def test_respond_completes_when_material_provider_fails() -> None:
    """H-2 の end-to-end 確認: 素材取得が例外でも respond はターンを完走させる

    (assistant_text を生成し、素材取得の失敗だけが degraded に記録される)。
    """

    state = TurnState(
        turn_id="turn-materials-db-error-e2e",
        thread_id=1,
        user_id=1,
        utterance="おすすめを教えて",
        profile=ProfileState(),
        spot_names={"spot_a": "地点A"},
        step_results={1: _recommend_result(["spot_a"])},
    )

    async def failing_provider(spot_ids: list[str]) -> dict[str, SpotMaterial]:
        del spot_ids
        raise RuntimeError("connection pool exhausted")

    client = _RecordingClient("地点Aをおすすめします。")

    await respond(state, client=client, spot_materials_provider=failing_provider)

    assert state.respond_status == "complete"
    assert "respond_materials_unavailable" in [value.code for value in state.degraded]
