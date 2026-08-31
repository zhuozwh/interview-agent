"""公开本地应用用例、会话结果和追踪协议。"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from interview_agent.application.ask import (
        AgentExecutor,
        AskInterviewAgentUseCase,
    )
    from interview_agent.application.evidence import (
        CitationEvidence,
        CitationEvidenceNotFoundError,
        CitationEvidenceService,
        CitationEvidenceSourceUnavailableError,
        CitationEvidenceUnavailableError,
        LocalCitationEvidenceService,
    )
    from interview_agent.application.external_search import (
        ControlledExternalSearchService,
        ExternalSearchCandidate,
        ExternalSearchConfirmationError,
        ExternalSearchPolicyRefusedError,
        ExternalSearchPreview,
        ExternalSearchProvider,
        ExternalSearchResult,
        ExternalSearchService,
        ExternalSearchUnavailableError,
        ExternalSource,
    )
    from interview_agent.application.history import (
        ConversationHistoryInfo,
        ConversationHistoryService,
        ConversationHistoryUnavailableError,
        LocalConversationHistoryService,
    )
    from interview_agent.application.history_models import (
        HistoryCitation,
        HistorySession,
        HistorySessionSummary,
        HistoryTurn,
    )
    from interview_agent.application.models import (
        AgentTraceRecord,
        AgentTraceStore,
        AskResult,
    )
    from interview_agent.application.runtime import (
        ApplicationUnavailableError,
        AskService,
        LazyLocalAskService,
        LocalInterviewRuntime,
        build_local_runtime,
    )


# application 的历史实现依赖 storage，而 storage 的历史表只依赖轻量模型。
# 按公开符号延迟导入，避免“先导入 storage”时由包初始化形成循环依赖。
_EXPORT_MODULES = {
    "AgentExecutor": "interview_agent.application.ask",
    "AskInterviewAgentUseCase": "interview_agent.application.ask",
    "CitationEvidence": "interview_agent.application.evidence",
    "CitationEvidenceNotFoundError": "interview_agent.application.evidence",
    "CitationEvidenceService": "interview_agent.application.evidence",
    "CitationEvidenceSourceUnavailableError": (
        "interview_agent.application.evidence"
    ),
    "CitationEvidenceUnavailableError": "interview_agent.application.evidence",
    "LocalCitationEvidenceService": "interview_agent.application.evidence",
    "ControlledExternalSearchService": (
        "interview_agent.application.external_search"
    ),
    "ExternalSearchCandidate": "interview_agent.application.external_search",
    "ExternalSearchConfirmationError": (
        "interview_agent.application.external_search"
    ),
    "ExternalSearchPolicyRefusedError": (
        "interview_agent.application.external_search"
    ),
    "ExternalSearchPreview": "interview_agent.application.external_search",
    "ExternalSearchProvider": "interview_agent.application.external_search",
    "ExternalSearchResult": "interview_agent.application.external_search",
    "ExternalSearchService": "interview_agent.application.external_search",
    "ExternalSearchUnavailableError": (
        "interview_agent.application.external_search"
    ),
    "ExternalSource": "interview_agent.application.external_search",
    "ConversationHistoryInfo": "interview_agent.application.history",
    "ConversationHistoryService": "interview_agent.application.history",
    "ConversationHistoryUnavailableError": "interview_agent.application.history",
    "LocalConversationHistoryService": "interview_agent.application.history",
    "HistoryCitation": "interview_agent.application.history_models",
    "HistorySession": "interview_agent.application.history_models",
    "HistorySessionSummary": "interview_agent.application.history_models",
    "HistoryTurn": "interview_agent.application.history_models",
    "AgentTraceRecord": "interview_agent.application.models",
    "AgentTraceStore": "interview_agent.application.models",
    "AskResult": "interview_agent.application.models",
    "ApplicationUnavailableError": "interview_agent.application.runtime",
    "AskService": "interview_agent.application.runtime",
    "LazyLocalAskService": "interview_agent.application.runtime",
    "LocalInterviewRuntime": "interview_agent.application.runtime",
    "build_local_runtime": "interview_agent.application.runtime",
}

__all__ = list(_EXPORT_MODULES)


def __getattr__(name: str):
    """首次访问公开符号时才加载对应实现，并缓存到当前包。"""
    try:
        module_name = _EXPORT_MODULES[name]
    except KeyError as error:
        raise AttributeError(
            f"module {__name__!r} has no attribute {name!r}"
        ) from error
    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """让交互式检查和文档工具仍能看到全部公开符号。"""
    return sorted({*globals(), *__all__})
