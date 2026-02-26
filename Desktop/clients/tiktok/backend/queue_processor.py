"""
Queue Processor - Batch Image Generation Worker
Processes images in batches from the global queue.
"""
import os
import sys
import time
import signal
import threading
import re
import json
import gc
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError
from typing import List, Optional
from dotenv import load_dotenv

load_dotenv()

# Import configuration
from config import QueueConfig, RedisConfig


def get_memory_usage_mb() -> float:
    """Get current process memory usage in MB (RSS - Resident Set Size)."""
    try:
        # Read from /proc/self/status (Linux)
        with open('/proc/self/status', 'r') as f:
            for line in f:
                if line.startswith('VmRSS:'):
                    # Format: "VmRSS:    123456 kB"
                    parts = line.split()
                    return int(parts[1]) / 1024  # Convert kB to MB
    except (IOError, OSError, ValueError, IndexError):
        pass

    # Fallback: use resource module
    try:
        import resource
        # Returns bytes on Linux, need to convert
        usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # On Linux ru_maxrss is in KB, on macOS it's in bytes
        if sys.platform == 'darwin':
            return usage / (1024 * 1024)
        return usage / 1024
    except (ImportError, AttributeError, OSError):
        return 0.0

from logging_config import setup_logging, get_logger

# Initialize logging (must be done before get_logger)
setup_logging()
logger = get_logger('queue_processor')

from image_queue import GlobalImageQueue, ImageTask, get_global_queue

from google import genai
from google.genai import types

# Import image generation function from gemini_service_v2
from gemini_service_v2 import (
    _generate_single_image, _get_client, _record_api_usage, _validate_image_structure,
    IMAGE_MODEL, REQUEST_TIMEOUT
)

# Import ApiKeyExhaustedError to handle key exhaustion as rate limit
from api_key_manager import ApiKeyExhaustedError

# Import metrics (optional - gracefully degrades if not installed)
try:
    from metrics import (
        record_image_generated, record_image_failed, record_batch_processed,
        record_api_request, set_circuit_breaker_state, set_processor_state,
        update_queue_metrics, init_metrics
    )
    METRICS_AVAILABLE = True
except ImportError:
    METRICS_AVAILABLE = False
    def record_image_generated(*args, **kwargs): pass
    def record_image_failed(*args, **kwargs): pass
    def record_batch_processed(*args, **kwargs): pass
    def record_api_request(*args, **kwargs): pass
    def set_circuit_breaker_state(*args, **kwargs): pass
    def set_processor_state(*args, **kwargs): pass
    def update_queue_metrics(*args, **kwargs): pass
    def init_metrics(): pass

# Use configuration values
BATCH_SIZE = QueueConfig.BATCH_SIZE
BATCH_INTERVAL = QueueConfig.BATCH_INTERVAL
BATCH_TIMEOUT = QueueConfig.BATCH_TIMEOUT
MAX_WORKERS = QueueConfig.MAX_WORKERS
PAUSE_ON_RATE_LIMIT = QueueConfig.PAUSE_ON_RATE_LIMIT
RATE_LIMIT_PAUSE_DEFAULT = QueueConfig.RATE_LIMIT_PAUSE_DEFAULT
CLEANUP_INTERVAL = QueueConfig.CLEANUP_INTERVAL
STALE_TASK_TIMEOUT = QueueConfig.STALE_TASK_TIMEOUT


