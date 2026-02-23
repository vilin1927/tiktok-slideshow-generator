"""
Viral Slideshow Generator - Flask Backend
"""
import os
import uuid
import shutil
import threading
import time
import json
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory, send_file
from flask_cors import CORS
from dotenv import load_dotenv
from werkzeug.utils import secure_filename

# Load environment variables first
load_dotenv()

# Import logging (must be after dotenv for LOG_DIR/LOG_LEVEL env vars)
from logging_config import setup_logging, get_logger, get_request_logger

# Initialize logging
setup_logging()
logger = get_logger('app')

# Import our modules
from tiktok_scraper import scrape_tiktok_slideshow, TikTokScraperError
from gemini_service_v2 import run_pipeline, run_pipeline_queued, GeminiServiceError, USE_QUEUE_MODE
from google_drive import upload_slideshow_output, GoogleDriveError
from batch_routes import batch_bp
from admin_routes import admin_bp
from video_routes import video_bp
from preset_routes import preset_bp
from tiktok_copy_routes import tiktok_copy_bp
from instagram_reel_routes import ig_reel_bp
from video_generator import create_videos_for_variations, VideoGeneratorError
from database import create_job, update_job_status

# Log queue mode status
logger.info(f"Queue mode: {'ENABLED' if USE_QUEUE_MODE else 'DISABLED'}")

# Startup health check — validate external APIs
def _run_startup_health_check():
    """Non-blocking startup validation of external APIs."""
    try:
        import redis as _redis
        import requests as _req
        r = _redis.Redis(host='localhost', port=6379, db=0)
        r.ping()
        logger.info("Startup health: Redis OK")
    except Exception as e:
        logger.error(f"Startup health: Redis FAILED — {e}")

    # Validate Gemini keys (non-ASCII check happens in ApiKeyManager.__init__)
    try:
        from api_key_manager import get_api_key_manager
        mgr = get_api_key_manager()
        logger.info(f"Startup health: Gemini keys OK ({len(mgr.keys)} loaded)")
    except Exception as e:
        logger.error(f"Startup health: Gemini keys FAILED — {e}")

_run_startup_health_check()

# Global progress tracking
progress_status = {}

# Simple in-memory per-IP rate limiter for generation endpoints
_rate_limit_cache = {}  # {ip: [timestamp, timestamp, ...]}
RATE_LIMIT_MAX = 10  # Max 10 generation requests per minute per IP
RATE_LIMIT_WINDOW = 60  # seconds

def _check_rate_limit():
    """Returns True if request should be rate-limited."""
    ip = request.remote_addr or 'unknown'
    now = time.time()
    timestamps = _rate_limit_cache.get(ip, [])
    # Remove old timestamps
    timestamps = [t for t in timestamps if now - t < RATE_LIMIT_WINDOW]
    if len(timestamps) >= RATE_LIMIT_MAX:
        return True
    timestamps.append(now)
    _rate_limit_cache[ip] = timestamps
    return False

# Deduplication: prevent same URL being submitted twice within 5 minutes
_recent_submissions = {}  # {url_hash: (session_id, timestamp)}
DEDUP_WINDOW = 300  # 5 minutes

def _check_duplicate(url: str):
    """Returns existing session_id if this URL was submitted recently, else None."""
    import hashlib
    url_hash = hashlib.md5(url.strip().lower().encode()).hexdigest()
    now = time.time()
    # Clean old entries
    expired = [k for k, v in _recent_submissions.items() if now - v[1] > DEDUP_WINDOW]
    for k in expired:
        del _recent_submissions[k]
    if url_hash in _recent_submissions:
        return _recent_submissions[url_hash][0]
    return None

def _record_submission(url: str, session_id: str):
    """Record a URL submission for dedup tracking."""
    import hashlib
    url_hash = hashlib.md5(url.strip().lower().encode()).hexdigest()
    _recent_submissions[url_hash] = (session_id, time.time())

app = Flask(__name__)
CORS(app)

# Register batch processing blueprint
app.register_blueprint(batch_bp)

# Register admin blueprint
app.register_blueprint(admin_bp)

