"""
TikTok Slideshow Generator - Flask Backend
"""
import os
import uuid
import shutil
import threading
import time
import json
from flask import Flask, request, jsonify, send_from_directory
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
from gemini_service_v2 import run_pipeline, GeminiServiceError
from google_drive import upload_slideshow_output, GoogleDriveError
from batch_routes import batch_bp
from admin_routes import admin_bp
from video_routes import video_bp
from preset_routes import preset_bp
from video_generator import create_videos_for_variations, VideoGeneratorError
from database import create_job, update_job_status

# Global progress tracking
progress_status = {}

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

# Frontend directory (relative to backend)
FRONTEND_DIR = os.path.join(os.path.dirname(__file__), '..', 'frontend')

# Configuration
UPLOAD_FOLDER = 'temp/uploads'
GENERATED_FOLDER = 'temp/generated'
SCRAPED_FOLDER = 'temp/scraped'

for folder in [UPLOAD_FOLDER, GENERATED_FOLDER, SCRAPED_FOLDER]:
    os.makedirs(folder, exist_ok=True)

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 200 * 1024 * 1024  # 200MB max upload

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


@app.route('/')
def serve_index():
    """Serve main frontend"""
    return send_from_directory(FRONTEND_DIR, 'index.html')


@app.route('/admin.html')
@app.route('/admin')
@app.route('/admin/keys')
@app.route('/admin/queue')
def serve_admin():
    """Serve admin dashboard"""
    return send_from_directory(FRONTEND_DIR, 'admin.html')


@app.route('/api/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({'status': 'ok', 'message': 'TikTok Slideshow Generator API is running'})


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
        update_progress(session_id, 'scraping', 'Downloading TikTok images...', 10,
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
    Main endpoint to generate TikTok slideshow images (async)

    Returns session_id immediately, then runs generation in background.
    Poll /api/status/<session_id> for progress.
    """
    # Create unique session ID for this generation
    session_id = str(uuid.uuid4())[:8]
    log = get_request_logger('app', session_id)
    session_scraped = os.path.join(SCRAPED_FOLDER, session_id)
    session_generated = os.path.join(GENERATED_FOLDER, session_id)
    session_uploads = os.path.join(UPLOAD_FOLDER, session_id)

    try:
        # Validate required fields
        tiktok_url = request.form.get('tiktok_url')
        folder_name = request.form.get('folder_name')
        product_context = request.form.get('product_context', 'Product')

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

        log.info(f"New request: url={tiktok_url[:50]}... folder={folder_name}")
        log.debug(f"Photo vars: hook={hook_photo_var}, body={body_photo_var}")
        log.debug(f"Text vars: hook={hook_text_var}, body={body_text_var}, product={product_text_var}")
        log.debug(f"Generate video: {generate_video}")

        if not tiktok_url:
            log.warning("Validation failed: missing TikTok URL")
            return jsonify({'error': 'TikTok URL is required'}), 400
        if not folder_name:
            log.warning("Validation failed: missing folder name")
            return jsonify({'error': 'Folder name is required'}), 400

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
    """Test endpoint to verify TikTok scraping works"""
    session_id = str(uuid.uuid4())[:8]
    log = get_request_logger('app', session_id)

    try:
        tiktok_url = request.json.get('tiktok_url')
        if not tiktok_url:
            return jsonify({'error': 'TikTok URL is required'}), 400

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
    """Delete a job from the queue"""
    try:
        from database import delete_job
        if delete_job(job_id):
            logger.info(f"Deleted job {job_id}")
            return jsonify({'status': 'deleted', 'job_id': job_id})
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
    logger.info("Starting Flask server on port 5001")
    app.run(debug=False, host='0.0.0.0', port=5001)
