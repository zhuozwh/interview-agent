"""提供只按已保存引用身份读取的本地证据 HTTP 接口。"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from interview_agent.application import (
    CitationEvidenceNotFoundError,
    CitationEvidenceService,
    CitationEvidenceSourceUnavailableError,
    CitationEvidenceUnavailableError,
)

router = APIRouter(prefix="/api/evidence")
_NO_STORE_HEADERS = {"Cache-Control": "no-store", "Pragma": "no-cache"}


class CitationEvidenceApiResponse(BaseModel):
    """当前本地文件中的有界片段，不暴露绝对路径。"""

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


@router.get(
    "/{session_id}/{trace_id}/{citation_id}",
    response_model=CitationEvidenceApiResponse,
)
def get_citation_evidence(
    session_id: str,
    trace_id: str,
    citation_id: str,
    request: Request,
) -> JSONResponse:
    """只使用三个身份查找服务端引用，不接受 namespace 或文件路径。"""
    service: CitationEvidenceService = request.app.state.evidence_service
    try:
        evidence = service.read(session_id, trace_id, citation_id)
    except ValueError:
        return _error_response(422, "Evidence identity is invalid.")
    except CitationEvidenceNotFoundError:
        return _error_response(404, "Citation evidence was not found.")
    except CitationEvidenceSourceUnavailableError:
        return _error_response(410, "The cited local source is no longer available.")
    except CitationEvidenceUnavailableError:
        return _error_response(503, "Local citation evidence is unavailable.")
    body = CitationEvidenceApiResponse(
        citation_id=evidence.citation_id,
        source_namespace=evidence.source_namespace,
        relative_path=evidence.relative_path,
        heading_path=evidence.heading_path,
        citation_start_line=evidence.citation_start_line,
        citation_end_line=evidence.citation_end_line,
        excerpt_start_line=evidence.excerpt_start_line,
        excerpt_end_line=evidence.excerpt_end_line,
        score=evidence.score,
        content=evidence.content,
        truncated=evidence.truncated,
    )
    return JSONResponse(
        content=body.model_dump(mode="json"),
        headers=_NO_STORE_HEADERS,
    )


def _error_response(status_code: int, detail: str) -> JSONResponse:
    """证据错误不回显 SQLite、绝对路径或底层读取异常。"""
    return JSONResponse(
        status_code=status_code,
        content={"detail": detail},
        headers=_NO_STORE_HEADERS,
    )


__all__ = ["router"]