# Register video blueprint
app.register_blueprint(video_bp)

# Register preset blueprint
app.register_blueprint(preset_bp)

# Register Video Copy blueprint
app.register_blueprint(tiktok_copy_bp)

# Register Instagram Reel blueprint
app.register_blueprint(ig_reel_bp)

# JSON error handlers for consistent API responses
@app.errorhandler(400)
def bad_request(e):
    return jsonify({'error': 'Bad request — invalid JSON or malformed input'}), 400

@app.errorhandler(404)
def not_found(e):
    # Only return JSON for API routes
    if request.path.startswith('/api/'):
        return jsonify({'error': 'Not found'}), 404
    return e

# Frontend directory (relative to backend)
FRONTEND_DIR = os.path.join(os.path.dirname(__file__), '..', 'frontend')

# Configuration
UPLOAD_FOLDER = 'temp/uploads'
GENERATED_FOLDER = 'temp/generated'
SCRAPED_FOLDER = 'temp/scraped'

for folder in [UPLOAD_FOLDER, GENERATED_FOLDER, SCRAPED_FOLDER]:
    os.makedirs(folder, exist_ok=True)

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024  # 500MB max upload (video files can be large)

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


@app.errorhandler(413)
def request_entity_too_large(error):
    return jsonify({'error': 'File too large. Maximum upload size is 500MB.'}), 413


@app.route('/')
def serve_index():
    """Serve main frontend"""
    return send_from_directory(FRONTEND_DIR, 'index.html')


@app.route('/admin.html')
@app.route('/admin')
@app.route('/admin/keys')
@app.route('/admin/queue')
@app.route('/admin/photos')
def serve_admin():
    """Serve admin dashboard"""
    return send_from_directory(FRONTEND_DIR, 'admin.html')


@app.route('/video-copy')
@app.route('/video-copy.html')
def serve_video_copy():
    """Serve Video Copy tool page"""
    return send_from_directory(FRONTEND_DIR, 'video-copy.html')


@app.route('/tiktok-copy')
@app.route('/tiktok-copy.html')
def redirect_tiktok_copy():
    """301 redirect from old TikTok Copy URL to new Video Copy URL"""
    from flask import redirect
    return redirect('/video-copy', code=301)


@app.route('/instagram-reel')
@app.route('/instagram-reel.html')
def serve_instagram_reel():
    """Serve Instagram Reel Generator page"""
    return send_from_directory(FRONTEND_DIR, 'instagram-reel.html')


@app.route('/api/health', methods=['GET'])
def health_check():
    """Comprehensive health check endpoint for monitoring."""
    import time as time_module
    health = {
        'status': 'healthy',
        'timestamp': datetime.utcnow().isoformat(),
        'service': 'Viral Slideshow Generator API',
        'checks': {}
    }

    # Check Redis
    try:
        import redis
        redis_client = redis.Redis(
            host=os.getenv('REDIS_HOST', 'localhost'),
            port=int(os.getenv('REDIS_PORT', '6379')),
            db=int(os.getenv('REDIS_QUEUE_DB', '1'))
        )
        redis_client.ping()
        health['checks']['redis'] = 'ok'
    except Exception as e:
        health['checks']['redis'] = f'error: {str(e)[:50]}'
        health['status'] = 'unhealthy'

    # Check queue processor heartbeat
    try:
        last_heartbeat = redis_client.get('queue_processor:heartbeat')
        if last_heartbeat:
            age = time_module.time() - float(last_heartbeat)
            if age < 120:  # 2 minutes
                health['checks']['queue_processor'] = f'ok (last seen {age:.0f}s ago)'
            else:
                health['checks']['queue_processor'] = f'stale ({age:.0f}s ago)'
                health['status'] = 'degraded'
        else:
            health['checks']['queue_processor'] = 'unknown (no heartbeat)'
    except Exception as e:
        health['checks']['queue_processor'] = f'error: {str(e)[:50]}'

    # Check API keys
    try:
        from api_key_manager import get_api_key_manager
        manager = get_api_key_manager()
        summary = manager.get_summary('image')
        available = summary['image']['available_keys']
        total = summary['total_keys']
        free_tier_count = sum(1 for s in summary['image']['keys'] if s.get('is_free_tier'))

        if free_tier_count > 0:
            health['checks']['api_keys'] = f'{available}/{total} available ({free_tier_count} free tier)'
        else:
            health['checks']['api_keys'] = f'{available}/{total} available'

        if available == 0:
            health['status'] = 'degraded'
    except Exception as e:
        health['checks']['api_keys'] = f'error: {str(e)[:50]}'

    # Check queue stats
    try:
        from image_queue import get_global_queue
        queue = get_global_queue()
        stats = queue.get_queue_stats()
        health['checks']['queue'] = {
            'pending': stats['pending'],
            'processing': stats['processing'],
            'retry': stats['retry'],
            'failed': stats['failed']
        }
    except Exception as e:
        health['checks']['queue'] = f'error: {str(e)[:50]}'

    status_code = 200 if health['status'] == 'healthy' else 503
    return jsonify(health), status_code


