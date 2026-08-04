"""④ `respond`: 共通テンプレート 1 本による日本語ストリーミング。

`Docs/30_design/agent_react_architecture.md` §3.4 が仕様。入力はこのターンの
軌跡(手と結果)+ 譲歩・縮退 + 会話履歴 + ⑤ 素材(`Docs/30_design/
dialogue_style.md` §4)。`done` はループの終了宣言であり応答文を持たない
ため、ユーザー向け本文はここで別呼び出しとして書く。

旧 respond の「モード」分岐(`QUESTION` = ask_user 用)は、メインループが
段2で `ask_user` を持たなくなったため不要になった(段5で復活しうる)。

⑤ 素材は、このターンで提示したスポット(候補 + 旅程 diff 追加分。
`_presented_material_spot_ids`)の `spot_id` から `static.spots` の
`description`/`social_proof`/`tags_ja` を読み取り専用クエリで引く
(`_default_spot_materials_provider`)。**素材は respond の入力にだけ足す**
(dialogue_style.md 論点 A2: ツールのダイジェスト/メインループのコンテキスト
には足さない)。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from typing import Any, Protocol, runtime_checkable

from sqlalchemy import select

from app.core.config import Settings
from app.core.db import session_scope
from app.core.llm import GenerationClient
from app.db_models import Spot
from app.domains.conversation.events import EventSinkLike, emit, error_event, token_event
from app.domains.conversation.guards import (
    find_forbidden_internal_terms,
    has_repeated_ngram,
    validate_response_spot_names,
)
from app.domains.conversation.prompts import (
    RESPOND_MATERIALS_MAX_SPOTS,
    SpotMaterial,
    build_respond_messages,
    format_materials_section,
    trajectory_text,
)
from app.domains.conversation.state import DegradedState, TurnState
from app.domains.conversation.types import ResponseMode, ToolName

RESPOND_EXPECTED_TOKENS = 1200
RESPOND_MAX_TOKENS = int(RESPOND_EXPECTED_TOKENS * 1.5)
RESPOND_WALLCLOCK_SEC = 90.0
# 2026-08-04 レビュー是正(H-2): ⑤ 素材の取得は respond 本体の生成
# (RESPOND_WALLCLOCK_SEC)とは独立した短いタイムアウトで打ち切る。DB の
# プール枯渇・接続断で生成そのものをブロックしないため(NFR-5)。
RESPOND_MATERIALS_TIMEOUT_SEC = 5.0

# このターンで提示したスポットの素材(spot_id → SpotMaterial)をまとめて
# 引く読み取り専用の口。既定は `_default_spot_materials_provider`(DB)。
# テストは差し替えてよい(`tool_adapters.RouteSessionScopeFactory` と同じ
# 「注入可能なデフォルト」の流儀)。
SpotMaterialsProvider = Callable[[Sequence[str]], Awaitable[Mapping[str, SpotMaterial]]]


@runtime_checkable
class StreamingGenerationPort(Protocol):
    def stream(
        self,
        messages: Sequence[dict[str, str]],
        *,
        temperature: float = 0.2,
        max_tokens: int = 1024,
        extra_body: dict[str, Any] | None = None,
    ) -> AsyncIterator[str]: ...


class GenerateOnlyPort(Protocol):
    async def generate(
        self,
        messages: Sequence[dict[str, str]],
        *,
        temperature: float = 0.2,
        max_tokens: int = 1024,
        extra_body: dict[str, Any] | None = None,
    ) -> str: ...


class RespondGenerationError(RuntimeError):
    """respond が完全な本文を生成できなかった。"""


async def respond(
    state: TurnState,
    *,
    client: StreamingGenerationPort | GenerateOnlyPort | None = None,
    event_sink: EventSinkLike = None,
    wallclock_sec: float = RESPOND_WALLCLOCK_SEC,
    settings: Settings | None = None,
    spot_materials_provider: SpotMaterialsProvider | None = None,
) -> TurnState:
    """生成済み断片を送出し、例外時も partial 本文を TurnState に残す。"""

    resolved_client = client or GenerationClient()
    mode = response_mode(state)
    material_spot_ids = _presented_material_spot_ids(state)
    materials = await _load_spot_materials_safely(
        material_spot_ids,
        settings=settings,
        provider=spot_materials_provider,
        state=state,
        event_sink=event_sink,
    )
    messages = build_respond_messages(
        state, mode=mode, materials=materials, material_spot_ids=material_spot_ids
    )
    # H-1(2026-08-04 レビュー是正): ⑤ 素材の本文(description/social_proof)
    # に他スポット名が出るケース(実データ: spot_010「奈曽の白滝」の
    # description に「金峰神社」が含まれる等)を、閉世界検査の許可集合に
    # 含めるため、respond へ渡すのと同じ整形済みテキストを検査にも使う。
    materials_text = format_materials_section(material_spot_ids, materials, state.spot_names)
    started_at = time.perf_counter()
    try:
        async with asyncio.timeout(wallclock_sec):
            async for chunk in _stream_or_generate(resolved_client, messages):
                if not chunk:
                    continue
                candidate = state.assistant_text + chunk
                if has_repeated_ngram(candidate):
                    raise RespondGenerationError("応答の反復 n-gram を検知しました")
                state.assistant_text = candidate
                await emit(event_sink, token_event(chunk))
        state.respond_status = "complete"
    except Exception as exc:
        state.respond_status = "partial" if state.assistant_text else "failed"
        state.log_fields["respond_ms"] = round(
            (time.perf_counter() - started_at) * 1000,
            2,
        )
        raise RespondGenerationError("応答の生成に失敗しました") from exc

    state.log_fields["respond_ms"] = round(
        (time.perf_counter() - started_at) * 1000,
        2,
    )
    state.log_fields["respond_mode"] = mode.value
    checked = validate_response_spot_names(
        state.assistant_text,
        all_spot_names=state.spot_names,
        allowed_spot_ids=_allowed_spot_ids(state, material_text=materials_text),
    )
    if not checked.accepted:
        state.degraded.append(
            DegradedState(
                code="response_closed_world_violation",
                stage="respond",
                message=checked.reason or "応答のクローズドワールド違反",
            )
        )
        await emit(
            event_sink,
            error_event(
                stage="respond",
                code="response_closed_world_violation",
                degraded=True,
                message="応答に未確認の地点名が含まれました",
            ),
        )
    forbidden_terms = find_forbidden_internal_terms(state.assistant_text)
    if forbidden_terms:
        state.degraded.append(
            DegradedState(
                code="response_forbidden_term",
                stage="respond",
                message=f"内部語・処理の自己言及が応答に含まれます: {forbidden_terms}",
            )
        )
        await emit(
            event_sink,
            error_event(
                stage="respond",
                code="response_forbidden_term",
                degraded=True,
                message="応答に内部語が含まれました",
            ),
        )
    return state


def response_mode(state: TurnState) -> ResponseMode:
    if state.main_agent_failed:
        return ResponseMode.FAILURE
    return ResponseMode.EXPLANATION


def _presented_material_spot_ids(state: TurnState) -> list[str]:
    """このターンで提示したスポット(dialogue_style.md §4「⑤ 素材」の対象)。

    対象は (i) このターンの `recommend` 結果の候補(k=3)と (ii) このターンの
    旅程 diff で追加されたスポット。新規作成の `plan_itinerary` には
    `diff` が無い(新規なので全項目が実質「追加」)ため、その場合は旅程の
    全アイテムを対象にする。`edit_itinerary` は `diff.added` だけを対象に
    する(dialogue_style.md §5 実装方針の記述どおり)。

    候補を先に並べ、続けて旅程追加分を並べたうえで重複を除き、
    `RESPOND_MATERIALS_MAX_SPOTS` 件で切り詰める。**素材は respond の入力に
    だけ使う**(ツールのダイジェスト・メインループのコンテキストには影響
    しない。論点 A2)。
    """

    seen: set[str] = set()
    candidate_ids: list[str] = []
    itinerary_ids: list[str] = []

    def _add(target: list[str], value: object) -> None:
        if isinstance(value, str) and value not in seen:
            seen.add(value)
            target.append(value)

    for result in state.step_results.values():
        if result.tool is not ToolName.RECOMMEND:
            continue
        for candidate in result.data.get("candidates", []) or []:
            if isinstance(candidate, dict):
                _add(candidate_ids, candidate.get("spot_id"))

    for result in state.step_results.values():
        if result.tool is ToolName.PLAN_ITINERARY:
            itinerary = result.data.get("itinerary")
            if not isinstance(itinerary, dict):
                continue
            for day in itinerary.get("days", []) or []:
                if not isinstance(day, dict):
                    continue
                for item in day.get("items", []) or []:
                    if isinstance(item, dict):
                        _add(itinerary_ids, item.get("spot_id"))
        elif result.tool is ToolName.EDIT_ITINERARY:
            diff = result.data.get("diff")
            if isinstance(diff, dict):
                for value in diff.get("added", []) or []:
                    _add(itinerary_ids, value)

    return (candidate_ids + itinerary_ids)[:RESPOND_MATERIALS_MAX_SPOTS]


async def _load_spot_materials(
    spot_ids: Sequence[str],
    *,
    settings: Settings | None,
    provider: SpotMaterialsProvider | None,
) -> Mapping[str, SpotMaterial]:
    """空なら DB へ触らない。respond を呼ぶ大半のターン(QA・failure・候補も
    旅程追加も無いターン)で余計な DB アクセスを避ける。

    `provider` が明示されず `settings` も渡っていない呼び出し元(素材取得を
    検査しない既存の respond 単体テスト・軽量呼び出し)は、DB へ触らず素材
    なし(`{}`)に縮退する(NFR-5: 素材が引けなくても respond は止めない)。
    本番の `ConversationPipeline` は常に `settings` を渡す。
    """

    if not spot_ids:
        return {}
    if provider is None:
        if settings is None:
            return {}
        provider = _default_spot_materials_provider(settings)
    return await provider(spot_ids)


async def _load_spot_materials_safely(
    spot_ids: Sequence[str],
    *,
    settings: Settings | None,
    provider: SpotMaterialsProvider | None,
    state: TurnState,
    event_sink: EventSinkLike,
) -> Mapping[str, SpotMaterial]:
    """H-2(2026-08-04 レビュー是正): ⑤ 素材取得を局所縮退する。

    是正前は `_load_spot_materials` の呼び出しが `respond` 本体の
    `try/asyncio.timeout` の外にあったため、`session_scope` 由来の例外
    (DB 接続プール枯渇・接続断)がそのまま `respond` を貫通し、ターンが
    まるごと落ちていた。docstring が謳う NFR-5(素材が引けなくても respond
    は止めない)と矛盾していたため、ここで例外を吸収して `{}` へ縮退し、
    `DegradedState` へ記録する。取得自体にも本体の生成タイムアウトとは
    独立した短いタイムアウト(`RESPOND_MATERIALS_TIMEOUT_SEC`)を付け、
    DB がハングしてもターンをブロックしない。
    """

    try:
        async with asyncio.timeout(RESPOND_MATERIALS_TIMEOUT_SEC):
            return await _load_spot_materials(spot_ids, settings=settings, provider=provider)
    except Exception as exc:  # noqa: BLE001 - 素材取得はベストエフォート(NFR-5)
        state.degraded.append(
            DegradedState(
                code="respond_materials_unavailable",
                stage="respond",
                message=(
                    f"スポットの素材を取得できませんでした({type(exc).__name__})。"
                    "素材なしで応答を続けます。"
                ),
            )
        )
        await emit(
            event_sink,
            error_event(
                stage="respond",
                code="respond_materials_unavailable",
                degraded=True,
                message="スポットの説明を取得できなかったため、簡略な案内になります",
            ),
        )
        return {}


def _default_spot_materials_provider(settings: Settings) -> SpotMaterialsProvider:
    """`static.spots` の読み取り専用クエリ(新しいテーブル・書き込みは作らない)。

    `tool_adapters.py` の `route_session_scope` と同じ「短寿命 session を
    注入可能にする」流儀(ADR-0020)。respond は元々セッションを持たない
    ノードなので、ここでだけ独立した読み取り専用セッションを開く。
    """

    async def provider(spot_ids: Sequence[str]) -> Mapping[str, SpotMaterial]:
        unique_ids = list(dict.fromkeys(spot_ids))
        if not unique_ids:
            return {}
        async with session_scope(settings) as session:
            rows = (
                await session.execute(
                    select(
                        Spot.spot_id,
                        Spot.description,
                        Spot.social_proof,
                        Spot.tags_ja,
                    ).where(Spot.spot_id.in_(unique_ids))
                )
            ).all()
        return {
            row.spot_id: SpotMaterial(
                description=_japanese_value(row.description),
                social_proof=_japanese_value(row.social_proof),
                tags_ja=tuple(row.tags_ja or ()),
            )
            for row in rows
        }

    return provider


def _japanese_value(value: Any) -> str | None:
    """`description`/`social_proof`(JSONB の言語別辞書)から日本語を取る。

    `users.language` は未実装(`20_architecture.md` §10。多言語はスコープ外)
    のため当該言語は常に日本語になる(`catalog/repo.py`・
    `recommendation/repo.py` と同じ抽出パターン)。
    """

    if not isinstance(value, dict):
        return None
    japanese = value.get("ja")
    return japanese if isinstance(japanese, str) else None


async def _stream_or_generate(
    client: StreamingGenerationPort | GenerateOnlyPort,
    messages: list[dict[str, str]],
) -> AsyncIterator[str]:
    if isinstance(client, StreamingGenerationPort):
        async for value in client.stream(
            messages,
            temperature=0.2,
            max_tokens=RESPOND_MAX_TOKENS,
        ):
            yield value
        return
    text = await client.generate(
        messages,
        temperature=0.2,
        max_tokens=RESPOND_MAX_TOKENS,
    )
    # generate しか持たないモックも同じイベント経路へ流す。
    yield text


def _allowed_spot_ids(state: TurnState, *, material_text: str = "") -> set[str]:
    values: set[str] = {candidate.spot_id for candidate in state.last_candidates}
    for result in state.step_results.values():
        raw_ids = result.data.get("spot_ids")
        if isinstance(raw_ids, list):
            values.update(value for value in raw_ids if isinstance(value, str))
        spot_id = result.data.get("spot_id")
        if isinstance(spot_id, str):
            values.add(spot_id)
        diff = result.data.get("diff")
        if isinstance(diff, dict):
            for field in ("added", "removed", "retimed"):
                raw_ids = diff.get(field)
                if isinstance(raw_ids, list):
                    values.update(value for value in raw_ids if isinstance(value, str))
            moved = diff.get("moved")
            if isinstance(moved, list):
                values.update(
                    value["spot_id"]
                    for value in moved
                    if isinstance(value, dict) and isinstance(value.get("spot_id"), str)
                )
        concessions = result.data.get("concessions")
        if isinstance(concessions, list):
            values.update(
                _concession_spot_ids(
                    concessions,
                    known_spot_ids=set(state.spot_names),
                )
            )
        itinerary = result.data.get("itinerary")
        if isinstance(itinerary, dict):
            for day in itinerary.get("days", []):
                if isinstance(day, dict):
                    for endpoint_name in ("origin", "destination"):
                        endpoint = day.get(endpoint_name)
                        if isinstance(endpoint, dict) and isinstance(endpoint.get("spot_id"), str):
                            values.add(endpoint["spot_id"])
                    values.update(
                        item["spot_id"]
                        for item in day.get("items", [])
                        if isinstance(item, dict) and isinstance(item.get("spot_id"), str)
                    )
    # 現在旅程の説明は入力事実なので常に許可する。
    if state.itinerary is not None:
        for day in state.itinerary.itinerary.days:
            values.add(day.origin.spot_id)
            values.update(item.spot_id for item in day.items)
            values.add(day.destination.spot_id)
    # 不具合2の是正(2026-08-04 実機調査): 上の構造化フィールドだけでは、
    # recommend の移動時間文(「〈起点施設〉から車で約 25 分」)に出る起点や、
    # 旅程ダイジェストの整形文にだけ現れる地点名を拾えず、respond がそれを
    # 使うと誤って closed-world 違反として検知していた
    # (`response_closed_world_violation`: 「鳥海高原家族旅行村」が未提示扱い
    # になった実機ログを確認)。`trajectory_text` は respond が実際に読む
    # ①軌跡の本文そのものなので、そこに登場するスポット名は無条件で
    # 許可する(起点・終点を含め、素材に出た名前を respond が使うのは正当)。
    # H-1(2026-08-04 レビュー是正): ⑤ 素材の本文(`format_materials_section`
    # の出力。呼び出し元が渡す)にも他スポット名が出ることがある(実データ:
    # spot_010「奈曽の白滝」の description に「金峰神社」が含まれる等、
    # 43 件中 4 件)。素材本文に出た名前は respond が読む入力そのものなので、
    # 軌跡本文と同様に無条件で許可する。
    scan_text = trajectory_text(state.trajectory)
    if material_text:
        scan_text = f"{scan_text}\n{material_text}"
    values.update(
        spot_id
        for spot_id, name in state.spot_names.items()
        if name and name in scan_text
    )
    return values


def _concession_spot_ids(
    value: Any,
    *,
    known_spot_ids: set[str],
    field_name: str | None = None,
) -> set[str]:
    """concession の `args.target` 等に含まれる既知地点も拾う。"""

    if isinstance(value, str):
        is_spot_field = field_name == "spot_id" or field_name == "spot_ids"
        return {value} if is_spot_field or value in known_spot_ids else set()
    if isinstance(value, dict):
        result: set[str] = set()
        for key, child in value.items():
            result.update(
                _concession_spot_ids(
                    child,
                    known_spot_ids=known_spot_ids,
                    field_name=key,
                )
            )
        return result
    if isinstance(value, list):
        result = set()
        for child in value:
            result.update(
                _concession_spot_ids(
                    child,
                    known_spot_ids=known_spot_ids,
                    field_name=field_name,
                )
            )
        return result
    return set()
