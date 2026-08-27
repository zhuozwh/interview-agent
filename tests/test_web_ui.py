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
    javascript = asyncio.run(_get(application, "/assets/app.js"))

    assert css.status_code == 200
    assert javascript.status_code == 200
    assert "text/css" in css.headers["content-type"]
    assert "javascript" in javascript.headers["content-type"]
    assert "https://" not in css.text
    assert "http://" not in css.text
    assert "innerHTML" not in javascript.text
    assert "textContent" in javascript.text
    assert "localStorage" not in javascript.text
    assert "sessionStorage" not in javascript.text


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