@app.route('/metrics', methods=['GET'])
def prometheus_metrics():
    """Prometheus metrics endpoint for monitoring."""
    try:
        from metrics import get_metrics, get_content_type, update_queue_metrics, update_api_key_metrics

        # Update current queue stats before returning metrics
        try:
            from image_queue import get_global_queue
            queue = get_global_queue()
            stats = queue.get_queue_stats()
            update_queue_metrics(stats)
        except Exception as e:
            logger.warning(f"Failed to update queue metrics: {e}")

        # Update API key availability
        try:
            from api_key_manager import get_api_key_manager
            manager = get_api_key_manager()
            text_summary = manager.get_summary('text')
            image_summary = manager.get_summary('image')
            update_api_key_metrics(
                text_available=text_summary['text']['available_keys'],
                image_available=image_summary['image']['available_keys'],
                total=manager.get_summary()['total_keys']
            )
        except Exception as e:
            logger.warning(f"Failed to update API key metrics: {e}")

        from flask import Response
        return Response(get_metrics(), mimetype=get_content_type())
    except ImportError:
        return "prometheus_client not installed", 501


@app.route('/api/verify-access', methods=['POST'])
def verify_access():
    """Verify page access password"""
    data = request.get_json() or {}
    password = data.get('password', '')

    page_password = os.getenv('PAGE_PASSWORD')
    if not page_password:
        # If no password configured, allow access
        return jsonify({'valid': True})

    if password == page_password:
        return jsonify({'valid': True})

    return jsonify({'valid': False, 'error': 'Invalid password'}), 401


@app.route('/api/status/<session_id>', methods=['GET'])
def get_status(session_id):
    """Get progress status for a session"""
    if session_id in progress_status:
        return jsonify(progress_status[session_id])
    return jsonify({'step': 'unknown', 'message': 'Session not found', 'progress': 0})


def update_progress(session_id, step, message, progress, details=None):
    """Update progress status for a session"""
    progress_status[session_id] = {
        'step': step,
        'message': message,
        'progress': progress,
        'details': details or {}
    }


