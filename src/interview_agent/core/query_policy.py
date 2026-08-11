"""集中实现检索前边界、事实锚点和轻量候选排序策略。"""

from __future__ import annotations

import re
from dataclasses import dataclass


_REVIEW_MARKERS = (
    "面试复盘",
    "面试记录",
    "面试表现",
    "面试官问",
    "复盘这场面试",
)
_RESUME_MARKERS = (
    "我的简历",
    "简历里",
    "简历中",
    "简历版本",
    "完整简历",
    "我的经历",
    "我做过",
    "我的实习",
    "实习经历",
    "后端实习",
    "实习",
    "个人经历",
    "resume",
)
_PROJECT_MARKERS = (
    "我的项目",
    "项目中",
    "项目里",
    "这个项目",
    "服务框架",
    "当前实现",
    "实现状态",
    "interview-agent",
    "interview agent",
)

_EXFIL_ACTION_MARKERS = (
    "输出",
    "展示",
    "列出",
    "打印",
    "导出",
    "返回",
    "给我",
    "发给",
    "提供",
)
_EXFIL_SENSITIVE_MARKERS = (
    "简历原文",
    "完整简历",
    "全部联系方式",
    "所有联系方式",
    "绝对路径",
    "本机路径",
    "原始路径",
    "身份证",
    "手机号",
    "手机号码",
    "邮箱",
    "微信",
    "wechat",
    "file://",
)
_EXFIL_BULK_OR_BYPASS_MARKERS = (
    "忽略规则",
    "忽略上述",
    "绕过",
    "不要脱敏",
    "取消脱敏",
    "原样",
    "完整",
    "全部",
    "所有",
)
_DIRECT_PATH_MARKERS = ("绝对路径", "本机路径", "原始路径", "file://")

_FACT_VERIFICATION_MARKERS = (
    "是否",
    "有没有",
    "有无",
    "用过",
    "使用过",
    "采用过",
    "做过",
    "参与过",
    "负责过",
    "实现过",
    "存在",
    "包含",
    "曾在",
    "实习过",
    "工作过",
    "具备",
    "支持",
)
_LATIN_TOKEN_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_.+#-]{1,31}")
_QUOTED_TEXT_PATTERN = re.compile(r"[\"“‘']([^\"”’']{2,32})[\"”’']")
_ORGANIZATION_PATTERNS = (
    re.compile(
        r"(?:在|加入)([\u4e00-\u9fffA-Za-z0-9]{2,16})"
        r"(?:担任|任职|实习|工作)"
    ),
    re.compile(
        r"([\u4e00-\u9fffA-Za-z0-9]{2,16}"
        r"(?:公司|集团|科技|银行|研究院|实验室))"
    ),
)
_ATTRIBUTE_PAIR_PATTERN = re.compile(
    r"包含(?:了)?([\u4e00-\u9fff]{2,8})(?:和|及|、)([\u4e00-\u9fff]{2,8})"
)
_CURRENT_MARKERS = ("当前", "现在", "目前", "现用", "最新版", "开发版")
_HISTORY_MARKERS = ("历史", "归档", "旧版", "旧版本", "曾经", "此前")
_REFERENCE_MARKERS = (
    "它",
    "这个",
    "该项目",
    "该实现",
    "该技术",
    "该系统",
    "该经历",
    "该方案",
    "该组件",
    "该方法",
    "该职责",
    "该功能",
    "该设计",
    "该版本",
    "该内容",
    "上述",
    "前面",
    "刚才",
    "其中",
    "那个",
    "那一",
    "那段",
    "那项",
    "那套",
)


@dataclass(frozen=True, slots=True)
class QueryPolicyDecision:
    """不携带问题正文的确定性检索前判定。"""

    allowed: bool
    reason_code: str


def infer_query_namespace(question: str) -> str | None:
    """按产品职责识别查询应进入的唯一 namespace。"""
    normalized = _normalize_question(question)
    if any(marker in normalized for marker in _REVIEW_MARKERS):
        return None
    if any(marker in normalized for marker in _RESUME_MARKERS):
        return "resume"
    if any(marker in normalized for marker in _PROJECT_MARKERS):
        return "projects"
    return "notes"


def assess_pre_retrieval_policy(
    question: str,
    *,
    target_namespace: str | None = None,
) -> QueryPolicyDecision:
    """在 Embedding 和向量查询前拒绝明显外带与跨域访问。"""
    normalized = _normalize_question(question)
    if _is_obvious_exfiltration(normalized):
        return QueryPolicyDecision(
            allowed=False,
            reason_code="sensitive_bulk_exfiltration_refused",
        )

    inferred_namespace = infer_query_namespace(normalized)
    if target_namespace is not None and inferred_namespace != target_namespace:
        return QueryPolicyDecision(
            allowed=False,
            reason_code="query_namespace_mismatch",
        )
    return QueryPolicyDecision(allowed=True, reason_code="query_allowed")


