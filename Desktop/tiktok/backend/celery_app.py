"""
Celery Application Configuration
Batch processing queue for TikTok slideshow generation
"""
import os
from celery import Celery
from dotenv import load_dotenv

load_dotenv()

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

    # Rate limiting - 10 tasks per minute to stay under Gemini API limits
    task_default_rate_limit='10/m',

    # Worker settings
    worker_concurrency=5,  # Max 5 parallel tasks
    worker_prefetch_multiplier=1,  # Don't prefetch, respect rate limits

    # Result backend settings
    result_expires=86400,  # Results expire after 24 hours

    # Task execution settings
    task_acks_late=True,  # Acknowledge after task completes (safer)
    task_reject_on_worker_lost=True,  # Requeue if worker dies

    # Retry settings
    task_annotations={
        'tasks.generate_variation': {
            'rate_limit': '10/m',
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
