"""
Logging Configuration Module
Centralized logging setup for the TikTok Slideshow Generator
"""
import os
import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

# Log directory (relative to backend or absolute for production)
LOG_DIR = os.getenv('LOG_DIR', os.path.join(os.path.dirname(__file__), 'logs'))
LOG_LEVEL = os.getenv('LOG_LEVEL', 'DEBUG')
LOG_FORMAT = '%(asctime)s | %(levelname)-8s | %(name)-25s | %(request_id)-10s | %(message)s'
LOG_DATE_FORMAT = '%Y-%m-%d %H:%M:%S'


class RequestIdFilter(logging.Filter):
    """Injects request_id into log records if not present"""
    def filter(self, record):
        if not hasattr(record, 'request_id'):
            record.request_id = 'system'
        return True


class RequestAdapter(logging.LoggerAdapter):
    """Adapter that injects request_id into log messages"""
    def process(self, msg, kwargs):
        kwargs.setdefault('extra', {})['request_id'] = self.extra.get('request_id', 'system')
        return msg, kwargs


_initialized = False


def setup_logging(app_name: str = 'tiktok') -> logging.Logger:
    """
    Set up application logging with file rotation.

    Args:
        app_name: Base name for log files

    Returns:
        Root logger for the application
    """
    global _initialized

    if _initialized:
        return logging.getLogger(app_name)

    # Create logs directory
    Path(LOG_DIR).mkdir(parents=True, exist_ok=True)

    # Create root logger
    logger = logging.getLogger(app_name)
    logger.setLevel(getattr(logging, LOG_LEVEL.upper(), logging.DEBUG))

    # Clear existing handlers
    logger.handlers = []

    # Create formatter
    formatter = logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT)

    # File handler with 7-day rotation (midnight rotation)
    log_file = os.path.join(LOG_DIR, f'{app_name}.log')
    file_handler = TimedRotatingFileHandler(
        filename=log_file,
        when='midnight',
        interval=1,
        backupCount=7,  # Keep 7 days
        encoding='utf-8'
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    file_handler.addFilter(RequestIdFilter())

    # Console handler for development
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    console_handler.addFilter(RequestIdFilter())

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    # Prevent propagation to root logger
    logger.propagate = False

    _initialized = True
    logger.info(f"Logging initialized: level={LOG_LEVEL}, dir={LOG_DIR}")

    return logger


def get_logger(module_name: str) -> logging.Logger:
    """
    Get a child logger for a specific module.

    Args:
        module_name: Name of the module (e.g., 'scraper', 'gemini', 'drive')

    Returns:
        Logger instance for the module
    """
    return logging.getLogger(f'tiktok.{module_name}')


def get_request_logger(module_name: str, request_id: str) -> RequestAdapter:
    """
    Get a logger adapter with request_id for tracking.

    Args:
        module_name: Name of the module
        request_id: Session/request ID for tracking

    Returns:
        RequestAdapter with request_id injected
    """
    logger = get_logger(module_name)
    return RequestAdapter(logger, {'request_id': request_id})
