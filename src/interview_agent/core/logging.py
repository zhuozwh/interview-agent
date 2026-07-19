"""Standard logging configuration for the application."""

import logging

LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
_HANDLER_MARKER = "_interview_agent_handler"


def configure_logging(level: str | int) -> None:
    """Configure the root logger without adding duplicate application handlers."""
    numeric_level = _to_numeric_level(level)
    root_logger = logging.getLogger()
    root_logger.setLevel(numeric_level)

    handler = next(
        (
            existing_handler
            for existing_handler in root_logger.handlers
            if getattr(existing_handler, _HANDLER_MARKER, False)
        ),
        None,
    )

    if handler is None:
        handler = logging.StreamHandler()
        setattr(handler, _HANDLER_MARKER, True)
        root_logger.addHandler(handler)

    handler.setLevel(numeric_level)
    handler.setFormatter(logging.Formatter(LOG_FORMAT))


def _to_numeric_level(level: str | int) -> int:
    if isinstance(level, int):
        return level

    numeric_level = logging.getLevelName(level.upper())
    if not isinstance(numeric_level, int):
        raise ValueError(f"Unsupported log level: {level}")
    return numeric_level
