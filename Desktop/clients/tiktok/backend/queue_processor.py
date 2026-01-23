"""
Queue Processor - Batch Image Generation Worker
Processes up to 18 images every 60 seconds from the global queue.
"""
import os
import sys
import time
import signal
import threading
import re
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError
from typing import List, Optional
from dotenv import load_dotenv

load_dotenv()

from logging_config import get_logger

logger = get_logger('queue_processor')

from image_queue import (
    GlobalImageQueue, ImageTask, get_global_queue,
    BATCH_SIZE, BATCH_INTERVAL, MAX_RETRIES
)

from google import genai
from google.genai import types

# Import image generation function from gemini_service_v2
# We'll call the low-level generation function directly
from gemini_service_v2 import (
    _generate_single_image, _get_client, _validate_image_structure,
    IMAGE_MODEL, REQUEST_TIMEOUT
)

# Configuration
BATCH_TIMEOUT = 120  # Max seconds to wait for individual image generation
PAUSE_ON_RATE_LIMIT = True  # Pause queue on 429 errors
RATE_LIMIT_PAUSE_DEFAULT = 65  # Default pause duration for rate limits


class BatchProcessor:
    """
    Processes image generation tasks in batches.

    Every 60 seconds:
    1. Pull up to 18 tasks from queue (FIFO, respecting dependencies)
    2. Submit all to Gemini API in parallel
    3. Handle results as they complete
    4. Start next batch at exactly t+60s (strict timer)
    """

    def __init__(self, queue: Optional[GlobalImageQueue] = None):
        self.queue = queue or get_global_queue()
        self.client = _get_client()
        self.running = False
        self._stop_event = threading.Event()
        self._paused = False
        self._pause_until = 0

        # Stats
        self.batches_processed = 0
        self.images_generated = 0
        self.images_failed = 0

        logger.info(f"BatchProcessor initialized: batch_size={BATCH_SIZE}, interval={BATCH_INTERVAL}s")

    def start(self):
        """Start the processor in the current thread."""
        logger.info("BatchProcessor starting...")
        self.running = True
        self._stop_event.clear()

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
        while not self._stop_event.is_set():
            batch_start = time.time()

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
                logger.info(f"Processing batch #{self.batches_processed + 1}: {len(tasks)} tasks")
                self._process_batch(tasks)
                self.batches_processed += 1
            else:
                logger.debug("No tasks in queue, waiting...")

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
        # Track results
        succeeded = 0
        failed = 0

        # Submit all tasks to thread pool
        with ThreadPoolExecutor(max_workers=BATCH_SIZE) as executor:
            # Submit all tasks
            futures = {
                executor.submit(self._generate_image, task): task
                for task in tasks
            }

            # Process results as they complete (no timeout - strict 60s timer handles pacing)
            for future in as_completed(futures):
                task = futures[future]
                try:
                    result_path = future.result(timeout=BATCH_TIMEOUT)
                    self.queue.mark_complete(task.task_id, result_path)
                    succeeded += 1
                    self.images_generated += 1
                    logger.info(f"Task {task.task_id} completed: {os.path.basename(result_path)}")

                except TimeoutError:
                    self.queue.mark_failed(task.task_id, "Generation timeout")
                    failed += 1
                    self.images_failed += 1
                    logger.warning(f"Task {task.task_id} timed out")

                except RateLimitError as e:
                    # Rate limit - pause queue and don't count against retries
                    self.queue.mark_failed(task.task_id, str(e), is_rate_limit=True)
                    failed += 1
                    self._handle_rate_limit(e)

                except Exception as e:
                    self.queue.mark_failed(task.task_id, str(e))
                    failed += 1
                    self.images_failed += 1
                    logger.error(f"Task {task.task_id} failed: {e}")

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
            Exception: For other errors
        """
        try:
            # Call the low-level generation function
            result_path = _generate_single_image(
                client=self.client,
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
                version=task.version,
                clean_image_mode=task.clean_image_mode,
                product_description=task.product_description
            )

            return result_path

        except Exception as e:
            error_str = str(e).lower()

            # Check for rate limit
            if '429' in error_str or 'resource_exhausted' in error_str or 'rate' in error_str:
                # Extract retry delay if present
                match = re.search(r'retry.*?(\d+)', error_str)
                retry_after = int(match.group(1)) if match else RATE_LIMIT_PAUSE_DEFAULT
                raise RateLimitError(f"Rate limit exceeded, retry after {retry_after}s", retry_after)

            raise

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


def main():
    """Main entry point for queue processor."""
    logger.info("=" * 60)
    logger.info("TikTok Image Queue Processor Starting")
    logger.info(f"Configuration: batch_size={BATCH_SIZE}, interval={BATCH_INTERVAL}s")
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