def requires_fact_evidence(question: str) -> bool:
    """识别“是否存在/使用/做过”等必须由明确证据确认的问题。"""
    normalized = _normalize_question(question)
    return any(marker in normalized for marker in _FACT_VERIFICATION_MARKERS)


def extract_query_anchors(question: str) -> tuple[str, ...]:
    """提取必须在证据中直接出现的技术名词、实体或限定词。"""
    _normalize_question(question)
    anchors: list[str] = []
    anchors.extend(token.casefold() for token in _LATIN_TOKEN_PATTERN.findall(question))
    anchors.extend(match.strip().casefold() for match in _QUOTED_TEXT_PATTERN.findall(question))
    for pattern in _ORGANIZATION_PATTERNS:
        anchors.extend(match.strip().casefold() for match in pattern.findall(question))
    for first, second in _ATTRIBUTE_PAIR_PATTERN.findall(question):
        anchors.extend((first.casefold(), second.casefold()))
    return tuple(dict.fromkeys(anchor for anchor in anchors if len(anchor) >= 2))


def has_sufficient_fact_evidence(
    question: str,
    evidence_contents: tuple[str, ...],
    *,
    source_namespace: str,
) -> bool:
    """事实确认问题要求具体锚点由本次候选证据直接覆盖。"""
    if source_namespace == "notes":
        return True
    anchors = extract_query_anchors(question)
    if not anchors:
        return True
    combined = "\n".join(evidence_contents).casefold()
    # 多组件问题可能由同一文档的相邻片段共同说明；这里至少要求一个具体
    # 锚点真实出现，剩余锚点由回答协议明确标为未获证据支持，不能据主题推断。
    return any(anchor in combined for anchor in anchors)


def candidate_relevance_adjustment(question: str, content: str) -> float:
    """只在已召回候选内使用小幅词面与时间一致性修正。"""
    normalized_question = _normalize_question(question)
    normalized_content = content.casefold()
    anchors = extract_query_anchors(question)
    coverage = (
        sum(anchor in normalized_content for anchor in anchors) / len(anchors)
        if anchors
        else 0.0
    )
    adjustment = 0.03 * coverage

    asks_current = any(marker in normalized_question for marker in _CURRENT_MARKERS)
    asks_history = any(marker in normalized_question for marker in _HISTORY_MARKERS)
    content_current = any(marker in normalized_content for marker in _CURRENT_MARKERS)
    content_history = any(marker in normalized_content for marker in _HISTORY_MARKERS)
    if asks_current:
        adjustment += 0.04 if content_current else 0.0
        adjustment -= 0.04 if content_history and not content_current else 0.0
    elif asks_history:
        adjustment += 0.04 if content_history else 0.0
        adjustment -= 0.04 if content_current and not content_history else 0.0
    return adjustment


def resolve_question_reference(
    question: str,
    previous_question: str | None,
) -> tuple[str, bool]:
    """只合并一条被当前问题显式指代的用户问题，不继承旧回答或引用。"""
    normalized = _normalize_question(question)
    if previous_question is None or not any(
        marker in normalized for marker in _REFERENCE_MARKERS
    ):
        return question.strip(), False
    if not isinstance(previous_question, str) or not previous_question.strip():
        raise ValueError("previous_question must be a non-empty string")
    return (
        f"上一轮问题：{previous_question.strip()}\n"
        f"当前追问：{question.strip()}",
        True,
    )


def _is_obvious_exfiltration(normalized: str) -> bool:
    """只拦截高确定性的批量、原样或路径外带，不扩展为完整语义 DLP。"""
    has_action = any(marker in normalized for marker in _EXFIL_ACTION_MARKERS)
    has_sensitive_target = any(
        marker in normalized for marker in _EXFIL_SENSITIVE_MARKERS
    )
    has_bulk_or_bypass = any(
        marker in normalized for marker in _EXFIL_BULK_OR_BYPASS_MARKERS
    )
    has_direct_path_target = any(
        marker in normalized for marker in _DIRECT_PATH_MARKERS
    )
    return has_action and has_sensitive_target and (
        has_bulk_or_bypass or has_direct_path_target
    )


def _normalize_question(question: str) -> str:
    """内部分析只接受非空文本，调用方仍负责长度和控制字符边界。"""
    if not isinstance(question, str) or not question.strip():
        raise ValueError("question must be a non-empty string")
    return question.strip().casefold()


__all__ = [
    "QueryPolicyDecision",
    "assess_pre_retrieval_policy",
    "candidate_relevance_adjustment",
    "extract_query_anchors",
    "has_sufficient_fact_evidence",
    "infer_query_namespace",
    "requires_fact_evidence",
    "resolve_question_reference",
]
