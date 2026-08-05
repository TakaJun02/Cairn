"""③ ReAct メインエージェント。

`Docs/30_design/agent_react_architecture.md` §3・§10 が仕様。毎周コンテキストを
組み立てて 1 回 LLM を呼び、`{"thought","action":{"tool","args"}}` を受け取り、
Tool を実行して軌跡(§3.1 ⑤)へ積み、次の周へ進む。`done` でループを終える。

Tool enum は `recommend` / `plan_itinerary` / `edit_itinerary` /
`search_knowledge` / `ask_user` / `done` の6つ。`ask_user` は結果(回答)を
軌跡へ持ち帰る通常の Tool である(§7。ターンは中断しない)。

既存 Tool(`tool_adapters.ToolAdapters`)は spot_id ベースの契約のまま変更しない。
このモジュールが「メインエージェントが書いたスポット名 → spot_id」の変換と、
その逆(結果 → 名前空間ダイジェスト)を橋渡しする。

`recommend` の `_dispatch_recommend` はレコメンドサブエージェント
(`recommend_agent.run_recommend_subagent`)を経由する。`instruction`
(自然言語)→ `filter` の翻訳はそちらが guided decoding で行い、このモジュールは
翻訳結果をそのまま既存 Tool へ渡すだけである(§4)。

`plan_itinerary`/`edit_itinerary` は旅程計画サブエージェント
(`itinerary_subagent.run_plan_itinerary`/`run_edit_itinerary`)を経由する。
名寄せ本実装・フロー1〜4はそちらに集約されている(§5)。

`ask_user` の実行(ガード検査 → HITL 待ち受け → 状態更新 → update_profile
再実行)は `ask_execution.execute_ask_user` に集約する。レコメンド SA
(`recommend_agent.py`)・知識検索 SA(narration 側のアダプタ経由)と共通。

このモジュールは Tool 選択・ループ制御(§3・§10)を持つ。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Sequence
from typing import Any, Protocol

from pydantic import ValidationError

from app.core.llm import GenerationError
from app.domains.conversation.ask_execution import execute_ask_user
from app.domains.conversation.events import (
    EventSinkLike,
    emit,
    error_event,
    resolve_error_stage,
    state_event,
)
from app.domains.conversation.guards import (
    MAX_ASK_STREAK,
    MAX_ASK_USER_PER_TURN,
    has_repeated_ngram,
)
from app.domains.conversation.history import estimate_tokens
from app.domains.conversation.itinerary_digest import UNNAMED_SPOT_JA
from app.domains.conversation.itinerary_subagent import (
    active_constraint_ids,
    run_edit_itinerary,
    run_plan_itinerary,
)
from app.domains.conversation.name_resolution import build_name_resolution_context
from app.domains.conversation.prompts import (
    build_main_agent_messages,
    main_agent_done_only_schema,
    main_agent_guided_schema,
)
from app.domains.conversation.recommend_agent import run_recommend_subagent
from app.domains.conversation.recommendation_context import build_recommendation_context
from app.domains.conversation.state import CandidateReference, DegradedState, TurnState
from app.domains.conversation.tool_ports import ConversationToolPort
from app.domains.conversation.types import (
    AskUserArgs,
    AskUserOption,
    MainAgentTurn,
    MainRecommendArgs,
    MainSearchKnowledgeArgs,
    MainToolName,
    RecommendArgs,
    SearchKnowledgeArgs,
    Slot,
    ToolError,
    ToolErrorCode,
    ToolResult,
    TrajectoryStep,
)

logger = logging.getLogger("app.conversation.main_agent")

# §9: max_model_len 16,384 を基準にした R2 の予算。
MAX_MODEL_LEN = 16_384
SOFT_BUDGET_TOKENS = round(MAX_MODEL_LEN * 0.70)
HARD_BUDGET_TOKENS = round(MAX_MODEL_LEN * 0.85)
# R1: 手数上限 8(done を除く実行手)。
MAX_EXECUTED_STEPS = 8
# 安全弁。仕様外だが、モックや異常な LLM 応答でループが無限に回るのを防ぐ。
MAX_LOOP_ITERATIONS = 20

MAIN_AGENT_EXPECTED_TOKENS = 800
MAIN_AGENT_MAX_TOKENS = int(MAIN_AGENT_EXPECTED_TOKENS * 1.5)
MAIN_AGENT_WALLCLOCK_SEC = 90.0

_STEP_LABELS: dict[str, dict[str, str]] = {
    "recommend": {
        "started": "おすすめを探しています",
        "finished": "おすすめを確定しました",
    },
    "plan_itinerary": {
        "started": "旅程を作成しています",
        "finished": "旅程を作成しました",
    },
    "edit_itinerary": {
        "started": "旅程を編集しています",
        "finished": "旅程を更新しました",
    },
    "search_knowledge": {
        "started": "資料を調べています",
        "finished": "調査結果をまとめました",
    },
    "ask_user": {
        "started": "質問を準備しています",
        "finished": "回答を受け取りました",
    },
}

_EXECUTABLE_TOOLS = frozenset(
    value.value for value in MainToolName if value is not MainToolName.DONE
)


class GenerationPort(Protocol):
    async def generate(
        self,
        messages: Sequence[dict[str, str]],
        *,
        temperature: float = 0.2,
        max_tokens: int = 1024,
        extra_body: dict[str, Any] | None = None,
    ) -> str: ...


async def run_main_agent(
    state: TurnState,
    *,
    tools: ConversationToolPort,
    client: GenerationPort,
    event_sink: EventSinkLike = None,
    wallclock_sec: float = MAIN_AGENT_WALLCLOCK_SEC,
) -> TurnState:
    """TurnState の ③ 欄(trajectory/step_results/degraded 等)だけを埋める。"""

    started_at = time.perf_counter()
    executed_signatures: set[tuple[str, str]] = set()
    executed_count = 0
    dispatch_index = 0
    iteration = 0

    while True:
        iteration += 1
        reduced = executed_count >= MAX_EXECUTED_STEPS or iteration > MAX_LOOP_ITERATIONS

        constraint_ids = active_constraint_ids(state)
        messages = build_main_agent_messages(state, reduced=reduced)
        estimated_tokens = sum(estimate_tokens(value["content"]) for value in messages)

        if estimated_tokens >= HARD_BUDGET_TOKENS:
            state.degraded.append(
                DegradedState(
                    code="context_budget_hard",
                    stage="main_agent",
                    message="コンテキスト予算の上限に達したため打ち切りました",
                )
            )
            await emit(
                event_sink,
                error_event(
                    stage="main_agent",
                    code="context_budget_hard",
                    degraded=True,
                    message="コンテキスト予算の上限に達したため打ち切りました",
                ),
            )
            break
        if estimated_tokens >= SOFT_BUDGET_TOKENS and not reduced:
            reduced = True
            messages = build_main_agent_messages(state, reduced=True)

        # R4/A2(§10): 上限に達した周は ask_user をスキーマから外す(縮小
        # スキーマではなく、6 分岐のうち ask_user だけを外した 5 分岐)。
        allow_ask = (
            state.ask_user_count < MAX_ASK_USER_PER_TURN
            and state.ask_streak < MAX_ASK_STREAK
        )
        schema = (
            main_agent_done_only_schema()
            if reduced
            else main_agent_guided_schema(constraint_ids, allow_ask_user=allow_ask)
        )
        turn = await _call_main_agent(
            client, messages, schema, wallclock_sec=wallclock_sec
        )
        state.main_agent_turns += 1
        if turn is None:
            if not state.trajectory:
                state.main_agent_failed = True
                await emit(
                    event_sink,
                    error_event(
                        stage="main_agent",
                        code="main_agent_failed",
                        degraded=False,
                        message="うまく処理できませんでした。もう一度入力してください。",
                    ),
                )
            else:
                state.degraded.append(
                    DegradedState(
                        code="main_agent_degraded",
                        stage="main_agent",
                        message="途中で応答生成を打ち切り、ここまでの結果でまとめます",
                    )
                )
            break

        tool = turn.action.tool
        if tool == MainToolName.DONE.value:
            break
        if tool not in _EXECUTABLE_TOOLS:
            # guided decoding のスキーマが排他な enum を強制するため通常は
            # 起きない。安全側でこの手を観測として差し戻し、周を続ける。
            state.trajectory.append(
                TrajectoryStep(
                    tool=str(tool),
                    thought=turn.thought,
                    args=turn.action.args,
                    observation=f"未知の Tool です: {tool}。有効な Tool を選び直してください。",
                )
            )
            continue

        signature = (
            tool,
            json.dumps(turn.action.args, sort_keys=True, ensure_ascii=False, default=str),
        )
        if signature in executed_signatures:
            # R3: 同一 Tool + 同一引数の反復は実行しない。
            state.trajectory.append(
                TrajectoryStep(
                    tool=tool,
                    thought=turn.thought,
                    args=turn.action.args,
                    observation=(
                        "同じ手を繰り返しています。"
                        "別の手を試すか、done でまとめてください。"
                    ),
                )
            )
            continue
        executed_signatures.add(signature)

        dispatch_index += 1
        await emit(
            event_sink,
            state_event(
                "step",
                tool=tool,
                status="started",
                label_ja=_STEP_LABELS[tool]["started"],
            ),
        )
        observation, error_payload, executed = await _dispatch(
            state,
            tools,
            tool=tool,
            raw_args=turn.action.args,
            step_id=dispatch_index,
            client=client,
            event_sink=event_sink,
        )
        await emit(
            event_sink,
            state_event(
                "step",
                tool=tool,
                status="finished",
                label_ja=_STEP_LABELS[tool]["finished"],
            ),
        )
        state.trajectory.append(
            TrajectoryStep(
                tool=tool,
                thought=turn.thought,
                args=turn.action.args,
                observation=observation,
                error=error_payload,
            )
        )
        # R1(§3.5・§10): 手数上限は「実行された手」だけを数える
        # (2026-08-04、レビュー是正・裁定18)。`ask_user` がガード
        # (R4/A1〜A6)で実行されなかった場合は、選ばれただけで実際には
        # 何も起きていないので加算しない。
        if executed:
            executed_count += 1
        if error_payload is not None and not error_payload.get("recoverable", True):
            # stage は SSE 契約(chat_sse.md)の ErrorStage 語彙に合わせる。
            # 語彙内の Tool 名(recommend/plan_itinerary/edit_itinerary/
            # search_knowledge)はそのまま stage に残す。語彙外の Tool 名
            # ("ask_user"。2026-08-04 実機再現、[25 §1-6])だけ
            # `resolve_error_stage` が "main_agent" へフォールバックする
            # (2026-08-04、レビュー是正 M-1。以前は常に "main_agent" へ
            # 落としており、語彙内 Tool の stage 情報を不要に失っていた)。
            await emit(
                event_sink,
                error_event(
                    stage=resolve_error_stage(tool),
                    code=str(error_payload.get("code", "internal")),
                    degraded=False,
                    message=str(error_payload.get("message_ja", "")),
                ),
            )
            state.degraded.append(
                DegradedState(
                    code=str(error_payload.get("code", "internal")),
                    stage="main_agent",
                    message=f"{tool} の失敗によりここまでの結果でまとめます",
                )
            )
            break

    state.executed_tool_count = executed_count
    state.log_fields["main_agent_ms"] = round((time.perf_counter() - started_at) * 1000, 2)
    state.log_fields["main_agent_turns"] = state.main_agent_turns
    state.log_fields["executed_tool_count"] = executed_count
    return state


async def _call_main_agent(
    client: GenerationPort,
    messages: list[dict[str, str]],
    schema: dict[str, Any],
    *,
    wallclock_sec: float,
) -> MainAgentTurn | None:
    """1 回の guided JSON 呼び出し。JSON 不正時は 1 回だけ再試行する。"""

    attempt_messages = messages
    for attempt in range(2):
        try:
            async with asyncio.timeout(wallclock_sec):
                raw = await client.generate(
                    attempt_messages,
                    temperature=0.2,
                    max_tokens=MAIN_AGENT_MAX_TOKENS,
                    extra_body={
                        "response_format": {
                            "type": "json_schema",
                            "json_schema": {
                                "name": "conversation_main_agent",
                                "strict": True,
                                "schema": schema,
                            },
                        }
                    },
                )
            if has_repeated_ngram(raw):
                raise ValueError("同一 n-gram の反復を検知しました")
            return MainAgentTurn.model_validate(json.loads(raw))
        except (json.JSONDecodeError, ValidationError, ValueError) as exc:
            if attempt == 0:
                attempt_messages = [dict(value) for value in messages]
                attempt_messages[0] = dict(attempt_messages[0])
                attempt_messages[0]["content"] += (
                    "\n再試行です。前回は契約違反でした。"
                    f"JSON Schema に厳密に従ってください。原因: {str(exc)[:240]}"
                )
                continue
            logger.warning("main_agent_parse_failed", extra={"reason": str(exc)})
            return None
        except (GenerationError, TimeoutError) as exc:
            logger.warning("main_agent_generation_failed", extra={"reason": str(exc)})
            return None
    return None  # pragma: no cover - for 文で必ず return する


async def _dispatch(
    state: TurnState,
    tools: ConversationToolPort,
    *,
    tool: str,
    raw_args: dict[str, Any],
    step_id: int,
    client: GenerationPort,
    event_sink: EventSinkLike,
) -> tuple[str, dict[str, Any] | None, bool]:
    """`(observation, error_payload, executed)` を返す。

    `executed` は R1(§3.5)が数える「実行された手」の判定に使う
    (2026-08-04、レビュー是正・裁定18)。`ask_user` 以外は Tool が
    ディスパッチされた時点で必ず何か実行されるため常に `True`。`ask_user`
    だけはガード(R4/A1〜A6)で実行されないことがあるため、
    `_dispatch_ask_user` の判定をそのまま伝える。`args` の契約違反
    (`ValidationError`。M-2)で落ちた場合は、どの Tool でも実行されて
    いないので `False` になる。
    """

    try:
        if tool == MainToolName.RECOMMEND.value:
            observation, error = await _dispatch_recommend(
                state, tools, raw_args, step_id, client=client, event_sink=event_sink
            )
            return observation, error, True
        if tool == MainToolName.PLAN_ITINERARY.value:
            observation, error = await run_plan_itinerary(state, tools, raw_args, step_id)
            return observation, error, True
        if tool == MainToolName.EDIT_ITINERARY.value:
            observation, error = await run_edit_itinerary(state, tools, raw_args, step_id)
            return observation, error, True
        if tool == MainToolName.SEARCH_KNOWLEDGE.value:
            observation, error = await _dispatch_search_knowledge(
                state, tools, raw_args, step_id, client=client, event_sink=event_sink
            )
            return observation, error, True
        if tool == MainToolName.ASK_USER.value:
            return await _dispatch_ask_user(
                state, tools, raw_args, step_id, client=client, event_sink=event_sink
            )
    except ValidationError as exc:
        # M-2(2026-08-04、レビュー是正): 各 Tool の args 契約違反(型不一致・
        # 必須項目欠落等)は、guided スキーマが通常防ぐが、フォールバック
        # 応答等で契約違反の生 args が来た場合に備え、どの Tool でも
        # recoverable=true の観測として差し戻す(設計は「Tool の失敗は結果で
        # 差し戻し、次の一手で対処する」)。`_dispatch_ask_user` 内の個別
        # try/except(より具体的な message_ja)はこれより先に評価されるため、
        # ここには重複して届かない。
        error = ToolError(
            code=ToolErrorCode.INTERNAL,
            message_ja=(
                f"{tool} の引数が契約に違反しています: {str(exc)[:240]}。"
                "最も妥当な解釈で引数を直して再実行してください。"
            ),
            recoverable=True,
            details={"error_type": type(exc).__name__},
        )
        return error.message_ja, _error_payload(error), False
    except Exception as exc:  # noqa: BLE001 - Tool 実装からの漏れも結果へ閉じる(C6)
        logger.exception("main_agent_dispatch_failed", extra={"tool": tool})
        error = ToolError(
            code=ToolErrorCode.INTERNAL,
            message_ja="処理中に予期しない問題が発生しました。",
            recoverable=False,
            details={"error_type": type(exc).__name__},
        )
        return error.message_ja, _error_payload(error), True
    raise AssertionError(f"未対応の Tool です: {tool}")  # pragma: no cover


def _error_payload(error: ToolError) -> dict[str, Any]:
    return {
        "code": error.code.value,
        "message_ja": error.message_ja,
        "recoverable": error.recoverable,
    }


async def _dispatch_recommend(
    state: TurnState,
    tools: ConversationToolPort,
    raw_args: dict[str, Any],
    step_id: int,
    *,
    client: GenerationPort,
    event_sink: EventSinkLike,
) -> tuple[str, dict[str, Any] | None]:
    """§4 レコメンド SA: `instruction` → `filter` 翻訳の後、既存推薦処理へ渡す。

    SA の判定 LLM(guided decoding)は `recommend_agent.run_recommend_subagent`
    が担う。タグ 80 語・`mobility` の enum はそちらのプロンプトにだけ載る
    (このモジュールには一切現れない。§4 の設計判断)。
    """

    parsed = MainRecommendArgs.model_validate(raw_args)
    act_result = await run_recommend_subagent(
        instruction=parsed.instruction,
        profile=build_recommendation_context(state).profile,
        tag_vocabulary=state.tag_vocabulary,
        client=client,
        event_sink=event_sink,
        state=state,
        tools=tools,
        step_id=step_id,
    )
    if act_result.degraded:
        state.degraded.append(
            DegradedState(
                code="recommend_agent_degraded",
                stage="recommend",
                message="指示の翻訳に失敗し、条件を絞らずに推薦しました",
            )
        )
    # 2026-08-04 レビュー是正(High・裁定3b): SA が ask_user で質問した場合、
    # `state.profile`/`state.score_adjustments` はターン内で既に更新されて
    # いる(§2・§4「回答 → update_profile → 更新後プロフィールで続行」)。
    # ここで `RecommendationContext` を**実推薦の直前に**作り直すことで、
    # 回答内容(interests/party/mobility 等だけでなく score_adjustments も)を
    # 実際のスコアリング・リランクへ反映する。SA 実行前に作った context を
    # 使い回すと、質問の回答が候補抽出・スコアへ一切効かなかった
    # (2026-08-04 レビュー指摘の実バグ)。
    context = build_recommendation_context(state)
    args = RecommendArgs(
        filter=act_result.filter.model_dump(mode="json", exclude_none=True),
        # 2026-08-04、dialogue_style.md §4 決定: 推薦は 3 件に固定し、
        # respond が 3 件すべてを語る(旧 k=5 から変更)。
        k=3,
        exclude=list(state.presented_spot_ids),
    )
    result = await tools.recommend(
        step_id=step_id,
        args=args,
        context=context,
        use_specialist=True,
    )
    if isinstance(result, ToolError):
        return result.message_ja, _error_payload(result)
    state.step_results[step_id] = result
    _apply_recommend_result(state, result)
    digest = _format_recommend_digest(
        result,
        state.spot_names,
        assumptions=act_result.assumptions,
        dropped=act_result.dropped,
    )
    return digest, None


async def _dispatch_search_knowledge(
    state: TurnState,
    tools: ConversationToolPort,
    raw_args: dict[str, Any],
    step_id: int,
    *,
    client: GenerationPort,
    event_sink: EventSinkLike,
) -> tuple[str, dict[str, Any] | None]:
    parsed = MainSearchKnowledgeArgs.model_validate(raw_args)
    spot_id: str | None = None
    dropped: list[str] = []
    if parsed.spot_name is not None:
        context = _name_context(state)
        spot_id = context.resolve(parsed.spot_name)
        if spot_id is None:
            dropped.append(parsed.spot_name)

    async def ask_callback(
        *,
        kind: str,
        slot: str | None = None,
        surface: str | None = None,
        reason: str,
        options: list[dict[str, str]],
    ) -> str:
        """§6: narration → conversation の逆依存を作らない ask コールバック(port)。

        narration 側は `kind`/`slot`/`surface`/`reason`/`options`(label/value
        の dict。narration_qa.md §2 の `{kind, slot?/surface?, reason,
        options}` と同じ形)と観測文字列だけを知る。ガード・HITL 待ち受け・
        状態更新は `ask_execution.execute_ask_user` に委譲する
        (update_profile は知識検索の聞き返しでは回さない。§6 の対象外)。
        """

        parsed_options = [
            AskUserOption(
                label=str(option.get("label", "")),
                value=str(option.get("value") or option.get("label", "")),
            )
            for option in options
        ]
        try:
            if kind == "preference":
                question = AskUserArgs(
                    kind="preference",
                    slot=Slot(slot) if slot else None,
                    reason=reason,
                    options=parsed_options,
                )
            else:
                question = AskUserArgs(
                    kind="clarify",
                    surface=surface,
                    reason=reason,
                    options=parsed_options,
                )
        except (ValueError, ValidationError) as exc:
            return (
                f"質問を確定できませんでした(契約違反: {exc})。"
                "最も確からしい解釈を採り、仮定を明示して進めてください。"
            )
        outcome = await execute_ask_user(
            state,
            tools,
            question,
            step_id=step_id,
            client=client,
            event_sink=event_sink,
            run_update_profile=False,
        )
        return outcome.digest

    # 2026-08-04 レビュー是正(Medium・裁定16): R4 に到達済みなら
    # `ask_callback` 自体を渡さない。`KnowledgeSearchAgent._available_tools`
    # は `ask_callback is None` のとき `ask_user` を guided schema の enum
    # から外すため、narration 側にカウンタを持ち込まずに同じ効果を得られる
    # (narration → conversation の逆依存を作らない設計を保つ)。
    ask_budget_available = (
        state.ask_user_count < MAX_ASK_USER_PER_TURN and state.ask_streak < MAX_ASK_STREAK
    )
    result = await tools.search_knowledge(
        step_id=step_id,
        args=SearchKnowledgeArgs(request=parsed.request, spot_id=spot_id),
        ask_callback=ask_callback if ask_budget_available else None,
    )
    if isinstance(result, ToolError):
        return result.message_ja, _error_payload(result)
    state.step_results[step_id] = result
    digest = _format_search_digest(result.data, dropped=dropped)
    return digest, None


async def _dispatch_ask_user(
    state: TurnState,
    tools: ConversationToolPort,
    raw_args: dict[str, Any],
    step_id: int,
    *,
    client: GenerationPort,
    event_sink: EventSinkLike,
) -> tuple[str, dict[str, Any] | None, bool]:
    """メインエージェント自身の `ask_user`(§3.3・§7)。

    メインエージェントは spot_id を見ない・書かないため(§3.3)、
    `kind=clarify` の `options[].value` はスポット**名**で書かれる。ここで
    名前 → spot_id を解決してから `ask_execution.execute_ask_user` へ渡す。
    1 つでも解決できなければ質問そのものを実行しない(A4)。

    `AskUserArgs.model_validate` の `ValidationError`(`kind` と `slot`/
    `surface` の排他違反等)は、guided スキーマの anyOf 分岐(§14・
    `prompts._ask_user_args_schema`)で通常は防がれるが、フォールバック応答
    やモックなど契約違反の生 args が来た場合に備え、ここで捕捉して
    recoverable=true の `ToolError` を観測として差し戻す(2026-08-04
    実機再現、[25 §1-6])。旧実装はここで例外を投げっぱなしにし、呼び出し元
    `_dispatch` の包括 except(C6)が recoverable=false の INTERNAL に変換して
    ターンを打ち切っていた(設計は「Tool の失敗は結果で差し戻し、次の一手で
    対処する」— recoverable のはず)。
    """

    try:
        parsed = AskUserArgs.model_validate(raw_args)
    except ValidationError as exc:
        error = ToolError(
            code=ToolErrorCode.INTERNAL,
            message_ja=(
                f"ask_user の引数が契約に違反しています: {str(exc)[:240]}。"
                "最も妥当な解釈を採って進めてください。"
            ),
            recoverable=True,
            details={"error_type": type(exc).__name__},
        )
        return error.message_ja, _error_payload(error), False
    if parsed.kind == "clarify":
        context = _name_context(state)
        # A6(2026-08-04、レビュー是正・裁定17。最小実装): `surface` 自体が
        # 既に一意に解決できるなら「念のための確認」なので聞かない。
        surface_match = context.resolve_detailed(parsed.surface or "")
        if surface_match.status == "resolved":
            resolved_name = state.spot_names.get(
                surface_match.spot_id or "", surface_match.spot_id or ""
            )
            return (
                f"「{parsed.surface}」は{resolved_name}として解決済みです。"
                "聞き返さずにそのまま進めてください。",
                None,
                False,
            )
        unresolved: list[str] = []
        resolved_options: list[AskUserOption] = []
        for option in parsed.options:
            spot_id = context.resolve(option.value)
            if spot_id is None:
                unresolved.append(option.value)
            else:
                resolved_options.append(AskUserOption(label=option.label, value=spot_id))
        if unresolved:
            return (
                "質問を確定できませんでした"
                f"({'、'.join(unresolved)} を地点として解決できません)。"
                "最も妥当な解釈を採って進めてください。",
                None,
                False,
            )
        parsed = parsed.model_copy(update={"options": resolved_options})

    allowed_spot_ids = set(state.spot_catalog)
    outcome = await execute_ask_user(
        state,
        tools,
        parsed,
        step_id=step_id,
        client=client,
        event_sink=event_sink,
        allowed_spot_ids=allowed_spot_ids,
        existing_spot_ids=allowed_spot_ids,
    )
    return outcome.digest, outcome.error, outcome.executed


# ---------------------------------------------------------------------------
# 名前解決の補助
# ---------------------------------------------------------------------------


def _name_context(state: TurnState):
    return build_name_resolution_context(
        spot_catalog=state.spot_catalog,
        last_candidates=state.last_candidates,
        current_itinerary=state.itinerary.itinerary if state.itinerary is not None else None,
    )


# ---------------------------------------------------------------------------
# 結果の適用(last_candidates/presented_spot_ids の更新)
# ---------------------------------------------------------------------------


def _apply_recommend_result(state: TurnState, result: ToolResult) -> None:
    candidates = result.data.get("candidates", [])
    references: list[CandidateReference] = []
    for index, value in enumerate(candidates, 1):
        if not isinstance(value, dict) or not isinstance(value.get("spot_id"), str):
            continue
        spot_id = value["spot_id"]
        references.append(
            CandidateReference(
                spot_id=spot_id,
                name_ja=state.spot_names.get(spot_id, UNNAMED_SPOT_JA),
                rank=int(value.get("rank", index)),
            )
        )
    state.last_candidates = references
    state.presented_spot_ids = list(
        dict.fromkeys(
            [
                *state.presented_spot_ids,
                *[value for value in result.data.get("spot_ids", []) if isinstance(value, str)],
            ]
        )
    )
    for code in result.degraded:
        state.degraded.append(
            DegradedState(
                code=code,
                stage="recommend",
                message="recommend は縮退経路を使用しました",
            )
        )


# ---------------------------------------------------------------------------
# ダイジェスト整形
# ---------------------------------------------------------------------------


def _format_recommend_digest(
    result: ToolResult,
    spot_names: dict[str, str],
    *,
    assumptions: list[str] | None = None,
    dropped: list[str] | None = None,
) -> str:
    candidates = result.data.get("candidates", [])
    if not candidates:
        lines = ["おすすめは見つかりませんでした。"]
    else:
        lines = ["おすすめ:"]
        for value in candidates:
            if not isinstance(value, dict):
                continue
            spot_id = value.get("spot_id")
            name = (
                spot_names.get(spot_id, UNNAMED_SPOT_JA) if isinstance(spot_id, str) else "?"
            )
            rank = value.get("rank", "?")
            reason = value.get("reason_materials", {}) or {}
            tags = reason.get("matched_tags", [])
            travel = reason.get("travel_time_text", "")
            detail = "、".join(str(value) for value in tags[:3]) if tags else ""
            suffix = "".join(
                filter(
                    None, [f"(タグ: {detail})" if detail else "", f" {travel}" if travel else ""]
                )
            )
            lines.append(f"  {rank}. {name}{suffix}")
    if assumptions:
        lines.append("置いた仮定: " + "、".join(assumptions))
    if dropped:
        lines.append("除外した条件: " + "、".join(dropped))
    return "\n".join(lines)


def _format_search_digest(data: dict[str, Any], *, dropped: list[str]) -> str:
    answer = data.get("answer_ja", "")
    coverage = data.get("coverage", "none")
    sources = data.get("sources", []) or []
    titles = [
        value.get("title")
        for value in sources
        if isinstance(value, dict) and value.get("title")
    ]
    lines = [f"検索結果(coverage={coverage}): {answer}"]
    if titles:
        lines.append("出典: " + "、".join(str(value) for value in titles))
    if dropped:
        lines.append("解決できなかったスポット名: " + "、".join(dropped))
    return "\n".join(lines)


