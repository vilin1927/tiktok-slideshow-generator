"""
API Key Manager - Handles rotation and usage tracking for Gemini API keys.
Tracks both RPM (requests per minute) and daily limits per key, per model type.

Model Types:
- 'text': Gemini Flash for all text analysis
- 'image': Gemini 3.1 Flash for image generation (1000 RPM, 1K RPD)
"""
import os
import time
from datetime import datetime, timedelta
from typing import Optional, List, Dict
import redis
import pytz
from dotenv import load_dotenv

load_dotenv()

from logging_config import get_logger
from config import ApiKeyConfig, RedisConfig

logger = get_logger('api_key_manager')

# Configuration - Per-model rate limits (from config)
RATE_LIMITS = {
    'text': {
        'rpm': ApiKeyConfig.TEXT_RPM,
        'daily': ApiKeyConfig.TEXT_DAILY,
    },
    'image': {
        'rpm': ApiKeyConfig.IMAGE_RPM,
        'daily': ApiKeyConfig.IMAGE_DAILY,
    }
}

# Backwards compatibility - default to image limits (most restrictive)
GEMINI_RPM_LIMIT = RATE_LIMITS['image']['rpm']
GEMINI_DAILY_LIMIT = RATE_LIMITS['image']['daily']

# Redis configuration (from config)
REDIS_HOST = RedisConfig.HOST
REDIS_PORT = RedisConfig.PORT
REDIS_DB = RedisConfig.QUEUE_DB

# Key prefixes in Redis
KEY_PREFIX = "gemini:key:"


class ApiKeyExhaustedError(Exception):
    """Raised when all API keys are exhausted."""
    pass


