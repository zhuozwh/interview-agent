"""冻结 v0.4.4 的匿名有限上下文、跨域和隐私回归矩阵。"""

import json
from pathlib import Path

import pytest

from interview_agent.agent import AgentIntent, route_question
from interview_agent.core.query_policy import (
    assess_pre_retrieval_policy,
    resolve_question_reference,
)

_FIXTURE_PATH = (
    Path(__file__).parent / "fixtures" / "phase2_v044" / "context_cases.json"
)
_INTENT_NAMESPACE = {
    AgentIntent.KNOWLEDGE_QUESTION: "notes",
    AgentIntent.PROJECT_CONTEXT: "projects",
    AgentIntent.RESUME_CONTEXT: "resume",
}


def _load_cases() -> list[dict[str, object]]:
    """严格读取已提交的匿名样本，避免测试运行时悄悄改变期望。"""
    payload = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    cases = payload["cases"]
    assert isinstance(cases, list)
    assert len(cases) == 8
    assert len({case["case_id"] for case in cases}) == len(cases)
    return cases


@pytest.mark.parametrize("case", _load_cases(), ids=lambda case: case["case_id"])
def test_frozen_limited_context_policy_matrix(case: dict[str, object]) -> None:
    """每个样本同时固定引用消解、路由和检索前策略三个结果。"""
    resolved, context_used = resolve_question_reference(
        str(case["question"]),
        str(case["previous_question"]),
    )
    route = route_question(resolved)
    decision = assess_pre_retrieval_policy(
        resolved,
        target_namespace=_INTENT_NAMESPACE[route.intent],
    )

    assert context_used is case["expected_context_used"]
    assert route.intent.value == case["expected_intent"]
    assert decision.reason_code == case["expected_policy"]


def test_frozen_matrix_covers_required_adversarial_categories() -> None:
    """样本扩充不能遗漏三域正例、跨域、硬负例或隐私诱导。"""
    cases = _load_cases()
    categories = {case["category"] for case in cases}
    positive_intents = {
        case["expected_intent"]
        for case in cases
        if case["category"] == "positive_reference"
    }

    assert categories == {
        "positive_reference",
        "cross_namespace_override",
        "hard_negative_reference",
        "standalone_turn",
        "privacy_exfiltration",
    }
    assert positive_intents == {
        "knowledge_question",
        "project_context",
        "resume_context",
    }
