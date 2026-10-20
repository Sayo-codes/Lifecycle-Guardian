"""Structured logging setup for Lifecycle Guardian."""

import logging
import sys
from src.config import settings

try:
    import structlog
    _has_structlog = True
except ImportError:
    _has_structlog = False


def setup_logger():
    """Configure structured logging using structlog or standard logging fallback."""
    log_level = getattr(logging, settings.log_level.upper(), logging.INFO)

    logging.basicConfig(
        format="[%(asctime)s] [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stdout,
        level=log_level,
    )

    if _has_structlog:
        structlog.configure(
            processors=[
                structlog.contextvars.merge_contextvars,
                structlog.processors.add_log_level,
                structlog.processors.StackInfoRenderer(),
                structlog.dev.set_exc_info,
                structlog.processors.TimeStamper(fmt="%Y-%m-%d %H:%M:%S", utc=False),
                structlog.dev.ConsoleRenderer(colors=True)
                if settings.app_env == "development"
                else structlog.processors.JSONRenderer(),
            ],
            wrapper_class=structlog.make_filtering_bound_logger(log_level),
            context_class=dict,
            logger_factory=structlog.PrintLoggerFactory(),
            cache_logger_on_first_use=True,
        )
        return structlog.get_logger("lifecycle_guardian")

    # Simple fallback wrapper providing .info(event, **kw) interface
    class FallbackLogger:
        def __init__(self, name: str):
            self.logger = logging.getLogger(name)

        def info(self, event: str, **kwargs):
            kw_str = " ".join(f"{k}={v}" for k, v in kwargs.items())
            self.logger.info(f"{event} {kw_str}".strip())

        def warning(self, event: str, **kwargs):
            kw_str = " ".join(f"{k}={v}" for k, v in kwargs.items())
            self.logger.warning(f"{event} {kw_str}".strip())

        def error(self, event: str, **kwargs):
            kw_str = " ".join(f"{k}={v}" for k, v in kwargs.items())
            self.logger.error(f"{event} {kw_str}".strip())

        def debug(self, event: str, **kwargs):
            kw_str = " ".join(f"{k}={v}" for k, v in kwargs.items())
            self.logger.debug(f"{event} {kw_str}".strip())

    return FallbackLogger("lifecycle_guardian")


logger = setup_logger()
