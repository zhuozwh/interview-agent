"""定义所有只读 Tool 共用的最小追踪记录协议。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, TypeAlias

TraceParameter: TypeAlias = str | int | float | bool


@dataclass(frozen=True, slots=True)
class ToolTraceRecord:
    """不含问题正文和笔记正文的一次 Tool 调用审计记录。"""

    tool_call_id: str
    trace_id: str
    tool_name: str
    started_at: str
    completed_at: str
    duration_ms: int
    status: str
    result_count: int
    parameters: tuple[tuple[str, TraceParameter], ...]
    result_ids: tuple[str, ...]
    error_code: str | None = None


class ToolTraceStore(Protocol):
    """Tool 只依赖这一条写入能力，不直接拼接 SQLite 查询。"""

    def record(self, trace: ToolTraceRecord) -> None:
        """持久化一条不含敏感正文的调用记录。"""
