"""知識検索サブエージェントの公開契約と診断型。"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SearchModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class KnowledgeSource(SearchModel):
    kind: Literal["knowledge"] = "knowledge"
    doc_id: str
    title: str
    spot_id: str | None = None


class WebSource(SearchModel):
    kind: Literal["web"] = "web"
    url: str
    title: str


SearchSource = KnowledgeSource | WebSource


class SearchResult(SearchModel):
    """メインエージェントから見える唯一の正常系戻り値。"""

    answer_ja: str
    sources: list[SearchSource]
    coverage: Literal["full", "partial", "none"]

    @model_validator(mode="after")
    def validate_coverage(self) -> SearchResult:
        if self.coverage != "none" and not self.sources:
            raise ValueError("full/partial の回答には出典が必要です")
        if self.coverage == "none" and self.sources:
            raise ValueError("coverage=none の回答に出典は付けられません")
        return self


class ToolErrorCode(StrEnum):
    UPSTREAM_TIMEOUT = "upstream_timeout"


class ToolError(SearchModel):
    code: ToolErrorCode
    message_ja: str
    recoverable: bool = True
    details: dict[str, Any] = Field(default_factory=dict)


class SearchStateEvent(SearchModel):
    """次委譲で SSE の state イベントへ接続する差し込み口。"""

    kind: Literal["searching"] = "searching"
    text: str


class SearchTraceStep(SearchModel):
    round: int
    thought: str
    tool: str
    args: dict[str, Any]
    context_tokens: int
    repeated: bool = False


class SearchTrace(SearchModel):
    steps: list[SearchTraceStep] = Field(default_factory=list)
    tool_executions: int = 0
    soft_budget_reached: bool = False
    hard_budget_reached: bool = False
    recursion_safety_used: bool = False
