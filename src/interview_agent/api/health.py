"""只表示应用进程能够响应请求的健康检查接口。"""

from fastapi import APIRouter

# APIRouter 可以理解为一张局部路由表，最后由 main.py 装入整个应用。
router = APIRouter()


# 装饰器把 GET /health 登记为下面这个函数负责处理。
@router.get("/health")
def get_health() -> dict[str, str]:
    """返回固定状态；当前不检查数据库或任何外部服务。"""
    # FastAPI 会把 Python 字典自动转换为 JSON，成功时默认状态码为 200。
    return {"status": "ok"}
