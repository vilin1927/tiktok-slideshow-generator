"""
Configuration module for Viral Slideshow Generator.
Centralizes all configurable values with environment variable overrides.
"""
import os
from dotenv import load_dotenv

load_dotenv()


def _get_int(key: str, default: int) -> int:
    """Get integer from environment with fallback."""
    try:
        return int(os.getenv(key, str(default)))
    except ValueError:
        return default


def _get_float(key: str, default: float) -> float:
    """Get float from environment with fallback."""
    try:
        return float(os.getenv(key, str(default)))
    except ValueError:
        return default


def _get_bool(key: str, default: bool) -> bool:
    """Get boolean from environment with fallback."""
    val = os.getenv(key, str(default)).lower()
    return val in ('true', '1', 'yes', 'on')


class QueueConfig:
    """Configuration for the image queue system."""

    # Batch processing
    # 3 keys × 900 RPM = 2700 requests/minute capacity (gemini-3.1-flash)
    BATCH_SIZE = _get_int('QUEUE_BATCH_SIZE', 50)
    BATCH_INTERVAL = _get_int('QUEUE_BATCH_INTERVAL', 60)  # seconds
    BATCH_TIMEOUT = _get_int('QUEUE_BATCH_TIMEOUT', 120)  # seconds per image
    STAGGER_DELAY = _get_float('QUEUE_STAGGER_DELAY', 0.5)  # seconds between submissions
    MAX_WORKERS = _get_int('QUEUE_MAX_WORKERS', 50)  # concurrent generation threads

    # Retry settings
    MAX_RETRIES = _get_int('QUEUE_MAX_RETRIES', 3)
    RETRY_DELAY_BASE = _get_int('QUEUE_RETRY_DELAY_BASE', 30)  # seconds

    # Rate limit handling
    PAUSE_ON_RATE_LIMIT = _get_bool('QUEUE_PAUSE_ON_RATE_LIMIT', True)
    RATE_LIMIT_PAUSE_DEFAULT = _get_int('QUEUE_RATE_LIMIT_PAUSE', 65)  # seconds

    # Cleanup settings
    CLEANUP_INTERVAL = _get_int('QUEUE_CLEANUP_INTERVAL', 10)  # batches
    STALE_PENDING_HOURS = _get_float('QUEUE_STALE_PENDING_HOURS', 2.0)
    STALE_RETRY_HOURS = _get_float('QUEUE_STALE_RETRY_HOURS', 1.0)
    STALE_PROCESSING_HOURS = _get_float('QUEUE_STALE_PROCESSING_HOURS', 0.5)
    STALE_TASK_TIMEOUT = _get_int('QUEUE_STALE_TASK_TIMEOUT', 1800)  # 30 min in seconds

    # Circuit breaker
    CIRCUIT_BREAKER_THRESHOLD = _get_int('CIRCUIT_BREAKER_THRESHOLD', 3)
    CIRCUIT_BREAKER_RESET_TIME = _get_int('CIRCUIT_BREAKER_RESET_TIME', 300)  # 5 minutes

    # Redis TTL
    TASK_DATA_TTL = _get_int('QUEUE_TASK_TTL', 86400)  # 24 hours
    JOB_DATA_TTL = _get_int('QUEUE_JOB_TTL', 86400)  # 24 hours
    RESULT_TTL = _get_int('QUEUE_RESULT_TTL', 86400)  # 24 hours


class RedisConfig:
    """Configuration for Redis connections."""

    HOST = os.getenv('REDIS_HOST', 'localhost')
    PORT = _get_int('REDIS_PORT', 6379)
    QUEUE_DB = _get_int('REDIS_QUEUE_DB', 1)
    CACHE_DB = _get_int('REDIS_CACHE_DB', 2)


class ApiKeyConfig:
    """Configuration for API key management."""

    # Per-model rate limits
    TEXT_RPM = _get_int('GEMINI_TEXT_RPM', 900)  # actual: 1000
    TEXT_DAILY = _get_int('GEMINI_TEXT_DAILY', 9000)  # actual: 10000
    IMAGE_RPM = _get_int('GEMINI_IMAGE_RPM', 900)  # actual: 1000 RPM per key (gemini-3.1-flash)
    IMAGE_DAILY = _get_int('GEMINI_IMAGE_DAILY', 900)  # actual: 1000 RPD per key


class GeminiConfig:
    """Configuration for Gemini API."""

    # Models
    TEXT_MODEL = os.getenv('GEMINI_TEXT_MODEL', 'gemini-2.0-flash')
    IMAGE_MODEL = os.getenv('GEMINI_IMAGE_MODEL', 'gemini-3.1-flash-image-preview')
    GROUNDING_MODEL = os.getenv('GEMINI_GROUNDING_MODEL', 'gemini-2.0-flash')

    # Timeouts
    REQUEST_TIMEOUT = _get_int('GEMINI_REQUEST_TIMEOUT', 120)  # seconds
    GROUNDING_TIMEOUT = _get_int('GEMINI_GROUNDING_TIMEOUT', 30)  # seconds

    # Queue mode
    USE_QUEUE_MODE = _get_bool('USE_QUEUE_MODE', True)
