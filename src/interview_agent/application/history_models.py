"""定义本地聊天历史的受控正文协议。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class HistoryCitation:
    """只保留界面定位来源所需的相对引用元数据。"""

    citation_id: str
    source_namespace: str
    relative_path: str
    heading_path: tuple[str, ...]
    start_line: int
    end_line: int
    score: float


@dataclass(frozen=True, slots=True)
class HistoryTurn:
    """一个可恢复的用户问题与对应 Agent 结果。"""

    trace_id: str
    session_id: str
    created_at: str
    question: str
    answer: str | None
    status: str
    intent: str | None
    error_code: str | None
    error_message: str | None
    confidence: str | None
    citations: tuple[HistoryCitation, ...]
    follow_up_questions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class HistorySessionSummary:
    """侧边栏展示所需的会话摘要，不包含问答正文。"""

    session_id: str
    title: str
    created_at: str
    updated_at: str
    turn_count: int


@dataclass(frozen=True, slots=True)
class HistorySession:
    """一个会话摘要及按时间排序的有限问答轮次。"""

    summary: HistorySessionSummary
    turns: tuple[HistoryTurn, ...]


class ConversationHistoryStore(Protocol):
    """应用层依赖的聊天历史存储边界。"""

    def initialize(self) -> None:
        """幂等初始化历史表。"""

    def record_turn(self, turn: HistoryTurn) -> None:
        """追加一轮并执行数量与时间清理。"""

    def list_sessions(self) -> tuple[HistorySessionSummary, ...]:
        """返回最近更新优先的有限会话列表。"""

    def load_session(self, session_id: str) -> HistorySession | None:
        """读取一个会话，不存在时返回空。"""

    def delete_session(self, session_id: str) -> bool:
        """删除一个会话的正文历史。"""

    def clear(self) -> int:
        """删除全部聊天正文历史并返回会话数量。"""

    def prune(self) -> int:
        """按当前保留策略删除过期或超量会话。"""


__all__ = [
    "ConversationHistoryStore",
    "HistoryCitation",
    "HistorySession",
    "HistorySessionSummary",
    "HistoryTurn",
]
