"""组织聊天正文的本地保存、读取和删除生命周期。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Protocol

from interview_agent.agent import AgentRequest
from interview_agent.application.history_models import (
    HistoryCitation,
    HistorySession,
    HistorySessionSummary,
    HistoryTurn,
)
from interview_agent.application.models import AskResult
from interview_agent.application.runtime import (
    validate_local_storage_boundaries,
)
from interview_agent.core.config import Settings
from interview_agent.storage import (
    SQLiteConversationHistoryStore,
    SQLiteDatabase,
)


class ConversationHistoryUnavailableError(RuntimeError):
    """历史存储无法安全初始化或读取。"""


@dataclass(frozen=True, slots=True)
class ConversationHistoryInfo:
    """向本地界面解释数据位置和生命周期的非敏感配置。"""

    enabled: bool
    database_path: str
    retention_days: int
    max_sessions: int
    max_turns_per_session: int


class ConversationHistoryService(Protocol):
    """FastAPI 依赖的应用级历史用例。"""

    @property
    def info(self) -> ConversationHistoryInfo:
        """返回当前历史策略。"""

    def record(self, request: AgentRequest, result: AskResult) -> str:
        """返回 saved、disabled 或 failed。"""

    def list_sessions(self) -> tuple[HistorySessionSummary, ...]:
        """读取最近会话。"""

    def load_session(self, session_id: str) -> HistorySession | None:
        """读取一个会话。"""

    def delete_session(self, session_id: str) -> bool:
        """删除一个会话的正文历史。"""

    def clear(self) -> int:
        """删除全部聊天正文历史。"""


class LocalConversationHistoryService:
    """延迟建立 SQLite 历史存储，避免健康检查产生运行时文件。"""

    def __init__(
        self,
        settings: Settings,
        *,
        store: SQLiteConversationHistoryStore | None = None,
    ) -> None:
        self.settings = settings
        self._store = store
        self._initialized = store is not None
        self._lock = Lock()

    @property
    def info(self) -> ConversationHistoryInfo:
        """返回配置值；路径按用户配置显示，不解析成绝对路径。"""
        return ConversationHistoryInfo(
            enabled=self.settings.session_history_enabled,
            database_path=str(self.settings.database_path),
            retention_days=self.settings.session_history_retention_days,
            max_sessions=self.settings.session_history_max_sessions,
            max_turns_per_session=(
                self.settings.session_history_max_turns_per_session
            ),
        )

    def record(self, request: AgentRequest, result: AskResult) -> str:
        """保存界面恢复字段；失败不诱导调用方自动重试 Agent。"""
        if not self.settings.session_history_enabled:
            return "disabled"
        try:
            store = self._require_store()
            response = result.response
            store.record_turn(
                HistoryTurn(
                    trace_id=response.trace_id,
                    session_id=result.session_id,
                    created_at=datetime.now(timezone.utc).isoformat(),
                    question=request.question.strip(),
                    answer=response.answer,
                    status=response.status.value,
                    intent=(
                        response.intent.value
                        if response.intent is not None
                        else None
                    ),
                    error_code=(
                        response.error.code
                        if response.error is not None
                        else None
                    ),
                    error_message=(
                        response.error.message
                        if response.error is not None
                        else None
                    ),
                    confidence=(
                        response.confidence.value
                        if response.confidence is not None
                        else None
                    ),
                    citations=tuple(
                        HistoryCitation(
                            citation_id=citation.citation_id,
                            source_namespace=citation.source_namespace,
                            relative_path=citation.relative_path,
                            heading_path=citation.heading_path,
                            start_line=citation.start_line,
                            end_line=citation.end_line,
                            score=citation.score,
                        )
                        for citation in response.citations
                    ),
                    follow_up_questions=response.follow_up_questions,
                )
            )
        except Exception:
            # 远端调用可能已经完成；返回 failed 让界面提示复制答案，不能自动重试。
            return "failed"
        return "saved"

    def list_sessions(self) -> tuple[HistorySessionSummary, ...]:
        """历史关闭时返回空；读取失败映射为稳定应用错误。"""
        if not self.settings.session_history_enabled:
            return ()
        try:
            return self._require_store().list_sessions()
        except Exception as error:
            raise ConversationHistoryUnavailableError(
                "Local conversation history is unavailable."
            ) from error

    def load_session(self, session_id: str) -> HistorySession | None:
        """读取单会话，不把 SQLite 或路径异常穿透给 HTTP。"""
        if not self.settings.session_history_enabled:
            return None
        try:
            return self._require_store().load_session(session_id)
        except ValueError:
            raise
        except Exception as error:
            raise ConversationHistoryUnavailableError(
                "Local conversation history is unavailable."
            ) from error

    def delete_session(self, session_id: str) -> bool:
        """删除单会话正文；历史关闭时按不存在处理。"""
        if not self.settings.session_history_enabled:
            return False
        try:
            return self._require_store().delete_session(session_id)
        except ValueError:
            raise
        except Exception as error:
            raise ConversationHistoryUnavailableError(
                "Local conversation history is unavailable."
            ) from error

    def clear(self) -> int:
        """显式清空全部聊天正文，不删除 Phase 2 无正文审计。"""
        if not self.settings.session_history_enabled:
            return 0
        try:
            return self._require_store().clear()
        except Exception as error:
            raise ConversationHistoryUnavailableError(
                "Local conversation history is unavailable."
            ) from error

    def _require_store(self) -> SQLiteConversationHistoryStore:
        """只在首次历史操作时验证边界并创建数据表。"""
        if self._initialized and self._store is not None:
            return self._store
        with self._lock:
            if self._initialized and self._store is not None:
                return self._store
            validate_local_storage_boundaries(self.settings)
            store = SQLiteConversationHistoryStore(
                SQLiteDatabase(self.settings.database_path),
                retention_days=self.settings.session_history_retention_days,
                max_sessions=self.settings.session_history_max_sessions,
                max_turns_per_session=(
                    self.settings.session_history_max_turns_per_session
                ),
            )
            store.initialize()
            store.prune()
            self._store = store
            self._initialized = True
            return store


__all__ = [
    "ConversationHistoryInfo",
    "ConversationHistoryService",
    "ConversationHistoryUnavailableError",
    "LocalConversationHistoryService",
]