class BatchProcessor:
    """
    Processes image generation tasks in batches.

    Configuration is loaded from QueueConfig (config.py).
    Default: pull up to 18 tasks every 60 seconds (4 keys × 18 RPM = 72/min capacity).
    """

    # Circuit breaker settings (from config)
    CIRCUIT_BREAKER_THRESHOLD = QueueConfig.CIRCUIT_BREAKER_THRESHOLD
    CIRCUIT_BREAKER_RESET_TIME = QueueConfig.CIRCUIT_BREAKER_RESET_TIME

    # Cleanup settings (from config)
    STALE_PENDING_HOURS = QueueConfig.STALE_PENDING_HOURS
    STALE_RETRY_HOURS = QueueConfig.STALE_RETRY_HOURS
    STALE_PROCESSING_HOURS = QueueConfig.STALE_PROCESSING_HOURS

    def __init__(self, queue: Optional[GlobalImageQueue] = None):
        self.queue = queue or get_global_queue()
        # Note: We no longer store a single client - each image generation
        # gets a fresh client with key rotation from ApiKeyManager
        self.running = False
        self._stop_event = threading.Event()
        self._paused = False
        self._pause_until = 0

        # Stats
        self.batches_processed = 0
        self.images_generated = 0
        self.images_failed = 0
        self._last_cleanup = 0  # Batch count at last cleanup

        # Startup cleanup flag
        self._startup_cleanup_done = False

        # Circuit breaker state
        self._consecutive_all_key_failures = 0
        self._circuit_open_until = 0

        logger.info(f"BatchProcessor initialized: batch_size={BATCH_SIZE}, interval={BATCH_INTERVAL}s")

    def start(self):
        """Start the processor in the current thread."""
        logger.info("BatchProcessor starting...")
        self.running = True
        self._stop_event.clear()

        # Initialize metrics
        init_metrics()
        set_processor_state(True)

        # Set up signal handlers for graceful shutdown
        signal.signal(signal.SIGTERM, self._handle_shutdown)
        signal.signal(signal.SIGINT, self._handle_shutdown)

        try:
            self._run_loop()
        except Exception as e:
            logger.error(f"BatchProcessor crashed: {e}", exc_info=True)
            raise
        finally:
            self.running = False
            set_processor_state(False)
            logger.info("BatchProcessor stopped")

    def stop(self):
        """Signal the processor to stop."""
        logger.info("BatchProcessor stopping...")
        self._stop_event.set()

    def _handle_shutdown(self, signum, frame):
        """Handle shutdown signals gracefully."""
        logger.info(f"Received signal {signum}, initiating shutdown...")
        self.stop()

    def _run_loop(self):
        """Main processing loop - strict 60-second batches."""
        # Run startup cleanup once
        if not self._startup_cleanup_done:
            self._run_startup_cleanup()

        while not self._stop_event.is_set():
            batch_start = time.time()

            # Update heartbeat for health checks
            self.queue.redis.set('queue_processor:heartbeat', str(time.time()))

            # Check circuit breaker
            if self._is_circuit_open():
                remaining = self._circuit_open_until - time.time()
                logger.warning(f"Circuit breaker OPEN - skipping batch ({remaining:.0f}s remaining)")
                self._stop_event.wait(BATCH_INTERVAL)
                continue

            # Check if paused (rate limit recovery)
            if self._paused and time.time() < self._pause_until:
                wait_time = self._pause_until - time.time()
                logger.info(f"Queue paused for rate limit recovery, waiting {wait_time:.1f}s...")
                self._stop_event.wait(min(wait_time, BATCH_INTERVAL))
                continue
            self._paused = False

            # Get batch of tasks
            tasks = self.queue.get_batch(BATCH_SIZE)

            if tasks:
                # Log memory before processing
                mem_before = get_memory_usage_mb()
                queue_stats = self.queue.get_queue_stats()
                logger.info(f"Processing batch #{self.batches_processed + 1}: {len(tasks)} tasks | "
                           f"Memory: {mem_before:.0f}MB | "
                           f"Queue: {queue_stats['pending']} pending, {queue_stats['processing']} processing")

                self._process_batch(tasks)
                self.batches_processed += 1

                # Log memory after processing
                mem_after = get_memory_usage_mb()
                mem_delta = mem_after - mem_before
                logger.info(f"Batch #{self.batches_processed} complete | "
                           f"Memory: {mem_after:.0f}MB ({mem_delta:+.0f}MB) | "
                           f"Generated: {self.images_generated}, Failed: {self.images_failed}")

                # Force garbage collection if memory grew significantly
                if mem_delta > 100:  # More than 100MB growth
                    gc.collect()
                    mem_after_gc = get_memory_usage_mb()
                    logger.info(f"GC triggered: {mem_after:.0f}MB -> {mem_after_gc:.0f}MB")

                # Run periodic cleanup
                self._run_periodic_cleanup()
            else:
                # Log memory stats even when idle (every 5 minutes worth of batches)
                if self.batches_processed % 5 == 0:
                    mem = get_memory_usage_mb()
                    queue_stats = self.queue.get_queue_stats()
                    logger.debug(f"Idle | Memory: {mem:.0f}MB | Queue: {queue_stats['pending']} pending")

            # Calculate time to wait until next batch
            elapsed = time.time() - batch_start
            wait_time = max(0, BATCH_INTERVAL - elapsed)

            if wait_time > 0:
                logger.debug(f"Batch completed in {elapsed:.1f}s, waiting {wait_time:.1f}s for next batch")
                # Use event wait so we can be interrupted for shutdown
                self._stop_event.wait(wait_time)

    def _process_batch(self, tasks: List[ImageTask]):
        """
        Process all tasks in parallel.

        Args:
            tasks: List of ImageTask objects to process
        """
        batch_start = time.time()

        # Track results
        succeeded = 0
        failed = 0

        # Submit tasks to thread pool with staggered delays to avoid rate limit bursts
        # 4 keys × 18 RPM each = 72 requests/minute capacity
        stagger_delay = QueueConfig.STAGGER_DELAY

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            # Submit tasks with delay between each to stagger API calls
            futures = {}
            task_start_times = {}
            for i, task in enumerate(tasks):
                task_start_times[task.task_id] = time.time()
                futures[executor.submit(self._generate_image, task)] = task
                if i < len(tasks) - 1:  # Don't sleep after last task
                    time.sleep(stagger_delay)

            # Process results as they complete (no timeout - strict 60s timer handles pacing)
            for future in as_completed(futures):
                task = futures[future]
                task_duration = time.time() - task_start_times.get(task.task_id, time.time())

                try:
                    result_path = future.result(timeout=BATCH_TIMEOUT)
                    self.queue.mark_complete(task.task_id, result_path)
                    succeeded += 1
                    self.images_generated += 1
                    self._record_generation_success()  # Reset circuit breaker
                    # Record metrics
                    record_image_generated(task.slide_type or 'unknown', task_duration)
                    logger.info(f"Task {task.task_id} completed: {os.path.basename(result_path)}")

                except TimeoutError:
                    self.queue.mark_failed(task.task_id, "Generation timeout")
                    failed += 1
                    self.images_failed += 1
                    record_image_failed('timeout')
                    logger.warning(f"Task {task.task_id} timed out")

                except (RateLimitError, ApiKeyExhaustedError) as e:
                    # Rate limit or keys exhausted - pause queue and don't count against retries
                    self.queue.mark_failed(task.task_id, str(e), is_rate_limit=True)
                    failed += 1
                    # Handle rate limit pause (RateLimitError has retry_after, ApiKeyExhaustedError doesn't)
                    if isinstance(e, RateLimitError):
                        self._handle_rate_limit(e)
                    else:
                        # Check if ALL keys are daily-exhausted (not just RPM)
                        from api_key_manager import get_api_key_manager
                        manager = get_api_key_manager()
                        if manager.are_all_keys_daily_exhausted('image'):
                            # All keys hit daily limit — pause until midnight PT
                            seconds_until_reset = manager._get_seconds_until_midnight_pt()
                            self._paused = True
                            self._pause_until = time.time() + seconds_until_reset
                            logger.error(
                                f"ALL API keys daily-exhausted! "
                                f"Pausing until midnight PT ({seconds_until_reset // 3600}h {(seconds_until_reset % 3600) // 60}m). "
                                f"Add more keys or wait for reset."
                            )
                        else:
                            # Just RPM exhaustion — short pause
                            self._paused = True
                            self._pause_until = time.time() + RATE_LIMIT_PAUSE_DEFAULT + 5
                            logger.warning(f"API keys RPM-exhausted, pausing queue for {RATE_LIMIT_PAUSE_DEFAULT + 5}s")
                    record_image_failed('rate_limit')
                    # Record for circuit breaker
                    if "All" in str(e) and "exhausted" in str(e):
                        self._record_all_keys_exhausted()

                except FileNotFoundError as e:
                    # Missing files - permanent failure, no retries
                    self.queue.mark_failed(task.task_id, str(e), permanent=True)
                    failed += 1
                    self.images_failed += 1
                    record_image_failed('file_missing')
                    logger.error(f"Task {task.task_id} permanently failed (missing file): {e}")

                except Exception as e:
                    self.queue.mark_failed(task.task_id, str(e))
                    failed += 1
                    self.images_failed += 1
                    record_image_failed('api_error')
                    logger.error(f"Task {task.task_id} failed: {e}")

        # Record batch metrics
        batch_duration = time.time() - batch_start
        record_batch_processed(batch_duration)
        update_queue_metrics(self.queue.get_queue_stats())

        logger.info(f"Batch complete: {succeeded} succeeded, {failed} failed")

    def _generate_image(self, task: ImageTask) -> str:
        """
        Generate a single image using Gemini API.

        Args:
            task: ImageTask with all generation parameters

        Returns:
            Path to generated image

        Raises:
            RateLimitError: If API returns 429
            FileNotFoundError: If reference files are missing
            Exception: For other errors
        """
        # Validate reference files exist before attempting generation
        if task.reference_image_path and not os.path.exists(task.reference_image_path):
            raise FileNotFoundError(f"Reference image missing: {task.reference_image_path}")
        if task.product_image_path and not os.path.exists(task.product_image_path):
            raise FileNotFoundError(f"Product image missing: {task.product_image_path}")
        if task.persona_reference_path and not os.path.exists(task.persona_reference_path):
            raise FileNotFoundError(f"Persona reference missing: {task.persona_reference_path}")

        # Try all available API keys before giving up
        from api_key_manager import get_api_key_manager, ApiKeyExhaustedError
        manager = get_api_key_manager()
        num_keys = len(manager.keys)
        last_error = None

        for attempt in range(num_keys):
            # Get a fresh client with key rotation
            client, api_key = _get_client()

            # Pre-record usage so concurrent tasks see this key's RPM count
            _record_api_usage(api_key, success=True, model_type='image')

            try:
                # Call the low-level generation function
                result_path = _generate_single_image(
                    client=client,
                    api_key=api_key,
                    slide_type=task.slide_type,
                    scene_description=task.scene_description,
                    text_content=task.text_content,
                    text_position_hint=task.text_position_hint,
                    output_path=task.output_path,
                    reference_image_path=task.reference_image_path,
                    product_image_path=task.product_image_path,
                    persona_reference_path=task.persona_reference_path,
                    has_persona=task.has_persona,
                    text_style=task.text_style,
                    visual_style=task.visual_style,
                    persona_info=task.persona_info,  # Demographics for new persona creation
                    version=task.version,
                    clean_image_mode=task.clean_image_mode,
                    product_description=task.product_description,
                    shows_product_on_face=task.shows_product_on_face,  # Per-slide face tape flag
                    transformation_role=task.transformation_role or None,  # "before", "after", or None
                    transformation_problem=task.transformation_problem or None,  # "under_eye", "forehead_lines", etc.
                    layout_type=task.layout_type or "single",  # "single" or "split_screen"
                    split_config=task.split_config or None  # Split-screen configuration
                )

                # Usage already pre-recorded above
                return result_path

            except Exception as e:
                error_str = str(e).lower()
                last_error = e

                # DEBUG: log the raw error to understand what Google returns
                logger.warning(f"Key {api_key[:8]} raw error: {str(e)[:200]}")

                # Check for INVALID KEY error (wrong or revoked API key)
                if 'invalid_argument' in error_str or 'api_key_invalid' in error_str or 'api key not valid' in error_str:
                    logger.error(f"Key {api_key[:8]} is INVALID (API_KEY_INVALID). Marking and skipping.")
                    _record_api_usage(api_key, success=False, is_invalid_key=True, model_type='image')
                    continue

                # Check for FREE TIER error (billing not enabled for this model)
                if 'free_tier' in error_str or 'limit: 0' in error_str:
                    logger.error(f"Key {api_key[:8]} is FREE TIER - no quota for image model! Skipping this key.")
                    _record_api_usage(api_key, success=False, is_rate_limit=True, model_type='image')
                    try:
                        manager.redis.set(f"gemini:key:{api_key[:8]}:image:free_tier", "true")
                    except (ConnectionError, TimeoutError) as redis_err:
                        logger.warning(f"Could not mark key as free tier in Redis: {redis_err}")
                    continue

                # Check for rate limit / quota error
                if '429' in error_str or 'resource_exhausted' in error_str or 'rate limit' in error_str or 'rate_limit' in error_str or 'quota' in error_str:
                    # Detect DAILY limit vs RPM limit
                    is_daily = ('daily' in error_str or 'per day' in error_str or
                               'quota' in error_str or 'generatecontent' in error_str)

                    if is_daily:
                        # Daily limit — mark key unavailable until midnight PT, try next key
                        manager.record_daily_exhaustion(api_key, model_type='image')
                        logger.warning(f"Key {api_key[:8]} hit DAILY limit, marked until midnight PT. Trying next key... (attempt {attempt+1}/{num_keys})")
                    else:
                        # RPM limit — mark exhausted for 60s, try next key
                        _record_api_usage(api_key, success=False, is_rate_limit=True, model_type='image')
                        logger.warning(f"Key {api_key[:8]} hit RPM limit, trying next key... (attempt {attempt+1}/{num_keys})")
                    continue

                # Non-rate-limit error - don't retry with other keys
                _record_api_usage(api_key, success=False, model_type='image')
                raise

        # All keys exhausted (daily limits on all keys)
        raise ApiKeyExhaustedError(f"All {num_keys} Gemini API keys daily-exhausted for image. Resets at midnight PT.")

    def _handle_rate_limit(self, error: 'RateLimitError'):
        """
        Handle rate limit error by pausing the queue.

        Args:
            error: RateLimitError with retry_after duration
        """
        if PAUSE_ON_RATE_LIMIT:
            pause_duration = error.retry_after + 5  # Add buffer
            self._paused = True
            self._pause_until = time.time() + pause_duration
            logger.warning(f"Rate limit hit! Pausing queue for {pause_duration}s")

    def _run_startup_cleanup(self):
        """Run comprehensive cleanup on processor startup."""
        if self._startup_cleanup_done:
            return

        logger.info("Running startup cleanup...")
        cleaned = self._cleanup_all_queues()
        logger.info(f"Startup cleanup complete: {cleaned} stale tasks removed")
        self._startup_cleanup_done = True

    def _run_periodic_cleanup(self):
        """
        Run periodic cleanup of stale tasks.
        Called every CLEANUP_INTERVAL batches.
        """
        if self.batches_processed - self._last_cleanup < CLEANUP_INTERVAL:
            return

        self._last_cleanup = self.batches_processed
        logger.info("Running periodic queue cleanup...")

        try:
            cleaned = self._cleanup_all_queues()
            if cleaned > 0:
                logger.info(f"Cleanup complete: removed {cleaned} stale tasks")
        except Exception as e:
            logger.error(f"Cleanup failed: {e}")

    def _cleanup_all_queues(self) -> int:
        """Clean stale tasks from all queues."""
        cleaned = 0
        cleaned += self._cleanup_queue_by_criteria(
            self.queue.PENDING_KEY, 'pending', self.STALE_PENDING_HOURS)
        cleaned += self._cleanup_queue_by_criteria(
            self.queue.RETRY_KEY, 'retry', self.STALE_RETRY_HOURS)
        cleaned += self._cleanup_queue_by_criteria(
            self.queue.PROCESSING_KEY, 'processing', self.STALE_PROCESSING_HOURS, is_set=True)
        return cleaned

    def _cleanup_queue_by_criteria(self, queue_key: str, queue_name: str,
                                    max_age_hours: float, is_set: bool = False) -> int:
        """
        Clean tasks from a queue based on:
        1. Missing reference files
        2. Task age exceeds max_age_hours
        3. Orphaned tasks (no task data)
        """
        cleaned = 0

        # Get task IDs from queue
        if is_set:
            task_ids = self.queue.redis.smembers(queue_key)
        else:
            task_ids = self.queue.redis.zrange(queue_key, 0, -1)

        for task_id in task_ids:
            task_data_key = f"{self.queue.TASK_DATA_PREFIX}{task_id}"

            # Check for orphaned task (no data)
            if is_set:
                task_data = self.queue.redis.hgetall(task_data_key)
            else:
                task_data = self.queue.redis.hgetall(task_data_key)

            if not task_data:
                self._remove_from_queue(task_id, queue_key, is_set)
                cleaned += 1
                logger.warning(f"Removed orphaned {queue_name} task: {task_id}")
                continue

            # Check for missing reference files
            ref_path = task_data.get('reference_image_path', '')
            if ref_path and not os.path.exists(ref_path):
                self._remove_task_completely(task_id, queue_key, task_data_key, is_set)
                cleaned += 1
                logger.warning(f"Removed {queue_name} task with missing file: {task_id}")
                continue

            # Check task age
            created_at = task_data.get('created_at', '')
            if created_at:
                age_hours = self._get_task_age_hours(created_at)
                if age_hours is not None and age_hours > max_age_hours:
                    self._remove_task_completely(task_id, queue_key, task_data_key, is_set)
                    cleaned += 1
                    logger.warning(f"Removed stale {queue_name} task (age {age_hours:.1f}h): {task_id}")

        return cleaned

    def _get_task_age_hours(self, created_at: str) -> Optional[float]:
        """Get task age in hours from ISO timestamp."""
        try:
            from datetime import datetime
            create_time = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
            # Handle timezone-naive vs aware
            now = datetime.utcnow()
            if create_time.tzinfo is not None:
                from datetime import timezone
                now = datetime.now(timezone.utc)
            age_seconds = (now - create_time.replace(tzinfo=None)).total_seconds()
            return age_seconds / 3600
        except (ValueError, TypeError):
            return None

    def _remove_from_queue(self, task_id: str, queue_key: str, is_set: bool):
        """Remove task from queue (sorted set or set)."""
        if is_set:
            self.queue.redis.srem(queue_key, task_id)
        else:
            self.queue.redis.zrem(queue_key, task_id)

    def _remove_task_completely(self, task_id: str, queue_key: str,
                                 task_data_key: str, is_set: bool):
        """Remove task from queue and mark as failed."""
        self._remove_from_queue(task_id, queue_key, is_set)
        self.queue.redis.delete(task_data_key)
        self.queue.redis.sadd(self.queue.FAILED_KEY, task_id)

    def _is_circuit_open(self) -> bool:
        """Check if circuit breaker is open."""
        is_open = time.time() < self._circuit_open_until
        set_circuit_breaker_state(is_open)
        return is_open

    def _record_all_keys_exhausted(self):
        """Record that all API keys failed - may trigger circuit breaker."""
        self._consecutive_all_key_failures += 1

        if self._consecutive_all_key_failures >= self.CIRCUIT_BREAKER_THRESHOLD:
            self._circuit_open_until = time.time() + self.CIRCUIT_BREAKER_RESET_TIME
            set_circuit_breaker_state(True)
            logger.error(
                f"Circuit breaker OPENED after {self._consecutive_all_key_failures} consecutive failures. "
                f"Pausing for {self.CIRCUIT_BREAKER_RESET_TIME}s"
            )

    def _record_generation_success(self):
        """Record successful generation - resets circuit breaker counter."""
        if self._consecutive_all_key_failures > 0:
            set_circuit_breaker_state(False)
        self._consecutive_all_key_failures = 0


    def get_stats(self) -> dict:
        """Get processor statistics."""
        return {
            'running': self.running,
            'paused': self._paused,
            'batches_processed': self.batches_processed,
            'images_generated': self.images_generated,
            'images_failed': self.images_failed,
            'queue_stats': self.queue.get_queue_stats()
        }