class ApiKeyManager:
    """
    Manages multiple Gemini API keys with automatic rotation.

    Features:
    - Round-robin selection with limit checking
    - RPM tracking (auto-expires after 60s)
    - Daily usage tracking (resets at midnight Pacific Time)
    - Redis-backed for persistence across restarts
    """

    def __init__(self, redis_client: Optional[redis.Redis] = None):
        """Initialize the manager."""
        # Load API keys
        keys_str = os.getenv('GEMINI_API_KEYS', '')
        if keys_str:
            self.keys = [k.strip() for k in keys_str.split(',') if k.strip()]
        else:
            # Fallback to single key
            single_key = os.getenv('GEMINI_API_KEY', '')
            self.keys = [single_key] if single_key else []

        if not self.keys:
            raise ValueError("No Gemini API keys configured. Set GEMINI_API_KEYS or GEMINI_API_KEY")

        # Validate keys: reject non-ASCII characters (e.g. Cyrillic lookalikes)
        valid_keys = []
        for key in self.keys:
            try:
                key.encode('ascii')
                valid_keys.append(key)
            except UnicodeEncodeError:
                logger.error(f"REJECTED key {key[:8]}... — contains non-ASCII characters (likely Cyrillic). Fix in .env file.")
        if valid_keys:
            self.keys = valid_keys
        else:
            raise ValueError("All Gemini API keys contain non-ASCII characters. Fix GEMINI_API_KEYS in .env")

        # Redis connection
        if redis_client:
            self.redis = redis_client
        else:
            self.redis = redis.Redis(
                host=REDIS_HOST,
                port=REDIS_PORT,
                db=REDIS_DB,
                decode_responses=True
            )

        # Track last used key index for round-robin
        self._last_key_index = -1

        logger.info(f"ApiKeyManager initialized with {len(self.keys)} keys")

    def _get_key_id(self, key: str) -> str:
        """Get short ID for a key (first 8 chars)."""
        return key[:8]

    def _get_midnight_pt_timestamp(self) -> int:
        """Get Unix timestamp for next midnight Pacific Time."""
        pt = pytz.timezone('America/Los_Angeles')
        now_pt = datetime.now(pt)
        midnight_pt = (now_pt + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return int(midnight_pt.timestamp())

    def _get_seconds_until_midnight_pt(self) -> int:
        """Get seconds until midnight Pacific Time."""
        return self._get_midnight_pt_timestamp() - int(time.time())

    def get_key_usage(self, key: str, model_type: str = 'image') -> Dict:
        """
        Get current usage stats for a key for a specific model type.

        Args:
            key: The API key
            model_type: 'text' or 'image' (default: 'image')

        Returns:
            {
                'key_id': str (first 8 chars),
                'model_type': str,
                'rpm_used': int,
                'rpm_limit': int,
                'daily_used': int,
                'daily_limit': int,
                'is_available': bool,
                'is_free_tier': bool
            }
        """
        key_id = self._get_key_id(key)
        limits = RATE_LIMITS.get(model_type, RATE_LIMITS['image'])

        # Check if key is marked as invalid (API_KEY_INVALID — wrong or revoked key)
        invalid_key = f"{KEY_PREFIX}{key_id}:invalid"
        is_invalid = self.redis.get(invalid_key) == "true"

        if is_invalid:
            return {
                'key_id': key_id,
                'model_type': model_type,
                'rpm_used': 0,
                'rpm_limit': 0,
                'daily_used': 0,
                'daily_limit': 0,
                'is_available': False,
                'is_free_tier': False,
                'is_invalid': True,
                'is_daily_exhausted': False
            }

        # Check if key is marked as free tier (permanently unusable)
        free_tier_key = f"{KEY_PREFIX}{key_id}:{model_type}:free_tier"
        is_free_tier = self.redis.get(free_tier_key) == "true"

        if is_free_tier:
            return {
                'key_id': key_id,
                'model_type': model_type,
                'rpm_used': 0,
                'rpm_limit': 0,
                'daily_used': 0,
                'daily_limit': 0,
                'is_available': False,
                'is_free_tier': True,
                'is_invalid': False,
                'is_daily_exhausted': False
            }

        # Check if key hit daily quota limit (marked until midnight PT)
        daily_exhausted_key = f"{KEY_PREFIX}{key_id}:{model_type}:daily_exhausted"
        is_daily_exhausted = self.redis.get(daily_exhausted_key) == "true"

        if is_daily_exhausted:
            # Read actual daily counter for accurate reporting
            daily_key = f"{KEY_PREFIX}{key_id}:{model_type}:daily"
            actual_daily_used = int(self.redis.get(daily_key) or 0)
            return {
                'key_id': key_id,
                'model_type': model_type,
                'rpm_used': 0,
                'rpm_limit': limits['rpm'],
                'daily_used': actual_daily_used,
                'daily_limit': limits['daily'],
                'is_available': False,
                'is_free_tier': False,
                'is_invalid': False,
                'is_daily_exhausted': True
            }

        rpm_key = f"{KEY_PREFIX}{key_id}:{model_type}:rpm"
        daily_key = f"{KEY_PREFIX}{key_id}:{model_type}:daily"

        rpm_used = int(self.redis.get(rpm_key) or 0)
        daily_used = int(self.redis.get(daily_key) or 0)

        rpm_limit = limits['rpm']
        daily_limit = limits['daily']

        return {
            'key_id': key_id,
            'model_type': model_type,
            'rpm_used': rpm_used,
            'rpm_limit': rpm_limit,
            'daily_used': daily_used,
            'daily_limit': daily_limit,
            'is_available': rpm_used < rpm_limit and daily_used < daily_limit,
            'is_free_tier': False,
            'is_invalid': False,
            'is_daily_exhausted': False
        }

    def get_all_keys_status(self, model_type: str = 'image') -> List[Dict]:
        """Get usage stats for all keys for a specific model type."""
        return [self.get_key_usage(key, model_type) for key in self.keys]

    def get_available_key(self, model_type: str = 'image') -> str:
        """
        Get the next available API key using round-robin with limit checking.

        Args:
            model_type: 'text' or 'image' (default: 'image')

        Returns:
            API key string

        Raises:
            ApiKeyExhaustedError: If all keys are exhausted
        """
        # Try each key starting from last used + 1
        for i in range(len(self.keys)):
            idx = (self._last_key_index + 1 + i) % len(self.keys)
            key = self.keys[idx]
            usage = self.get_key_usage(key, model_type)

            # Skip invalid keys (API_KEY_INVALID)
            if usage.get('is_invalid'):
                logger.debug(f"Skipping key #{idx + 1} ({usage['key_id']}) - INVALID KEY")
                continue

            # Skip free tier keys
            if usage.get('is_free_tier'):
                logger.debug(f"Skipping key #{idx + 1} ({usage['key_id']}) - FREE TIER")
                continue

            if usage['is_available']:
                self._last_key_index = idx
                logger.debug(f"Selected key #{idx + 1} ({usage['key_id']}) for {model_type} | "
                           f"RPM: {usage['rpm_used']}/{usage['rpm_limit']} | "
                           f"Daily: {usage['daily_used']}/{usage['daily_limit']}")
                return key

        # All keys exhausted - log status
        status = self.get_all_keys_status(model_type)
        logger.error(f"All {len(self.keys)} API keys exhausted for {model_type} model!")
        for i, s in enumerate(status):
            if s.get('is_invalid'):
                logger.error(f"  Key #{i + 1} ({s['key_id']}): INVALID (API_KEY_INVALID)")
            elif s.get('is_free_tier'):
                logger.error(f"  Key #{i + 1} ({s['key_id']}): FREE TIER (no billing)")
            elif s.get('is_daily_exhausted'):
                logger.error(f"  Key #{i + 1} ({s['key_id']}): DAILY LIMIT (resets at midnight PT)")
            else:
                logger.error(f"  Key #{i + 1} ({s['key_id']}): "
                            f"RPM {s['rpm_used']}/{s['rpm_limit']}, "
                            f"Daily {s['daily_used']}/{s['daily_limit']}")

        raise ApiKeyExhaustedError(
            f"All {len(self.keys)} Gemini API keys exhausted for {model_type}. "
            f"RPM resets in <60s, daily resets at midnight PT."
        )

    def record_usage(self, key: str, model_type: str = 'image'):
        """
        Record that a request was made with this key for a specific model.
        Increments both RPM and daily counters for that model type.

        Args:
            key: The API key that was used
            model_type: 'text' or 'image' (default: 'image')
        """
        key_id = self._get_key_id(key)

        # Increment RPM counter (expires in 60 seconds)
        rpm_key = f"{KEY_PREFIX}{key_id}:{model_type}:rpm"
        self.redis.incr(rpm_key)
        self.redis.expire(rpm_key, 60)

        # Increment daily counter (expires at midnight PT)
        daily_key = f"{KEY_PREFIX}{key_id}:{model_type}:daily"
        self.redis.incr(daily_key)

        # Set expiry to midnight PT if not already set
        ttl = self.redis.ttl(daily_key)
        if ttl == -1:  # No expiry set
            seconds_until_midnight = self._get_seconds_until_midnight_pt()
            self.redis.expire(daily_key, seconds_until_midnight)

    def record_invalid_key(self, key: str):
        """
        Mark a key as permanently invalid (API_KEY_INVALID from Google).
        Applies across all model types. Expires after 24h so keys can be
        re-validated if the user fixes them.

        Args:
            key: The API key that returned INVALID_ARGUMENT
        """
        key_id = self._get_key_id(key)
        invalid_key = f"{KEY_PREFIX}{key_id}:invalid"
        # Mark invalid for 24h (auto-clears so updated keys get retried)
        self.redis.set(invalid_key, "true", ex=86400)
        logger.warning(f"Key {key_id} marked INVALID for 24h (API_KEY_INVALID)")

    def record_failure(self, key: str, model_type: str = 'image', is_rate_limit: bool = False, is_invalid_key: bool = False):
        """
        Record that a request failed.
        If rate limit error, mark this key as temporarily exhausted for that model (60s RPM cooldown).
        If invalid key error, mark permanently invalid (24h).

        Args:
            key: The API key that failed
            model_type: 'text' or 'image' (default: 'image')
            is_rate_limit: Whether this was a 429 rate limit error
            is_invalid_key: Whether this was a 400 API_KEY_INVALID error
        """
        if is_invalid_key:
            self.record_invalid_key(key)
            return

        if is_rate_limit:
            key_id = self._get_key_id(key)
            limits = RATE_LIMITS.get(model_type, RATE_LIMITS['image'])
            # Set RPM to max to prevent using this key for 60s
            rpm_key = f"{KEY_PREFIX}{key_id}:{model_type}:rpm"
            self.redis.set(rpm_key, limits['rpm'])
            self.redis.expire(rpm_key, 60)
            logger.warning(f"Key {key_id} hit RPM limit for {model_type}, marked exhausted for 60s")

    def record_daily_exhaustion(self, key: str, model_type: str = 'image'):
        """
        Mark a key as daily-exhausted (hit daily quota limit).
        Key will be unavailable until midnight Pacific Time.

        Args:
            key: The API key that hit daily limit
            model_type: 'text' or 'image' (default: 'image')
        """
        key_id = self._get_key_id(key)
        daily_exhausted_key = f"{KEY_PREFIX}{key_id}:{model_type}:daily_exhausted"
        seconds_until_midnight = self._get_seconds_until_midnight_pt()
        self.redis.set(daily_exhausted_key, "true", ex=seconds_until_midnight)
        logger.warning(f"Key {key_id} hit DAILY limit for {model_type}, "
                      f"marked unavailable for {seconds_until_midnight // 3600}h {(seconds_until_midnight % 3600) // 60}m")

    def is_daily_exhausted(self, key: str, model_type: str = 'image') -> bool:
        """Check if a key is marked as daily-exhausted."""
        key_id = self._get_key_id(key)
        daily_exhausted_key = f"{KEY_PREFIX}{key_id}:{model_type}:daily_exhausted"
        return self.redis.get(daily_exhausted_key) == "true"

    def get_summary(self, model_type: str = None) -> Dict:
        """
        Get overall summary of API key status.

        Args:
            model_type: 'text', 'image', or None for all models

        Returns:
            {
                'total_keys': int,
                'seconds_until_daily_reset': int,
                'text': { model stats },
                'image': { model stats },
                'keys': { per-key stats }
            }
        """
        result = {
            'total_keys': len(self.keys),
            'seconds_until_daily_reset': self._get_seconds_until_midnight_pt(),
        }

        # Get stats for each model type
        for mtype in ['text', 'image']:
            if model_type and mtype != model_type:
                continue

            status = self.get_all_keys_status(mtype)
            available = [s for s in status if s['is_available']]
            limits = RATE_LIMITS[mtype]

            total_rpm_available = sum(
                limits['rpm'] - s['rpm_used']
                for s in status
            )
            total_daily_available = sum(
                limits['daily'] - s['daily_used']
                for s in status
            )

            result[mtype] = {
                'available_keys': len(available),
                'total_rpm_available': total_rpm_available,
                'total_daily_available': total_daily_available,
                'rpm_limit_per_key': limits['rpm'],
                'daily_limit_per_key': limits['daily'],
                'keys': status
            }

        return result

    def are_all_keys_daily_exhausted(self, model_type: str = 'image') -> bool:
        """
        Check if ALL keys are daily-exhausted for a given model type.
        Used by queue processor to decide whether to pause until midnight PT
        instead of retrying every 70 seconds.

        Returns:
            True if all keys are daily-exhausted, invalid, or free-tier
        """
        for key in self.keys:
            usage = self.get_key_usage(key, model_type)
            if usage.get('is_available'):
                return False
            # Key might be only RPM-exhausted (not daily) — still usable soon
            if not usage.get('is_daily_exhausted') and not usage.get('is_free_tier') and not usage.get('is_invalid'):
                return False
        return True

    def reset_key(self, key_id: str, model_type: str = None):
        """
        Manually reset a key's counters (admin function).

        Args:
            key_id: First 8 chars of the key to reset
            model_type: 'text', 'image', or None for all
        """
        model_types = [model_type] if model_type else ['text', 'image']

        for mtype in model_types:
            rpm_key = f"{KEY_PREFIX}{key_id}:{mtype}:rpm"
            daily_key = f"{KEY_PREFIX}{key_id}:{mtype}:daily"
            self.redis.delete(rpm_key, daily_key)

        # Also clear invalid flag
        invalid_key = f"{KEY_PREFIX}{key_id}:invalid"
        self.redis.delete(invalid_key)

        logger.info(f"Manually reset counters for key {key_id} ({model_type or 'all models'})")

    def reset_all_keys(self, model_type: str = None):
        """Manually reset all key counters (admin function)."""
        for key in self.keys:
            self.reset_key(self._get_key_id(key), model_type)
        logger.info(f"Manually reset all API key counters ({model_type or 'all models'})")


# Global singleton instance
_manager: Optional[ApiKeyManager] = None


def get_api_key_manager() -> ApiKeyManager:
    """Get or create the global ApiKeyManager instance."""
    global _manager
    if _manager is None:
        _manager = ApiKeyManager()
    return _manager
