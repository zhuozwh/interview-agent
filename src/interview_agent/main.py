"""FastAPI application construction and local entry point."""

import uvicorn
from fastapi import FastAPI

from interview_agent.api.health import router as health_router
from interview_agent.core.config import Settings, get_settings
from interview_agent.core.logging import configure_logging


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create and configure the FastAPI application."""
    current_settings = settings or get_settings()
    configure_logging(current_settings.log_level)

    application = FastAPI(title=current_settings.app_name)
    application.state.settings = current_settings
    application.include_router(health_router)
    return application


app = create_app()


def main() -> None:
    """Run the local development server."""
    uvicorn.run(app, host="127.0.0.1", port=8000, log_config=None)


if __name__ == "__main__":
    main()