class RateLimitError(Exception):
    """Exception for rate limit errors with retry information."""
    def __init__(self, message: str, retry_after: int = RATE_LIMIT_PAUSE_DEFAULT):
        super().__init__(message)
        self.retry_after = retry_after


def validate_startup() -> bool:
    """
    Validate all dependencies before starting.
    Returns True if all checks pass, False otherwise.
    """
    import redis

    all_ok = True

    # Check Redis connection
    try:
        r = redis.Redis(
            host=RedisConfig.HOST,
            port=RedisConfig.PORT,
            db=RedisConfig.QUEUE_DB,
            socket_connect_timeout=5
        )
        r.ping()
        logger.info(f"Redis: OK (connected to {RedisConfig.HOST}:{RedisConfig.PORT})")
    except redis.ConnectionError as e:
        logger.critical(f"Redis: FAILED - Cannot connect to {RedisConfig.HOST}:{RedisConfig.PORT}: {e}")
        all_ok = False
    except Exception as e:
        logger.critical(f"Redis: FAILED - {e}")
        all_ok = False

    # Check API keys
    try:
        from api_key_manager import get_api_key_manager
        manager = get_api_key_manager()
        total_keys = len(manager.keys)

        if total_keys == 0:
            logger.critical("API Keys: FAILED - No Gemini API keys configured!")
            all_ok = False
        else:
            # Check for free tier keys
            free_tier_count = 0
            for key in manager.keys:
                usage = manager.get_key_usage(key, 'image')
                if usage.get('is_free_tier'):
                    free_tier_count += 1
                    logger.warning(f"  Key {key[:8]}: FREE TIER (will be skipped)")

            available = total_keys - free_tier_count
            if available == 0:
                logger.critical("API Keys: FAILED - All keys are FREE TIER!")
                all_ok = False
            elif free_tier_count > 0:
                logger.warning(f"API Keys: PARTIAL - {available}/{total_keys} keys available ({free_tier_count} free tier)")
            else:
                logger.info(f"API Keys: OK ({total_keys} keys available)")
    except Exception as e:
        logger.critical(f"API Keys: FAILED - {e}")
        all_ok = False

    # Log configuration
    logger.info(f"Config: batch_size={BATCH_SIZE}, interval={BATCH_INTERVAL}s, "
                f"circuit_breaker={QueueConfig.CIRCUIT_BREAKER_THRESHOLD} failures")

    return all_ok


def main():
    """Main entry point for queue processor."""
    logger.info("=" * 60)
    logger.info("Image Queue Processor Starting")
    logger.info("=" * 60)

    # Validate dependencies
    if not validate_startup():
        logger.critical("Startup validation FAILED - exiting")
        sys.exit(1)

    logger.info("Startup validation PASSED")
    logger.info("=" * 60)

    processor = BatchProcessor()

    try:
        processor.start()
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received")
    finally:
        stats = processor.get_stats()
        logger.info(f"Final stats: {stats}")


if __name__ == '__main__':
    main()
