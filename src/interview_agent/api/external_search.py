"""提供本地预览、显式确认的外部证据搜索 HTTP 协议。"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from interview_agent.application import (
    ExternalSearchConfirmationError,
    ExternalSearchPolicyRefusedError,
    ExternalSearchService,
    ExternalSearchUnavailableError,
)

router = APIRouter(prefix="/api/external-search")
_NO_STORE_HEADERS = {"Cache-Control": "no-store", "Pragma": "no-cache"}

_PREVIEW_MESSAGES = {
    "query_allowed": "查询可以在确认后发送给已配置的外部搜索服务。",
    "sensitive_data_detected": (
        "查询包含联系方式、本机路径、凭据或其他敏感标识，不允许外发。"
    ),
    "sensitive_bulk_exfiltration_refused": (
        "查询要求批量或原样输出敏感资料，不允许外发。"
    ),
    "personal_context_requires_local_evidence": (
        "项目、简历和面试记录只能使用本地证据，外部搜索不能补写个人事实。"
    ),
}


class ExternalSearchPreviewApiRequest(BaseModel):
    """预览只接受当前问题，不接收路径、namespace 或提供方。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    question: str = Field(min_length=1, max_length=480)


class ExternalSearchApiRequest(BaseModel):
    """搜索必须同时提交原问题和用户看到的确认查询。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    question: str = Field(min_length=1, max_length=480)
    confirmed_query: str = Field(min_length=1, max_length=480)


class ExternalSearchPreviewApiResponse(BaseModel):
    """不会调用提供方的本地策略结果。"""

    allowed: bool
    reason_code: str
    message: str
    query: str | None
    provider_configured: bool
    provider_name: str | None
    max_results: int


class ExternalSourceApiResponse(BaseModel):
    """与本地 [S] 引用明确分离的临时 [W] 来源。"""

    citation_id: str
    title: str
    url: str
    display_domain: str
    snippet: str
    source_type: str


class ExternalSearchApiResponse(BaseModel):
    """一次搜索结果；不会写入聊天历史。"""

    search_id: str
    query: str
    provider_name: str
    sources: tuple[ExternalSourceApiResponse, ...]
    persisted: bool = False


@router.post("/preview", response_model=ExternalSearchPreviewApiResponse)
def preview_external_search(
    payload: ExternalSearchPreviewApiRequest,
    request: Request,
) -> JSONResponse:
    """完全在本地计算外发预览，不产生网络副作用。"""
    service: ExternalSearchService = request.app.state.external_search_service
    try:
        preview = service.preview(payload.question)
    except ValueError:
        return _error_response(422, "外部查询必须是有效的有限文本。")
    body = ExternalSearchPreviewApiResponse(
        allowed=preview.allowed,
        reason_code=preview.reason_code,
        message=_PREVIEW_MESSAGES.get(
            preview.reason_code,
            "当前问题不能进入外部搜索边界。",
        ),
        query=preview.query,
        provider_configured=preview.provider_configured,
        provider_name=preview.provider_name,
        max_results=preview.max_results,
    )
    return _json_response(body)


@router.post("", response_model=ExternalSearchApiResponse)
def post_external_search(
    payload: ExternalSearchApiRequest,
    request: Request,
) -> JSONResponse:
    """只有确认查询与当前服务端预览一致时才允许一次提供方调用。"""
    service: ExternalSearchService = request.app.state.external_search_service
    try:
        result = service.search(payload.question, payload.confirmed_query)
    except ValueError:
        return _error_response(422, "外部查询必须是有效的有限文本。")
    except ExternalSearchPolicyRefusedError as error:
        return _error_response(
            403,
            _PREVIEW_MESSAGES.get(
                error.reason_code,
                "当前问题不能进入外部搜索边界。",
            ),
        )
    except ExternalSearchConfirmationError:
        return _error_response(409, "确认查询已经变化，请重新预览后再确认。")
    except ExternalSearchUnavailableError:
        return _error_response(
            503,
            "外部搜索服务尚未配置或当前不可用，本地问答不受影响。",
        )
    body = ExternalSearchApiResponse(
        search_id=result.search_id,
        query=result.query,
        provider_name=result.provider_name,
        sources=tuple(
            ExternalSourceApiResponse(
                citation_id=source.citation_id,
                title=source.title,
                url=source.url,
                display_domain=source.display_domain,
                snippet=source.snippet,
                source_type=source.source_type,
            )
            for source in result.sources
        ),
    )
    return _json_response(body)


def _json_response(body: BaseModel) -> JSONResponse:
    """外部预览和结果都不允许浏览器或中间层缓存。"""
    return JSONResponse(
        content=body.model_dump(mode="json"),
        headers=_NO_STORE_HEADERS,
    )


def _error_response(status_code: int, detail: str) -> JSONResponse:
    """不把提供方异常、查询原文或凭据写入错误响应。"""
    return JSONResponse(
        status_code=status_code,
        content={"detail": detail},
        headers=_NO_STORE_HEADERS,
    )


__all__ = ["router"]
