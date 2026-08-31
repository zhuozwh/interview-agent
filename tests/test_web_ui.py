"""验证本地聊天页可直接加载且保持同源安全边界。"""

import asyncio

import httpx

from interview_agent.core.config import Settings
from interview_agent.main import create_app


async def _get(application, path: str) -> httpx.Response:
    """通过内存 ASGI transport 读取页面，不启动浏览器或端口。"""
    transport = httpx.ASGITransport(app=application)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        return await client.get(path)


def test_chat_page_is_primary_local_entry_with_security_headers() -> None:
    """根路径展示聊天入口，并限制脚本、样式和连接为同源。"""
    application = create_app(Settings(_env_file=None))
    response = asyncio.run(_get(application, "/"))

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Interview Agent" in response.text
    assert "把你的资料" in response.text
    assert "FastAPI" not in response.text
    policy = response.headers["content-security-policy"]
    assert "default-src 'self'" in policy
    assert "connect-src 'self'" in policy
    assert "object-src 'none'" in policy
    assert response.headers["x-content-type-options"] == "nosniff"


def test_chat_assets_load_without_external_dependencies_or_unsafe_html() -> None:
    """静态资源随 Python 包提供，界面渲染正文不使用 innerHTML。"""
    application = create_app(Settings(_env_file=None))
    css = asyncio.run(_get(application, "/assets/app.css"))
    formatter = asyncio.run(_get(application, "/assets/safe_format.js"))
    javascript = asyncio.run(_get(application, "/assets/app.js"))

    assert css.status_code == 200
    assert formatter.status_code == 200
    assert javascript.status_code == 200
    assert "text/css" in css.headers["content-type"]
    assert "javascript" in formatter.headers["content-type"]
    assert "javascript" in javascript.headers["content-type"]
    assert "https://" not in css.text
    assert "http://" not in css.text
    combined_scripts = formatter.text + javascript.text
    assert "innerHTML" not in combined_scripts
    assert "outerHTML" not in combined_scripts
    assert "insertAdjacentHTML" not in combined_scripts
    assert "textContent" in javascript.text
    assert "javascript:" not in formatter.text
    assert "localStorage" not in combined_scripts
    assert "sessionStorage" not in combined_scripts


def test_citations_open_current_local_evidence_without_client_paths() -> None:
    """可展开引用只提交保存身份，并始终把 Markdown 正文当纯文本。"""
    application = create_app(Settings(_env_file=None))
    page = asyncio.run(_get(application, "/"))
    javascript = asyncio.run(_get(application, "/assets/app.js"))

    assert 'id="evidence-panel"' in page.text
    assert "当前只读 Markdown 文件，不是回答时的快照" in page.text
    assert "检索分数表示相关性排序，不代表答案正确率" in page.text
    assert javascript.text.count("fetch(`/api/evidence/${endpoint}`") == 1
    assert "[state.sessionId, turn.trace_id, citation.citation_id]" in javascript.text
    assert ".map((value) => encodeURIComponent(value))" in javascript.text
    assert "evidence.source_namespace" not in javascript.text.split(
        "fetch(`/api/evidence/${endpoint}`"
    )[0][-300:]
    assert "evidence.relative_path" not in javascript.text.split(
        "fetch(`/api/evidence/${endpoint}`"
    )[0][-300:]
    assert "elements.evidenceContent.textContent" in javascript.text
    assert 'turn.evidence_available === true' in javascript.text
    assert 'body.history_status === "saved"' in javascript.text
    assert 'cache: "no-store"' in javascript.text
    assert "requestSequence !== state.evidenceRequestSequence" in javascript.text
    assert "引用对应的本地文件或行号已经变化" in javascript.text
    assert "body.detail || \"无法读取这条本地证据。\"" not in javascript.text
    assert "setTimeout" not in javascript.text


def test_chat_ui_exposes_evidence_strength_and_actionable_single_errors() -> None:
    """中等证据不能再隐身，模型错误只在消息内给出中文动作。"""
    application = create_app(Settings(_env_file=None))
    javascript = asyncio.run(_get(application, "/assets/app.js"))

    assert "证据匹配：中，请核对引用" in javascript.text
    assert "证据匹配：低，仅作参考" in javascript.text
    assert "表示本轮检索证据强度，不是模型正确率" in javascript.text
    assert "回答没有完整生成" in javascript.text
    assert "手动重试会重新检索" in javascript.text
    assert "可能产生一次远端模型调用和费用" in javascript.text
    assert "setNotice(body.error.message)" not in javascript.text
    assert "error_retryable" in javascript.text


def test_chat_manual_retry_is_explicit_single_request_in_same_session() -> None:
    """重试必须经确认，只走一个提交入口，且不把失败问题变成追问上下文。"""
    application = create_app(Settings(_env_file=None))
    javascript = asyncio.run(_get(application, "/assets/app.js"))

    assert javascript.text.count('fetch("/ask"') == 1
    assert "window.confirm" in javascript.text
    assert "state.manualRetryQuestion !== question" in javascript.text
    assert "payload.session_id = state.sessionId" in javascript.text
    assert "payload.previous_question = previousQuestion" in javascript.text
    assert "setTimeout" not in javascript.text
    assert "invalid_llm_response" in javascript.text
    assert '"pending"' in javascript.text
    assert "NON_FAILURE_STATUSES" in javascript.text


def test_health_remains_lightweight_after_web_ui_is_added(tmp_path) -> None:
    """只打开页面和健康检查仍不会创建会话数据库。"""
    database_path = tmp_path / "runtime" / "agent.sqlite3"
    application = create_app(
        Settings(database_path=database_path, _env_file=None)
    )
    root = asyncio.run(_get(application, "/"))
    health = asyncio.run(_get(application, "/health"))

    assert root.status_code == 200
    assert health.json() == {"status": "ok"}
    assert not database_path.exists()
