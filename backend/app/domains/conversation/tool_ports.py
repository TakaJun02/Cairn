"""Tool アダプタの純粋 Port。DB 実装を import せず executor をテストできる。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Protocol

from app.domains.conversation.types import (
    AskUserArgs,
    ConstraintDraft,
    EditItineraryArgs,
    PlanItineraryArgs,
    RecommendArgs,
    SearchKnowledgeArgs,
    ToolError,
    ToolResult,
)
from app.domains.recommendation.types import RecommendationContext

# 知識検索 SA(narration)へ注入する ask コールバック(port)。§6:
# narration → conversation の逆依存を作らないため、narration 側は
# `surface`/`reason`/`options`(label/value の dict)と観測文字列だけを知る。
SearchAskCallback = Callable[..., Awaitable[str]]


class ConversationToolPort(Protocol):
    async def recommend(
        self,
        *,
        step_id: int,
        args: RecommendArgs,
        context: RecommendationContext,
        use_specialist: bool,
    ) -> ToolResult | ToolError: ...

    async def plan_itinerary(
        self,
        *,
        step_id: int,
        user_id: int,
        args: PlanItineraryArgs,
        constraints: Sequence[ConstraintDraft],
        selection_text: str,
        recommendation_context: RecommendationContext,
        use_specialist: bool,
    ) -> ToolResult | ToolError: ...

    async def edit_itinerary(
        self,
        *,
        step_id: int,
        user_id: int,
        args: EditItineraryArgs,
        constraints: Sequence[ConstraintDraft],
        constraints_remove: Sequence[str],
        selection_text: str,
        recommendation_context: RecommendationContext,
        use_specialist: bool,
    ) -> ToolResult | ToolError: ...

    async def search_knowledge(
        self,
        *,
        step_id: int,
        args: SearchKnowledgeArgs,
        ask_callback: SearchAskCallback | None = None,
    ) -> ToolResult | ToolError: ...

    async def ask_user(
        self,
        *,
        step_id: int,
        args: AskUserArgs,
    ) -> ToolResult | ToolError: ...
