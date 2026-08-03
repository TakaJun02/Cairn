"""鳥海山観光の 1 ターンを司会する conversation ドメイン。"""

from __future__ import annotations

from typing import Any

from app.domains.conversation.events import ConversationEvent, MemoryEventSink
from app.domains.conversation.types import (
    Intent,
    ResponseMode,
    ToolError,
    ToolErrorCode,
    ToolName,
    UnderstandOutput,
)

__all__ = [
    "ConversationEvent",
    "ConversationPipeline",
    "Intent",
    "MemoryEventSink",
    "ResponseMode",
    "ToolError",
    "ToolErrorCode",
    "ToolName",
    "TurnState",
    "UnderstandOutput",
    "run_turn",
]


def __getattr__(name: str) -> Any:
    """DB ドライバを Tool 実行前に読み込まない遅延公開面。"""

    if name in {"ConversationPipeline", "run_turn"}:
        from app.domains.conversation.pipeline import ConversationPipeline, run_turn

        return {"ConversationPipeline": ConversationPipeline, "run_turn": run_turn}[name]
    if name == "TurnState":
        from app.domains.conversation.state import TurnState

        return TurnState
    raise AttributeError(name)
