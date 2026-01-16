"""
Redis-based Rate Limiter for Gemini API

Provides a shared rate limiter across all processes (Flask + Celery workers)
using Redis as the coordination backend.

Rate limit: 20 requests per 65 seconds (Gemini API quota with safety margin)
"""
import time
import redis
import os
from typing import Optional

from logging_config import get_logger

logger = get_logger('rate_limiter')

# Rate limiting config
RPM_LIMIT = 20          # 20 requests per minute (Gemini strict quota)
RATE_WINDOW = 65.0      # 65 second window (safety margin)
MAX_WAIT_TIME = 120     # Max seconds to wait for rate limit

# Redis config
REDIS_HOST = os.getenv('REDIS_HOST', 'localhost')
REDIS_PORT = int(os.getenv('REDIS_PORT', 6379))
REDIS_DB = int(os.getenv('REDIS_DB', 0))

# Redis keys
RATE_LIMIT_KEY = 'gemini:rate_limit:requests'
RATE_LIMIT_LOCK = 'gemini:rate_limit:lock'


class RedisRateLimiter:
    """
    Distributed rate limiter using Redis.

    Uses a sliding window approach:
    - Stores timestamps of recent requests in a sorted set
    - Before each request, checks if we're under the limit
    - If over limit, waits until oldest request expires
    """

    def __init__(
        self,
        rpm: int = RPM_LIMIT,
        window: float = RATE_WINDOW,
        redis_client: Optional[redis.Redis] = None
    ):
        self.rpm = rpm
        self.window = window
        self.min_interval = window / rpm  # 65/20 = 3.25s between requests

        if redis_client:
            self.redis = redis_client
        else:
            self.redis = redis.Redis(
                host=REDIS_HOST,
                port=REDIS_PORT,
                db=REDIS_DB,
                decode_responses=True
            )

        logger.info(f"RedisRateLimiter initialized: rpm={rpm}, window={window}s, interval={self.min_interval:.2f}s")

    def acquire(self, timeout: float = MAX_WAIT_TIME) -> bool:
        """
        Acquire permission to make a request.
        Blocks until rate limit allows, or timeout.

        Returns:
            True if acquired, False if timeout
        """
        start_time = time.time()

        while True:
            now = time.time()
            window_start = now - self.window

            # Use Redis transaction for atomic check-and-add
            pipe = self.redis.pipeline()

            try:
                # Remove old requests outside the window
                pipe.zremrangebyscore(RATE_LIMIT_KEY, '-inf', window_start)

                # Count requests in current window
                pipe.zcard(RATE_LIMIT_KEY)

                # Execute
                results = pipe.execute()
                current_count = results[1]

                if current_count < self.rpm:
                    # Under limit - add this request and proceed
                    request_id = f"{now}:{os.getpid()}"
                    self.redis.zadd(RATE_LIMIT_KEY, {request_id: now})

                    # Set expiry on the key (cleanup)
                    self.redis.expire(RATE_LIMIT_KEY, int(self.window * 2))

                    logger.debug(f"Rate limiter: acquired ({current_count + 1}/{self.rpm} in window)")
                    return True

                # Over limit - calculate wait time
                # Get the oldest request timestamp
                oldest = self.redis.zrange(RATE_LIMIT_KEY, 0, 0, withscores=True)
                if oldest:
                    oldest_time = oldest[0][1]
                    wait_time = (oldest_time + self.window) - now + 0.1  # +0.1s buffer
                else:
                    wait_time = self.min_interval

                # Check timeout
                elapsed = time.time() - start_time
                if elapsed + wait_time > timeout:
                    logger.warning(f"Rate limiter: timeout after {elapsed:.1f}s")
                    return False

                logger.debug(f"Rate limiter: waiting {wait_time:.1f}s ({current_count}/{self.rpm} in window)")
                time.sleep(min(wait_time, 5.0))  # Sleep max 5s then recheck

            except redis.RedisError as e:
                logger.error(f"Redis error in rate limiter: {e}")
                # On Redis error, fall back to simple delay
                time.sleep(self.min_interval)
                return True

    def release(self):
        """
        Release is a no-op for sliding window rate limiter.
        Requests automatically expire from the window.
        """
        pass

    def get_status(self) -> dict:
        """Get current rate limiter status."""
        now = time.time()
        window_start = now - self.window

        # Clean and count
        self.redis.zremrangebyscore(RATE_LIMIT_KEY, '-inf', window_start)
        current_count = self.redis.zcard(RATE_LIMIT_KEY)

        return {
            'current_requests': current_count,
            'limit': self.rpm,
            'window_seconds': self.window,
            'available': self.rpm - current_count
        }


# Global singleton
_redis_rate_limiter: Optional[RedisRateLimiter] = None


def get_rate_limiter() -> RedisRateLimiter:
    """Get or create the global Redis rate limiter."""
    global _redis_rate_limiter

    if _redis_rate_limiter is None:
        _redis_rate_limiter = RedisRateLimiter(rpm=RPM_LIMIT, window=RATE_WINDOW)

    return _redis_rate_limiter


def reset_rate_limiter():
    """Reset the rate limiter (for testing)."""
    global _redis_rate_limiter

    if _redis_rate_limiter:
        _redis_rate_limiter.redis.delete(RATE_LIMIT_KEY)
    _redis_rate_limiter = None