def run_generation(session_id, job_id, tiktok_url, folder_name, product_context,
                   saved_product_images, session_scraped, session_generated,
                   hook_photo_var=1, hook_text_var=1,
                   body_photo_var=1, body_text_var=1,
                   product_text_var=1, generate_video=False, preset_id='gemini'):
    """Background task for generation using v2 pipeline with photo × text variations"""
    log = get_request_logger('app', session_id)
    start_time = time.time()

    log.info(f"Starting generation pipeline - URL: {tiktok_url[:60]}...")
    log.debug(f"Params: folder={folder_name}, products={len(saved_product_images)}")
    log.debug(f"Photo vars: hook={hook_photo_var}, body={body_photo_var}")
    log.debug(f"Text vars: hook={hook_text_var}, body={body_text_var}, product={product_text_var}")
    log.debug(f"Generate video: {generate_video}, preset: {preset_id}")

    # Update job status to processing
    update_job_status(job_id, 'processing')

    try:
        # ===== STEP 1: Scrape TikTok =====
        log.info("Step 1/4: Scraping TikTok slideshow")
        update_progress(session_id, 'scraping', 'Downloading slideshow images...', 10,
                       {'detail': 'Downloading via proxy'})
        try:
            scraped = scrape_tiktok_slideshow(tiktok_url, session_scraped, session_id)
        except TikTokScraperError as e:
            log.error(f"Scraping failed: {str(e)}")
            update_progress(session_id, 'error', f'Scraping failed: {str(e)}', 0)
            update_job_status(job_id, 'failed', error_message=f'Scraping failed: {str(e)}')
            return

        if not scraped['images']:
            log.error("No slideshow images found in TikTok")
            update_progress(session_id, 'error', 'No slideshow images found', 0)
            update_job_status(job_id, 'failed', error_message='No slideshow images found')
            return

        log.info(f"Scraped {len(scraped['images'])} images, audio={'yes' if scraped.get('audio') else 'no'}")
        update_progress(session_id, 'scraping', f'Downloaded {len(scraped["images"])} images', 25,
                       {'images_count': len(scraped['images'])})

        # ===== STEP 2 & 3: Analyze + Generate (v2 pipeline) =====
        log.info("Step 2-3/4: Analyzing and generating slides")

        def progress_callback(step, message, percent):
            update_progress(session_id, step, message, percent)

        try:
            # Use queued pipeline if queue mode is enabled
            if USE_QUEUE_MODE:
                log.info("Using QUEUED pipeline (global queue system)")
                result = run_pipeline_queued(
                    slide_paths=scraped['images'],
                    product_image_paths=saved_product_images,
                    product_description=product_context,
                    output_dir=session_generated,
                    job_id=job_id,  # Required for queue mode
                    progress_callback=progress_callback,
                    hook_photo_var=hook_photo_var,
                    hook_text_var=hook_text_var,
                    body_photo_var=body_photo_var,
                    body_text_var=body_text_var,
                    product_text_var=product_text_var,
                    request_id=session_id,
                    preset_id=preset_id
                )
            else:
                log.info("Using DIRECT pipeline (no queue)")
                result = run_pipeline(
                    slide_paths=scraped['images'],
                    product_image_paths=saved_product_images,
                    product_description=product_context,
                    output_dir=session_generated,
                    progress_callback=progress_callback,
                    hook_photo_var=hook_photo_var,
                    hook_text_var=hook_text_var,
                    body_photo_var=body_photo_var,
                    body_text_var=body_text_var,
                    product_text_var=product_text_var,
                    request_id=session_id,
                    preset_id=preset_id
                )
        except GeminiServiceError as e:
            log.error(f"Generation failed: {str(e)}")
            update_progress(session_id, 'error', f'Generation failed: {str(e)}', 0)
            update_job_status(job_id, 'failed', error_message=f'Generation failed: {str(e)}')
            return

        all_generated = result['generated_images']

        if not all_generated:
            log.error("No slides were generated")
            update_progress(session_id, 'error', 'Failed to generate any slides', 0)
            update_job_status(job_id, 'failed', error_message='Failed to generate any slides')
            return

        log.info(f"Generated {len(all_generated)} slides")
        update_progress(session_id, 'generating', f'Generated {len(all_generated)} slides', 85)

        # ===== STEP 3.5: Create Videos (if requested) =====
        video_paths = []
        if generate_video:
            log.info("Step 3.5: Creating slideshow videos")
            update_progress(session_id, 'video', 'Creating slideshow videos...', 88)
            try:
                video_paths = create_videos_for_variations(
                    generated_images=all_generated,
                    audio_path=scraped.get('audio'),
                    output_dir=session_generated,
                    request_id=session_id
                )
                log.info(f"Created {len(video_paths)} videos")
                # Add videos to upload list
                all_generated.extend(video_paths)
            except VideoGeneratorError as e:
                log.warning(f"Video generation failed: {str(e)} - continuing without videos")
                # Don't fail the whole job, just skip videos

        # ===== STEP 4: Upload to Google Drive =====
        log.info("Step 4/4: Uploading to Google Drive")
        update_progress(session_id, 'uploading', 'Uploading to Google Drive...', 92)
        try:
            upload_result = upload_slideshow_output(
                session_generated,
                folder_name,
                all_generated,
                scraped.get('audio'),
                request_id=session_id
            )
        except GoogleDriveError as e:
            log.error(f"Upload failed: {str(e)}")
            update_progress(session_id, 'error', f'Upload failed: {str(e)}', 0)
            update_job_status(job_id, 'failed', error_message=f'Upload failed: {str(e)}')
            return

        elapsed = time.time() - start_time
        log.info(f"Pipeline complete in {elapsed:.1f}s - {upload_result['folder_link']}")

        # Update job as completed
        update_job_status(job_id, 'completed', drive_folder_url=upload_result['folder_link'])

        update_progress(session_id, 'complete', 'Done!', 100, {
            'folder_link': upload_result['folder_link'],
            'analysis': result.get('analysis'),
            'stats': {
                'source_slides': len(scraped['images']),
                'generated_slides': len(all_generated) - len(video_paths),
                'generated_videos': len(video_paths),
                'uploaded_images': len(upload_result['uploaded_images']),
                'has_audio': upload_result['audio_file'] is not None
            }
        })

    except Exception as e:
        log.error(f"Unexpected error: {str(e)}", exc_info=True)
        update_progress(session_id, 'error', f'Unexpected error: {str(e)}', 0)
        update_job_status(job_id, 'failed', error_message=f'Unexpected error: {str(e)}')

    finally:
        # Clean up progress after 10 minutes
        time.sleep(600)
        progress_status.pop(session_id, None)


