"""load_context → update_profile → main_agent(ReAct) → respond → persist を

固定順で流す旅程計画エージェントの司会(段2: ReAct 構成)。

`Docs/30_design/agent_react_architecture.md` §1 が仕様。旧 6 ノード構成
(understand / validate_plan / act)は廃止し、③ ReAct メインループ
(`main_agent.run_main_agent`)に置き換えた。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.llm import GenerationClient
from app.domains.conversation.ask_registry import AskUserRegistry
from app.domains.conversation.context import ContextRepositoryPort, load_context
from app.domains.conversation.events import (
    EventSinkLike,
    done_event,
    emit,
    error_event,
)
from app.domains.conversation.main_agent import run_main_agent
from app.domains.conversation.persist import (
    HistoryRepositoryFactory,
    PersistRepositoryPort,
    persist,
)
from app.domains.conversation.respond import RespondGenerationError, respond
from app.domains.conversation.state import TurnState
from app.domains.conversation.tool_ports import ConversationToolPort
from app.domains.conversation.update_profile import update_profile

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
        ask_registry: AskUserRegistry | None = None,
    ) -> None:
        self.repository = repository
        self.event_sink = event_sink
        # update_profile / main_agent / respond の全 LLM 呼び出しを同じ
        # 差し替え可能な client へ接続する。
        self.llm_client = llm_client or GenerationClient(settings)
        self.tools = tools
        self.settings = settings
        # `ask_user` の HITL 待ち受け(§7)。API 層(`api/routers/chat.py`)が
        # `request.app.state` から取り出して渡す(`ActiveTurnRegistry` と
        # 同じ流儀)。既定 `ToolAdapters` が実際の待ち受けに使う。
        self.ask_registry = ask_registry or AskUserRegistry()

    async def run(
        self,
        *,
        user_id: int,
        utterance: str,
        turn_id: str | None = None,
    ) -> TurnState:
        # ① load_context
        state = await load_context(
            self.repository,  # type: ignore[arg-type]
            user_id=user_id,
            utterance=utterance,
            turn_id=turn_id,
        )
        try:
            # ② update_profile(load_context の後・メインループの前。§2)
            await update_profile(
                state,
                client=self.llm_client,  # type: ignore[arg-type]
                event_sink=self.event_sink,
            )
        except asyncio.CancelledError:
            await self._persist_or_finish(state)
            self._log_turn(state)
            raise

        # ③ メインエージェント(ReAct ループ)
        tools = self.tools or self._default_tools(state)
        try:
            await run_main_agent(
                state,
                tools=tools,
                client=self.llm_client,  # type: ignore[arg-type]
                event_sink=self.event_sink,
            )
        except asyncio.CancelledError:
            # respond に一度も到達していないので、assistant 行は書かない
            # (§13: persist には必ず到達する。ここまでの状態は保存する)。
            await self._persist_or_finish(state)
            self._log_turn(state)
            raise

        # ④ respond + ⑤ persist
        await self._respond_then_persist(state)
        self._log_turn(state)
        return state

    async def _respond_then_persist(self, state: TurnState) -> None:
        state.responded = True
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
            # ⑤ persist は respond の出口に必ず置く。
            await self._persist_or_finish(state)

    async def _persist_or_finish(self, state: TurnState) -> None:
        try:
            await persist(
                state,
                repository=self.repository,  # type: ignore[arg-type]
                event_sink=self.event_sink,
                history_client=self.llm_client,  # type: ignore[arg-type]
                history_repository_factory=self._history_repository_factory(),
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

    def _history_repository_factory(self) -> HistoryRepositoryFactory | None:
        """履歴要約(§8・裁定19)がバックグラウンドで使う、独立セッションの factory。

        `self.settings` が無ければ(テストの既定経路)`None` を返し、
        `persist()` が `repository` をそのまま使う後方互換経路へフォールバック
        する。本番は必ず `settings` が渡るため、常にこの独立セッション経路
        (`persist()` を呼び出したセッションが閉じても要約タスクは動き続ける)
        を通る。
        """

        if self.settings is None:
            return None
        settings = self.settings

        @asynccontextmanager
        async def factory() -> AsyncIterator[Any]:
            from app.core.db import get_session_factory
            from app.domains.conversation.repository import ConversationRepository

            session_factory = get_session_factory(settings)
            async with session_factory() as session:
                yield ConversationRepository(session)

        return factory

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
            thread_id=state.thread_id,
            user_id=state.user_id,
            ask_registry=self.ask_registry,
        )

    @staticmethod
    def _log_turn(state: TurnState) -> None:
        logger.info(
            "conversation_turn",
            extra={
                **state.log_fields,
                "turn_id": state.turn_id,
                "executed_tools": [step.tool for step in state.trajectory],
                "main_agent_failed": state.main_agent_failed,
                "degraded": [value.model_dump(mode="json") for value in state.degraded],
            },
        )


async def run_turn(
    session: AsyncSession,
    *,
    user_id: int,
    utterance: str,
    event_sink: EventSinkLike = None,
    llm_client: Any | None = None,
    settings: Settings | None = None,
    ask_registry: AskUserRegistry | None = None,
) -> TurnState:
    """次委譲の SSE router が呼ぶ入口。router 自体はここで作らない。"""

    from app.domains.conversation.repository import ConversationRepository

    repository = ConversationRepository(session)
    return await ConversationPipeline(
        repository,
        event_sink=event_sink,
        llm_client=llm_client,
        settings=settings,
        ask_registry=ask_registry,
    ).run(user_id=user_id, utterance=utterance)
