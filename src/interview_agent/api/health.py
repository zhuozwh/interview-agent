"""Process-level health endpoint."""

from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
def get_health() -> dict[str, str]:
    """Report that the application process is running."""
    return {"status": "ok"}
