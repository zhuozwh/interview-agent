"""提供不依赖前端构建工具的本地聊天入口。"""

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

router = APIRouter()
_WEB_DIRECTORY = Path(__file__).resolve().parent.parent / "web"


@router.get("/", include_in_schema=False)
def get_chat_page() -> FileResponse:
    """返回本地聊天页，并限制页面只能加载同源资源。"""
    return FileResponse(
        _WEB_DIRECTORY / "index.html",
        media_type="text/html",
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": (
                "default-src 'self'; "
                "script-src 'self'; "
                "style-src 'self'; "
                "connect-src 'self'; "
                "img-src 'self' data:; "
                "object-src 'none'; "
                "base-uri 'none'; "
                "frame-ancestors 'none'"
            ),
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
        },
    )


__all__ = ["_WEB_DIRECTORY", "router"]
