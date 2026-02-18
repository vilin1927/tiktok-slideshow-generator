"""
Prometheus Metrics for Viral Slideshow Generator.
Exposes metrics at /metrics endpoint for monitoring.
"""
import time
from functools import wraps
from typing import Optional

try:
    from prometheus_client import Counter, Gauge, Histogram, Info, generate_latest, CONTENT_TYPE_LATEST
    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False
    # Create dummy classes if prometheus_client not installed
    class DummyMetric:
        def __init__(self, *args, **kwargs):
            pass
        def inc(self, *args, **kwargs):
            pass
        def dec(self, *args, **kwargs):
            pass
        def set(self, *args, **kwargs):
            pass
        def observe(self, *args, **kwargs):
            pass
        def labels(self, *args, **kwargs):
            return self
        def info(self, *args, **kwargs):
            pass
    Counter = Gauge = Histogram = Info = DummyMetric
    def generate_latest(*args, **kwargs):
        return b"# prometheus_client not installed\n"
    CONTENT_TYPE_LATEST = "text/plain"

from logging_config import get_logger

logger = get_logger('metrics')


# =============================================================================
# Image Generation Metrics
# =============================================================================

# Counters (only go up)
images_generated_total = Counter(
    'slideshow_images_generated_total',
    'Total number of images successfully generated',
    ['slide_type']  # hook, body, product
)

images_failed_total = Counter(
    'slideshow_images_failed_total',
    'Total number of image generation failures',
    ['error_type']  # rate_limit, timeout, api_error, file_missing
)

batches_processed_total = Counter(
    'slideshow_batches_processed_total',
    'Total number of batches processed'
)

api_requests_total = Counter(
    'slideshow_api_requests_total',
    'Total API requests made',
    ['model', 'status']  # model: text/image, status: success/failure
)


# Gauges (can go up or down)
queue_size = Gauge(
    'slideshow_queue_size',
    'Current queue size',
    ['queue']  # pending, processing, retry, failed
)

api_keys_available = Gauge(
    'slideshow_api_keys_available',
    'Number of available API keys',
    ['model']  # text, image
)

api_keys_total = Gauge(
    'slideshow_api_keys_total',
    'Total number of API keys configured'
)

circuit_breaker_open = Gauge(
    'slideshow_circuit_breaker_open',
    'Whether circuit breaker is open (1) or closed (0)'
)

processor_running = Gauge(
    'slideshow_processor_running',
    'Whether the queue processor is running (1) or stopped (0)'
)


# Histograms (for timing)
image_generation_duration = Histogram(
    'slideshow_image_generation_seconds',
    'Time spent generating an image',
    ['slide_type'],
    buckets=[5, 10, 20, 30, 45, 60, 90, 120, 180]  # seconds
)

batch_processing_duration = Histogram(
    'slideshow_batch_processing_seconds',
    'Time spent processing a batch',
    buckets=[10, 30, 60, 90, 120, 180, 300]  # seconds
)


# Info (static metadata)
app_info = Info(
    'slideshow_app',
    'Application information'
)


# =============================================================================
# Helper Functions
# =============================================================================

def init_metrics():
    """Initialize metrics with app info."""
    if PROMETHEUS_AVAILABLE:
        try:
            from config import QueueConfig
            app_info.info({
                'version': '2.0',
                'batch_size': str(QueueConfig.BATCH_SIZE),
                'batch_interval': str(QueueConfig.BATCH_INTERVAL),
            })
            logger.info("Prometheus metrics initialized")
        except Exception as e:
            logger.warning(f"Could not initialize metrics info: {e}")


def update_queue_metrics(stats: dict):
    """Update queue size gauges from queue stats."""
    if not PROMETHEUS_AVAILABLE:
        return
    try:
        queue_size.labels(queue='pending').set(stats.get('pending', 0))
        queue_size.labels(queue='processing').set(stats.get('processing', 0))
        queue_size.labels(queue='retry').set(stats.get('retry', 0))
        queue_size.labels(queue='failed').set(stats.get('failed', 0))
    except Exception as e:
        logger.warning(f"Could not update queue metrics: {e}")


def update_api_key_metrics(text_available: int, image_available: int, total: int):
    """Update API key availability gauges."""
    if not PROMETHEUS_AVAILABLE:
        return
    try:
        api_keys_available.labels(model='text').set(text_available)
        api_keys_available.labels(model='image').set(image_available)
        api_keys_total.set(total)
    except Exception as e:
        logger.warning(f"Could not update API key metrics: {e}")


def record_image_generated(slide_type: str, duration_seconds: float):
    """Record successful image generation."""
    if not PROMETHEUS_AVAILABLE:
        return
    try:
        images_generated_total.labels(slide_type=slide_type).inc()
        image_generation_duration.labels(slide_type=slide_type).observe(duration_seconds)
    except Exception as e:
        logger.warning(f"Could not record image metrics: {e}")


def record_image_failed(error_type: str):
    """Record failed image generation."""
    if not PROMETHEUS_AVAILABLE:
        return
    try:
        images_failed_total.labels(error_type=error_type).inc()
    except Exception as e:
        logger.warning(f"Could not record failure metrics: {e}")


def record_batch_processed(duration_seconds: float):
    """Record batch processing completion."""
    if not PROMETHEUS_AVAILABLE:
        return
    try:
        batches_processed_total.inc()
        batch_processing_duration.observe(duration_seconds)
    except Exception as e:
        logger.warning(f"Could not record batch metrics: {e}")


def record_api_request(model: str, success: bool):
    """Record an API request."""
    if not PROMETHEUS_AVAILABLE:
        return
    try:
        status = 'success' if success else 'failure'
        api_requests_total.labels(model=model, status=status).inc()
    except Exception as e:
        logger.warning(f"Could not record API request metrics: {e}")


def set_circuit_breaker_state(is_open: bool):
    """Update circuit breaker state gauge."""
    if not PROMETHEUS_AVAILABLE:
        return
    try:
        circuit_breaker_open.set(1 if is_open else 0)
    except Exception as e:
        logger.warning(f"Could not update circuit breaker metrics: {e}")


def set_processor_state(is_running: bool):
    """Update processor running state gauge."""
    if not PROMETHEUS_AVAILABLE:
        return
    try:
        processor_running.set(1 if is_running else 0)
    except Exception as e:
        logger.warning(f"Could not update processor state metrics: {e}")


def get_metrics():
    """Get metrics in Prometheus format."""
    return generate_latest()


def get_content_type():
    """Get content type for metrics response."""
    return CONTENT_TYPE_LATEST


# =============================================================================
# Timing Decorator
# =============================================================================

def timed(metric_name: str = None, slide_type: str = None):
    """Decorator to time function execution."""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            start = time.time()
            try:
                result = func(*args, **kwargs)
                return result
            finally:
                duration = time.time() - start
                if slide_type:
                    record_image_generated(slide_type, duration)
        return wrapper
    return decorator