@app.route('/api/generate', methods=['POST'])
def generate_slideshow():
    """
    Main endpoint to generate slideshow images (async)

    Returns session_id immediately, then runs generation in background.
    Poll /api/status/<session_id> for progress.
    """
    if _check_rate_limit():
        return jsonify({'error': 'Too many requests. Please wait a moment before generating again.'}), 429

    # Create unique session ID for this generation
    session_id = str(uuid.uuid4())[:8]
    log = get_request_logger('app', session_id)
    session_scraped = os.path.join(SCRAPED_FOLDER, session_id)
    session_generated = os.path.join(GENERATED_FOLDER, session_id)
    session_uploads = os.path.join(UPLOAD_FOLDER, session_id)

    try:
        # Validate required fields
        tiktok_url = request.form.get('source_url') or request.form.get('tiktok_url')
        folder_name = request.form.get('folder_name')
        product_context = request.form.get('product_context', 'Product')

        # Sanitize inputs
        if product_context and not product_context.strip():
            product_context = 'Product'  # Default if whitespace-only
        if folder_name:
            folder_name = folder_name.strip()[:200]  # Cap length, strip whitespace

        # Photo × Text variation params (default to 1 if not provided)
        hook_photo_var = int(request.form.get('hook_photo_var', 1))
        hook_text_var = int(request.form.get('hook_text_var', 1))
        body_photo_var = int(request.form.get('body_photo_var', 1))
        body_text_var = int(request.form.get('body_text_var', 1))
        product_text_var = int(request.form.get('product_text_var', 1))

        # Video generation option (default false)
        generate_video = request.form.get('generate_video', 'false').lower() == 'true'

        # Text preset (default 'gemini' = let Gemini render text)
        preset_id = request.form.get('preset_id', 'gemini')

        # Clamp to valid range (1-5)
        hook_photo_var = max(1, min(5, hook_photo_var))
        hook_text_var = max(1, min(5, hook_text_var))
        body_photo_var = max(1, min(5, body_photo_var))
        body_text_var = max(1, min(5, body_text_var))
        product_text_var = max(1, min(5, product_text_var))

        if not tiktok_url:
            log.warning("Validation failed: missing URL")
            return jsonify({'error': 'Slideshow URL is required'}), 400

        # Validate URL format
        tiktok_url = tiktok_url.strip()
        if not tiktok_url.startswith(('https://www.tiktok.com/', 'https://tiktok.com/', 'https://vm.tiktok.com/', 'http://www.tiktok.com/', 'http://tiktok.com/')):
            log.warning(f"Validation failed: invalid URL format: {tiktok_url[:80]}")
            return jsonify({'error': 'Invalid URL. Must be a TikTok link (https://www.tiktok.com/...)'}), 400

        if not folder_name:
            log.warning("Validation failed: missing folder name")
            return jsonify({'error': 'Folder name is required'}), 400

        # Check for duplicate submission (same URL within 5 minutes)
        existing_session = _check_duplicate(tiktok_url)
        if existing_session:
            log.info(f"Duplicate URL detected, returning existing session {existing_session}")
            return jsonify({
                'message': 'This URL is already being processed. Returning existing job.',
                'session_id': existing_session,
                'status': 'duplicate'
            }), 200

        log.info(f"New request: url={tiktok_url[:50]}... folder={folder_name}")

        # Get product images (multiple allowed for photo variations)
        product_images = request.files.getlist('product_images')
        if not product_images or len(product_images) == 0 or product_images[0].filename == '':
            log.warning("Validation failed: missing product image")
            return jsonify({'error': 'At least one product image is required'}), 400

        # Save all product images
        os.makedirs(session_uploads, exist_ok=True)
        saved_product_images = []
        for i, img in enumerate(product_images):
            if img and img.filename and allowed_file(img.filename):
                filename = secure_filename(img.filename)
                filepath = os.path.join(session_uploads, f'product_{i}_{filename}')
                img.save(filepath)
                saved_product_images.append(filepath)
                log.debug(f"Saved product image {i+1}: {filename}")

        if not saved_product_images:
            log.warning("Validation failed: invalid product image format")
            return jsonify({'error': 'Invalid product image(s)'}), 400

        log.info(f"Saved {len(saved_product_images)} product images")

        # Create job in database for tracking
        job_id = create_job(
            job_type='single',
            tiktok_url=tiktok_url,
            total_links=1,
            product_description=product_context,
            folder_name=folder_name,
            variations_config=json.dumps({
                'hook_photo_var': hook_photo_var,
                'hook_text_var': hook_text_var,
                'body_photo_var': body_photo_var,
                'body_text_var': body_text_var,
                'product_text_var': product_text_var,
                'generate_video': generate_video,
                'preset_id': preset_id,
                'product_image_paths': saved_product_images
            })
        )
        log.info(f"Created job {job_id} in database")

        # Initialize progress
        update_progress(session_id, 'starting', 'Starting generation...', 5)

        # Start background generation thread (v2 pipeline with photo × text variations)
        thread = threading.Thread(target=run_generation, args=(
            session_id, job_id, tiktok_url, folder_name, product_context,
            saved_product_images, session_scraped, session_generated,
            hook_photo_var, hook_text_var,
            body_photo_var, body_text_var,
            product_text_var, generate_video, preset_id
        ))
        thread.start()
        log.info("Background thread started")

        # Record submission for dedup
        _record_submission(tiktok_url, session_id)

        # Return immediately with session_id for polling
        return jsonify({
            'status': 'started',
            'session_id': session_id,
            'message': 'Generation started. Poll /api/status/{session_id} for progress.'
        })

    except ValueError as e:
        log.error(f"Invalid input: {str(e)}")
        return jsonify({'error': f'Invalid input: {str(e)}'}), 400
    except Exception as e:
        log.error(f"Server error: {str(e)}", exc_info=True)
        # Clean up on error
        for folder in [session_scraped, session_generated, session_uploads]:
            shutil.rmtree(folder, ignore_errors=True)
        return jsonify({'error': f'Server error: {str(e)}'}), 500


