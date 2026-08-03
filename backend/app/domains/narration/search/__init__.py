"""知識検索サブエージェントの公開面。"""

from app.core.config import Settings
from app.domains.narration.search.agent import (
    AskCallback,
    KnowledgeSearchAgent,
    SearchEventSink,
)
from app.domains.narration.search.types import (
    KnowledgeSource,
    SearchResult,
    SearchStateEvent,
    SearchTrace,
    ToolError,
    ToolErrorCode,
    WebSource,
)


async def search_knowledge(
    request: str,
    spot_id: str | None = None,
    *,
    settings: Settings | None = None,
    event_sink: SearchEventSink | None = None,
    ask_callback: AskCallback | None = None,
) -> SearchResult | ToolError:
    """DB 実装は実際の Tool 呼び出し時まで import しない。"""

    from app.domains.narration.search.service import search_knowledge as run

    return await run(
        request,
        spot_id,
        settings=settings,
        event_sink=event_sink,
        ask_callback=ask_callback,
    )

__all__ = [
    "AskCallback",
    "KnowledgeSearchAgent",
    "KnowledgeSource",
    "SearchResult",
    "SearchStateEvent",
    "SearchTrace",
    "ToolError",
    "ToolErrorCode",
    "WebSource",
    "search_knowledge",
]
