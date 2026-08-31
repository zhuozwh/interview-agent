"""组织已保存引用到当前只读 Markdown 原文的核验用例。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import TYPE_CHECKING, Protocol
from uuid import UUID

from interview_agent.core.config import Settings
from interview_agent.retrieval.evidence import (
    LocalEvidenceReadError,
    read_local_markdown_evidence,
)

if TYPE_CHECKING:
    from interview_agent.application.history import ConversationHistoryService

_CITATION_ID = re.compile(r"S[1-9][0-9]{0,2}")


class CitationEvidenceNotFoundError(LookupError):
    """会话、轮次或引用不存在。"""


class CitationEvidenceSourceUnavailableError(RuntimeError):
    """已登记来源已经无法安全读取。"""


class CitationEvidenceUnavailableError(RuntimeError):
    """历史或证据服务当前不可用。"""


@dataclass(frozen=True, slots=True)
class CitationEvidence:
    """交互层可展示的当前本地证据，不含绝对路径。"""

    citation_id: str
    source_namespace: str
    relative_path: str
    heading_path: tuple[str, ...]
    citation_start_line: int
    citation_end_line: int
    excerpt_start_line: int
    excerpt_end_line: int
    score: float
    content: str
    truncated: bool


class CitationEvidenceService(Protocol):
    """HTTP 层依赖的本地引用核验边界。"""

    def read(
        self,
        session_id: str,
        trace_id: str,
        citation_id: str,
    ) -> CitationEvidence:
        """按三个已登记身份读取当前原文。"""


class LocalCitationEvidenceService:
    """先查受控历史，再从固定 namespace 根目录只读取证。"""

    def __init__(
        self,
        settings: Settings,
        history_service: ConversationHistoryService,
    ) -> None:
        self.settings = settings
        self.history_service = history_service

    def read(
        self,
        session_id: str,
        trace_id: str,
        citation_id: str,
    ) -> CitationEvidence:
        """浏览器不能提供路径；路径只能来自已保存引用。"""
        _require_uuid(session_id, "session_id")
        _require_uuid(trace_id, "trace_id")
        if not isinstance(citation_id, str) or not _CITATION_ID.fullmatch(citation_id):
            raise ValueError("citation_id must use the canonical S-number format")
        try:
            session = self.history_service.load_session(session_id)
        except ValueError:
            raise
        except Exception as error:
            raise CitationEvidenceUnavailableError(
                "Local citation evidence is unavailable."
            ) from error
        if session is None:
            raise CitationEvidenceNotFoundError("Citation evidence was not found.")
        matching_turns = tuple(
            turn for turn in session.turns if turn.trace_id == trace_id
        )
        if len(matching_turns) != 1:
            raise CitationEvidenceNotFoundError("Citation evidence was not found.")
        matching_citations = tuple(
            citation
            for citation in matching_turns[0].citations
            if citation.citation_id == citation_id
        )
        if len(matching_citations) != 1:
            raise CitationEvidenceNotFoundError("Citation evidence was not found.")
        citation = matching_citations[0]
        source_directory = _source_directory(
            self.settings,
            citation.source_namespace,
        )
        try:
            excerpt = read_local_markdown_evidence(
                source_directory,
                self.settings.allowed_data_directories,
                citation.relative_path,
                citation.start_line,
                citation.end_line,
                max_file_size_bytes=self.settings.markdown_max_file_size_bytes,
            )
        except LocalEvidenceReadError as error:
            raise CitationEvidenceSourceUnavailableError(
                "The cited local source is no longer available."
            ) from error
        return CitationEvidence(
            citation_id=citation.citation_id,
            source_namespace=citation.source_namespace,
            relative_path=citation.relative_path,
            heading_path=citation.heading_path,
            citation_start_line=citation.start_line,
            citation_end_line=citation.end_line,
            excerpt_start_line=excerpt.start_line,
            excerpt_end_line=excerpt.end_line,
            score=citation.score,
            content=excerpt.content,
            truncated=excerpt.truncated,
        )


def _source_directory(settings: Settings, namespace: str) -> Path:
    """namespace 只能映射到三个固定只读根，未知值不做猜测。"""
    directories = {
        "notes": settings.markdown_source_directory,
        "projects": settings.project_source_directory,
        "resume": settings.resume_source_directory,
    }
    try:
        return directories[namespace]
    except KeyError as error:
        raise CitationEvidenceSourceUnavailableError(
            "The cited local source namespace is not available."
        ) from error


def _require_uuid(value: object, label: str) -> None:
    """路径身份只接受规范 UUID，避免多种编码表示同一记录。"""
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a canonical UUID string")
    try:
        normalized = str(UUID(value))
    except ValueError as error:
        raise ValueError(f"{label} must be a canonical UUID string") from error
    if normalized != value:
        raise ValueError(f"{label} must be a canonical UUID string")


__all__ = [
    "CitationEvidence",
    "CitationEvidenceNotFoundError",
    "CitationEvidenceService",
    "CitationEvidenceSourceUnavailableError",
    "CitationEvidenceUnavailableError",
    "LocalCitationEvidenceService",
]