@app.route('/api/test-scrape', methods=['POST'])
def test_scrape():
    """Test endpoint to verify scraping works"""
    session_id = str(uuid.uuid4())[:8]
    log = get_request_logger('app', session_id)

    try:
        tiktok_url = request.json.get('source_url') or request.json.get('tiktok_url')
        if not tiktok_url:
            return jsonify({'error': 'URL is required'}), 400

        log.info(f"Test scrape: {tiktok_url[:50]}...")
        output_dir = os.path.join(SCRAPED_FOLDER, f'test_{session_id}')

        scraped = scrape_tiktok_slideshow(tiktok_url, output_dir, session_id)

        log.info(f"Test scrape success: {len(scraped['images'])} images")
        return jsonify({
            'status': 'success',
            'images_count': len(scraped['images']),
            'has_audio': scraped['audio'] is not None,
            'metadata': scraped['metadata']
        })

    except TikTokScraperError as e:
        log.error(f"Test scrape failed: {str(e)}")
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        log.error(f"Test scrape error: {str(e)}", exc_info=True)
        return jsonify({'error': f'Server error: {str(e)}'}), 500


@app.route('/api/test-text-render', methods=['POST'])
def test_text_render():
    """Test endpoint to render text with a preset on a blank background"""
    import io
    from PIL import Image
    from text_renderer import render_text
    from presets import get_preset

    try:
        data = request.json
        text = data.get('text', 'Sample text')
        preset_id = data.get('preset_id', 'classic_shadow')
        bg_color = data.get('background_color', '#1a1a1a')

        # Create a blank TikTok-sized image
        width, height = 828, 1472
        img = Image.new('RGB', (width, height), bg_color)

        # Save temp image
        temp_path = f'/tmp/test_bg_{uuid.uuid4().hex[:8]}.png'
        img.save(temp_path)

        # Create safe zone (center of image)
        zone = {
            'bounds': {
                'x': 50,
                'y': height // 3,
                'w': width - 100,
                'h': height // 3
            },
            'text_color_suggestion': 'white'
        }

        # Render text
        output_path = f'/tmp/test_render_{uuid.uuid4().hex[:8]}.png'
        result = render_text(temp_path, text, zone, preset_id, output_path)

        # Clean up temp bg
        os.remove(temp_path)

        # Return image
        return send_file(output_path, mimetype='image/png')

    except Exception as e:
        return jsonify({'error': f'Render failed: {str(e)}'}), 500


