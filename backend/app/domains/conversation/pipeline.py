"""6 ノードを固定順で流す旅程計画エージェントの司会。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.llm import GenerationClient
from app.domains.conversation.context import ContextRepositoryPort, load_context
from app.domains.conversation.events import (
    EventSinkLike,
    done_event,
    emit,
    error_event,
)
from app.domains.conversation.executor import act, apply_profile_update
from app.domains.conversation.persist import PersistRepositoryPort, persist
from app.domains.conversation.planner import validate_plan
from app.domains.conversation.respond import RespondGenerationError, respond
from app.domains.conversation.state import TurnState
from app.domains.conversation.tool_ports import ConversationToolPort
from app.domains.conversation.understand import (
    GenerationPort,
    UnderstandFatalError,
    understand,
)

logger = logging.getLogger("app.conversation.turn")


class ConversationPipeline:
    def __init__(
        self,
        repository: ContextRepositoryPort | PersistRepositoryPort,
        *,
        event_sink: EventSinkLike = None,
        llm_client: Any | None = None,
        tools: ConversationToolPort | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.repository = repository
        self.event_sink = event_sink
        # N2/N5 と P7 の専門呼び出しを同じ差し替え可能な
        # client へ接続する。
        self.llm_client = llm_client or GenerationClient(settings)
        self.tools = tools
        self.settings = settings

    async def run(
        self,
        *,
        user_id: int,
        utterance: str,
        resolves: Mapping[str, Any] | None = None,
        turn_id: str | None = None,
    ) -> TurnState:
        # N1
        state = await load_context(
            self.repository,  # type: ignore[arg-type]
            user_id=user_id,
            utterance=utterance,
            resolves=resolves,
            turn_id=turn_id,
        )
        try:
            # N2
            await understand(
                state,
                client=self.llm_client,  # type: ignore[arg-type]
                event_sink=self.event_sink,
            )
        except asyncio.CancelledError:
            state.understand_failed = True
            await self._persist_or_finish(state)
            self._log_turn(state)
            raise
        except UnderstandFatalError:
            await emit(
                self.event_sink,
                error_event(
                    stage="understand",
                    code="understand_failed",
                    degraded=False,
                    message=(
                        "うまく理解できませんでした。"
                        "もう一度入力してください。"
                    ),
                ),
            )
            await self._persist_or_finish(state)
            self._log_turn(state)
            return state

        # N3
        await validate_plan(state, event_sink=self.event_sink)

        # N4（E3 のときは Tool なし）
        if state.accepted_steps:
            tools = self.tools or self._default_tools(state)
            await act(state, tools=tools, event_sink=self.event_sink)

        # §16.7 の順序: Tool state の後、profile、token。
        await apply_profile_update(state, event_sink=self.event_sink)

        # N5 + N6 (finally 相当)
        await self._respond_then_persist(state)
        self._log_turn(state)
        return state

    async def _respond_then_persist(self, state: TurnState) -> None:
        try:
            try:
                await respond(
                    state,
                    client=self.llm_client,
                    event_sink=self.event_sink,
                )
            except RespondGenerationError:
                await emit(
                    self.event_sink,
                    error_event(
                        stage="respond",
                        code="respond_failed",
                        degraded=False,
                        message=(
                            "応答の生成に失敗しました。"
                            "表示済みの候補や旅程は保存します。"
                        ),
                    ),
                )
            except asyncio.CancelledError:
                state.respond_status = (
                    "partial" if state.assistant_text else "failed"
                )
                raise
        finally:
            # ストリーム中断や error イベント送出失敗も含め、
            # N6 は respond の出口に必ず置く。
            await self._persist_or_finish(state)

    async def _persist_or_finish(self, state: TurnState) -> None:
        try:
            await persist(
                state,
                repository=self.repository,  # type: ignore[arg-type]
                event_sink=self.event_sink,
            )
        except Exception:
            logger.exception("conversation_persist_failed")
            await emit(
                self.event_sink,
                error_event(
                    stage="persist",
                    code="persist_failed",
                    degraded=False,
                    message=(
                        "保存に失敗しました。"
                        "表示した変更は確定していません。"
                    ),
                ),
            )
            # ストリーム開始後は失敗しても done を 1 回送る。
            await emit(
                self.event_sink,
                done_event(
                    turn_id=state.turn_id,
                    message_id=None,
                    degraded=bool(state.degraded),
                ),
            )

    def _default_tools(self, state: TurnState) -> ConversationToolPort:
        from app.domains.conversation.tool_adapters import ToolAdapters

        session = getattr(self.repository, "session", None)
        if not isinstance(session, AsyncSession):
            raise RuntimeError("既定 ToolAdapters には AsyncSession が必要です")
        return ToolAdapters(
            session,
            event_sink=self.event_sink,
            settings=self.settings,
            spot_names=state.spot_names,
            generation_client=self.llm_client,
        )

    @staticmethod
    def _log_turn(state: TurnState) -> None:
        logger.info(
            "conversation_turn",
            extra={
                **state.log_fields,
                "turn_id": state.turn_id,
                "intent": state.intent.value if state.intent is not None else None,
                "asked": state.log_fields.get("asked"),
                "resumed_from_ask": state.log_fields.get("resumed_from_ask", False),
                "executed_tools": [
                    value.tool.value for value in state.step_results.values()
                ],
                "rejected_steps": [
                    value.model_dump(mode="json") for value in state.rejected_steps
                ],
                "degraded": [value.model_dump(mode="json") for value in state.degraded],
            },
        )


async def run_turn(
    session: AsyncSession,
    *,
    user_id: int,
    utterance: str,
    event_sink: EventSinkLike = None,
    llm_client: GenerationPort | Any | None = None,
    resolves: Mapping[str, Any] | None = None,
    settings: Settings | None = None,
) -> TurnState:
    """次委譲の SSE router が呼ぶ入口。router 自体はここで作らない。"""

    from app.domains.conversation.repository import ConversationRepository

    repository = ConversationRepository(session)
    return await ConversationPipeline(
        repository,
        event_sink=event_sink,
        llm_client=llm_client,
        settings=settings,
    ).run(user_id=user_id, utterance=utterance, resolves=resolves)
