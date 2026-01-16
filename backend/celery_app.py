"""
Celery Application Configuration
Batch processing queue for TikTok slideshow generation
"""
import os
from celery import Celery
from dotenv import load_dotenv

load_dotenv()

# CRITICAL: Disable OpenCV threading before any Celery workers fork
# OpenCV uses internal threading which causes SIGSEGV in forked processes
import cv2
cv2.setNumThreads(0)
cv2.ocl.setUseOpenCL(False)  # Disable OpenCL as well for stability

# Redis configuration
REDIS_HOST = os.getenv('REDIS_HOST', 'localhost')
REDIS_PORT = os.getenv('REDIS_PORT', '6379')
REDIS_URL = f'redis://{REDIS_HOST}:{REDIS_PORT}/0'

# Create Celery app
celery_app = Celery(
    'tiktok_batch',
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=['tasks']  # Module containing task definitions
)

# Celery configuration
celery_app.conf.update(
    # Task settings
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='UTC',
    enable_utc=True,

    # No Celery rate limiting - Gemini's internal rate limiter (25 RPM) handles API quota
    # This allows batch to process links in parallel like single-run mode

    # Worker settings (optimized for 1 CPU VPS)
    worker_concurrency=5,  # Allow parallel task processing
    worker_prefetch_multiplier=1,  # Don't prefetch

    # Result backend settings
    result_expires=86400,  # Results expire after 24 hours

    # Task execution settings
    task_acks_late=True,  # Acknowledge after task completes (safer)
    task_reject_on_worker_lost=True,  # Requeue if worker dies

    # Retry settings (no rate_limit - Gemini handles API quota internally)
    task_annotations={
        'tasks.generate_variation': {
            'max_retries': 3,
            'default_retry_delay': 60,
        }
    },

    # Beat scheduler (if needed for scheduled tasks)
    beat_schedule={},
)

# Optional: Configure task routes for different queues
celery_app.conf.task_routes = {
    'tasks.process_batch': {'queue': 'batch'},
    'tasks.process_link': {'queue': 'links'},
    'tasks.generate_variation': {'queue': 'variations'},
}

if __name__ == '__main__':
    celery_app.start()