@app.route('/api/jobs/<job_id>', methods=['GET'])
def get_job_details(job_id):
    """Get single job details for admin dashboard"""
    try:
        from database import get_job
        job = get_job(job_id)
        if job:
            return jsonify(job)
        return jsonify({'error': 'Job not found'}), 404
    except Exception as e:
        logger.error(f"Failed to get job {job_id}: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/jobs/<job_id>', methods=['DELETE'])
def delete_job_endpoint(job_id):
    """Delete a job from the queue and revoke its Celery tasks"""
    try:
        from database import delete_job, get_job_task_ids
        from celery_utils import revoke_tasks

        # Get task IDs before deleting from database
        task_ids = get_job_task_ids(job_id)

        # Delete from database
        if delete_job(job_id):
            # Revoke Celery tasks
            revoked = revoke_tasks(task_ids)
            logger.info(f"Deleted job {job_id}, revoked {revoked} Celery tasks")
            return jsonify({'status': 'deleted', 'job_id': job_id, 'tasks_revoked': revoked})
        return jsonify({'error': 'Job not found'}), 404
    except Exception as e:
        logger.error(f"Failed to delete job {job_id}: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/jobs', methods=['GET'])
def list_all_jobs():
    """List all jobs with optional filtering for admin dashboard"""
    try:
        from database import list_jobs, get_jobs_count

        # Get query params
        limit = request.args.get('limit', 20, type=int)
        offset = request.args.get('offset', 0, type=int)
        job_type = request.args.get('type', None)
        status = request.args.get('status', None)

        # Fetch jobs
        jobs = list_jobs(
            job_type=job_type if job_type else None,
            status=status if status else None,
            limit=limit,
            offset=offset
        )

        total = get_jobs_count(
            job_type=job_type if job_type else None,
            status=status if status else None
        )

        return jsonify({
            'jobs': jobs,
            'total': total,
            'limit': limit,
            'offset': offset
        })

    except Exception as e:
        logger.error(f"Failed to list jobs: {e}")
        return jsonify({'jobs': [], 'total': 0, 'error': str(e)})


if __name__ == '__main__':
    port = int(os.getenv('PORT', 5001))
    host = os.getenv('HOST', '0.0.0.0')
    logger.info(f"Starting Flask server on {host}:{port}")
    app.run(debug=False, host=host, port=port)
