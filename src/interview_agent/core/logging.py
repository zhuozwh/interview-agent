"""使用 Python 标准库统一配置应用日志。"""

import logging

# 每条日志依次显示时间、级别、logger 名称和消息。
LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"

# 给本项目创建的 handler 打标记，以免重复初始化时不断添加 handler。
_HANDLER_MARKER = "_interview_agent_handler"


def configure_logging(level: str | int) -> None:
    """配置根 logger，并保证本项目的输出 handler 只有一个。"""
    numeric_level = _to_numeric_level(level)

    # 根 logger 是所有未单独配置的 logger 最终汇总输出的位置。
    root_logger = logging.getLogger()
    root_logger.setLevel(numeric_level)

    # 从现有 handler 中寻找带有本项目标记的那一个；找不到时返回 None。
    handler = next(
        (
            existing_handler
            for existing_handler in root_logger.handlers
            if getattr(existing_handler, _HANDLER_MARKER, False)
        ),
        None,
    )

    if handler is None:
        # StreamHandler 默认把日志写到终端；只有第一次配置时才创建。
        handler = logging.StreamHandler()
        setattr(handler, _HANDLER_MARKER, True)
        root_logger.addHandler(handler)

    # 即使 handler 已存在，也允许新的配置更新日志级别和格式。
    handler.setLevel(numeric_level)
    handler.setFormatter(logging.Formatter(LOG_FORMAT))


def _to_numeric_level(level: str | int) -> int:
    """把 INFO 等字符串转换为 logging 内部使用的整数级别。"""
    if isinstance(level, int):
        return level

    numeric_level = logging.getLevelName(level.upper())
    if not isinstance(numeric_level, int):
        raise ValueError(f"Unsupported log level: {level}")
    return numeric_level
