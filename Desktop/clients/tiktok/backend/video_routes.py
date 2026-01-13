"""
Video Routes - API endpoints for video generation
Handles standalone video creation and slideshow-to-video conversion
"""
import os
import uuid
import threading
import time
from flask import Blueprint, request, jsonify
from werkzeug.utils import secure_filename

from logging_config import get_logger, get_request_logger
from database import (
    create_video_job, get_video_job, update_video_job_status,
    get_next_pending_video_job, list_video_jobs, get_video_jobs_count
)
from video_generator import create_video, VideoGeneratorError
from google_drive import upload_file, create_folder, set_folder_public, get_folder_link, GoogleDriveError

logger = get_logger('video_routes')

video_bp = Blueprint('video', __name__, url_prefix='/api/video')

# Configuration
UPLOAD_FOLDER = 'temp/video_uploads'
OUTPUT_FOLDER = 'temp/video_output'
ALLOWED_IMAGE_EXTENSIONS = {'png', 'jpg', 'jpeg', 'webp'}
ALLOWED_AUDIO_EXTENSIONS = {'mp3', 'wav', 'm4a'}

# Ensure directories exist
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

# Video worker status
_worker_running = False
_worker_thread = None


def allowed_image(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_IMAGE_EXTENSIONS


def allowed_audio(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_AUDIO_EXTENSIONS


def process_video_job(job_id: str):
    """Process a single video job."""
    log = get_request_logger('video_routes', job_id[:8])

    job = get_video_job(job_id)
    if not job:
        logger.error(f"[{job_id[:8]}] Job not found")
        return

    log.info(f"Processing video job: {job['job_type']}")
    update_video_job_status(job_id, 'processing')

    try:
        # Get image paths
        image_paths = job.get('image_paths', [])
        if not image_paths:
            raise VideoGeneratorError("No images provided")

        # Verify all images exist
        for img_path in image_paths:
            if not os.path.exists(img_path):
                raise VideoGeneratorError(f"Image not found: {img_path}")

        audio_path = job.get('audio_path')
        if audio_path and not os.path.exists(audio_path):
            log.warning(f"Audio file not found: {audio_path}, creating silent video")
            audio_path = None

        # Create output directory
        output_dir = os.path.join(OUTPUT_FOLDER, job_id[:8])
        os.makedirs(output_dir, exist_ok=True)

        # Generate video
        output_filename = f"slideshow_{job.get('variation_key', 'video')}.mp4"
        output_path = os.path.join(output_dir, output_filename)

        log.info(f"Creating video with {len(image_paths)} images")
        create_video(
            image_paths=image_paths,
            audio_path=audio_path,
            output_path=output_path,
            request_id=job_id[:8]
        )

        # Upload to Google Drive
        folder_name = job.get('folder_name', f'Video_{job_id[:8]}')
        log.info(f"Uploading to Google Drive: {folder_name}")

        try:
            # Create folder
            drive_folder_id = create_folder(folder_name)
            set_folder_public(drive_folder_id)
            drive_folder_url = get_folder_link(drive_folder_id)

            # Upload video
            upload_file(output_path, drive_folder_id)

            log.info(f"Video uploaded: {drive_folder_url}")
            update_video_job_status(
                job_id, 'completed',
                output_path=output_path,
                drive_url=drive_folder_url
            )
        except GoogleDriveError as e:
            log.error(f"Drive upload failed: {e}")
            # Still mark as completed with local path
            update_video_job_status(
                job_id, 'completed',
                output_path=output_path,
                error_message=f"Upload failed: {e}"
            )

    except VideoGeneratorError as e:
        log.error(f"Video generation failed: {e}")
        update_video_job_status(job_id, 'failed', error_message=str(e))
    except Exception as e:
        log.error(f"Unexpected error: {e}", exc_info=True)
        update_video_job_status(job_id, 'failed', error_message=str(e))


def video_worker():
    """Background worker that processes video jobs from the queue."""
    global _worker_running
    logger.info("Video worker started")

    while _worker_running:
        try:
            job = get_next_pending_video_job()
            if job:
                process_video_job(job['id'])
            else:
                time.sleep(2)  # No jobs, wait before checking again
        except Exception as e:
            logger.error(f"Video worker error: {e}", exc_info=True)
            time.sleep(5)  # Wait longer on error

    logger.info("Video worker stopped")


def start_video_worker():
    """Start the video worker thread if not already running."""
    global _worker_running, _worker_thread

    if _worker_running and _worker_thread and _worker_thread.is_alive():
        return  # Already running

    _worker_running = True
    _worker_thread = threading.Thread(target=video_worker, daemon=True)
    _worker_thread.start()
    logger.info("Video worker thread started")


def stop_video_worker():
    """Stop the video worker thread."""
    global _worker_running
    _worker_running = False
    logger.info("Video worker stopping...")


# ============ API Endpoints ============

@video_bp.route('/create', methods=['POST'])
def create_standalone_video():
    """
    Create a video from uploaded images and audio.

    Expects multipart/form-data with:
    - images: Multiple image files
    - audio: Single audio file (optional)
    - folder_name: Output folder name for Google Drive
    """
    request_id = str(uuid.uuid4())[:8]
    log = get_request_logger('video_routes', request_id)

    try:
        # Validate images
        images = request.files.getlist('images')
        if not images or len(images) == 0:
            return jsonify({'error': 'At least one image is required'}), 400

        # Create session directory
        session_dir = os.path.join(UPLOAD_FOLDER, request_id)
        os.makedirs(session_dir, exist_ok=True)

        # Save images
        saved_images = []
        for i, img in enumerate(images):
            if img and img.filename and allowed_image(img.filename):
                ext = img.filename.rsplit('.', 1)[1].lower()
                filename = f"slide_{i+1:02d}.{ext}"
                filepath = os.path.join(session_dir, filename)
                img.save(filepath)
                saved_images.append(filepath)
                log.debug(f"Saved image: {filename}")

        if not saved_images:
            return jsonify({'error': 'No valid images provided'}), 400

        log.info(f"Received {len(saved_images)} images")

        # Save audio if provided
        audio_path = None
        audio = request.files.get('audio')
        if audio and audio.filename and allowed_audio(audio.filename):
            ext = audio.filename.rsplit('.', 1)[1].lower()
            audio_filename = f"audio.{ext}"
            audio_path = os.path.join(session_dir, audio_filename)
            audio.save(audio_path)
            log.info(f"Saved audio: {audio_filename}")

        # Get folder name
        folder_name = request.form.get('folder_name', f'Video_{request_id}')

        # Sort images by filename to ensure correct order
        saved_images.sort()

        # Create video job
        job_id = create_video_job(
            job_type='standalone',
            image_paths=saved_images,
            audio_path=audio_path,
            folder_name=folder_name
        )
        log.info(f"Created video job: {job_id}")

        # Ensure worker is running
        start_video_worker()

        return jsonify({
            'status': 'queued',
            'job_id': job_id,
            'message': 'Video queued for processing. Poll /api/video/status/{job_id} for progress.'
        })

    except Exception as e:
        log.error(f"Error creating video job: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@video_bp.route('/from-slideshow', methods=['POST'])
def create_from_slideshow():
    """
    Create videos from existing slideshow session.

    Expects JSON with:
    - session_id: ID of the slideshow generation session
    - variations: List of variation keys to convert (optional, defaults to all)
    - audio_path: Path to audio file
    - folder_name: Output folder name
    """
    request_id = str(uuid.uuid4())[:8]
    log = get_request_logger('video_routes', request_id)

    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'JSON body required'}), 400

        session_id = data.get('session_id')
        if not session_id:
            return jsonify({'error': 'session_id is required'}), 400

        # Get generated images directory
        generated_dir = os.path.join('temp/generated', session_id)
        if not os.path.exists(generated_dir):
            return jsonify({'error': f'Session not found: {session_id}'}), 404

        # Get all images in the session
        all_images = []
        for f in os.listdir(generated_dir):
            if f.lower().endswith(('.png', '.jpg', '.jpeg')):
                all_images.append(os.path.join(generated_dir, f))

        if not all_images:
            return jsonify({'error': 'No images found in session'}), 404

        audio_path = data.get('audio_path')
        folder_name = data.get('folder_name', f'Video_{session_id}')
        variations = data.get('variations')  # Optional filter

        log.info(f"Creating videos from session {session_id} ({len(all_images)} images)")

        # Group images by variation
        import re
        variation_groups = {}

        for img_path in all_images:
            filename = os.path.basename(img_path)
            match = re.search(r'_p(\d+)_t(\d+)\.png$', filename, re.IGNORECASE)

            if match:
                var_key = f"p{match.group(1)}_t{match.group(2)}"
                if variations and var_key not in variations:
                    continue  # Skip if not in requested variations
                if var_key not in variation_groups:
                    variation_groups[var_key] = []
                variation_groups[var_key].append(img_path)

        if not variation_groups:
            # Fallback: create single video from all images
            variation_groups['all'] = sorted(all_images)

        # Sort images: hook → body slides (except last) → product → last body slide
        def sort_slides_for_video(images):
            hooks = []
            bodies = {}  # {num: path}
            products = []
            others = []

            for img_path in images:
                filename = os.path.basename(img_path).lower()
                if filename.startswith('hook'):
                    hooks.append(img_path)
                elif filename.startswith('body'):
                    match = re.search(r'body_(\d+)', filename)
                    num = int(match.group(1)) if match else 0
                    bodies[num] = img_path
                elif filename.startswith('product'):
                    products.append(img_path)
                else:
                    others.append(img_path)

            result = []
            result.extend(sorted(hooks))  # Hooks first

            if bodies:
                sorted_body_nums = sorted(bodies.keys())
                if len(sorted_body_nums) > 1:
                    # Add all body slides except the last
                    for num in sorted_body_nums[:-1]:
                        result.append(bodies[num])
                    # Add products before last body
                    result.extend(sorted(products))
                    # Add last body slide
                    result.append(bodies[sorted_body_nums[-1]])
                else:
                    # Only one body slide - product goes after it
                    result.append(bodies[sorted_body_nums[0]])
                    result.extend(sorted(products))
            else:
                result.extend(sorted(products))

            result.extend(sorted(others))
            return result

        # Create a job for each variation
        jobs = []
        for var_key, images in sorted(variation_groups.items()):
            sorted_images = sort_slides_for_video(images)

            job_id = create_video_job(
                job_type='from_slideshow',
                image_paths=sorted_images,
                audio_path=audio_path,
                folder_name=f"{folder_name}_{var_key}",
                source_session_id=session_id,
                variation_key=var_key
            )
            jobs.append({
                'job_id': job_id,
                'variation': var_key,
                'status': 'queued',
                'images_count': len(sorted_images)
            })
            log.info(f"Created video job for {var_key}: {job_id}")

        # Ensure worker is running
        start_video_worker()

        return jsonify({
            'status': 'queued',
            'jobs': jobs,
            'total_jobs': len(jobs),
            'message': f'{len(jobs)} video(s) queued for processing'
        })

    except Exception as e:
        log.error(f"Error creating slideshow videos: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@video_bp.route('/status/<job_id>', methods=['GET'])
def get_video_status(job_id):
    """Get status of a video generation job."""
    job = get_video_job(job_id)
    if not job:
        return jsonify({'error': 'Job not found'}), 404

    return jsonify({
        'job_id': job_id,
        'status': job['status'],
        'job_type': job['job_type'],
        'drive_url': job.get('drive_url'),
        'error_message': job.get('error_message'),
        'created_at': job.get('created_at'),
        'completed_at': job.get('completed_at')
    })


@video_bp.route('/jobs', methods=['GET'])
def list_videos():
    """List all video jobs with optional filtering."""
    try:
        limit = request.args.get('limit', 20, type=int)
        offset = request.args.get('offset', 0, type=int)
        status = request.args.get('status')

        jobs = list_video_jobs(status=status, limit=limit, offset=offset)
        total = get_video_jobs_count(status=status)

        return jsonify({
            'jobs': jobs,
            'total': total,
            'limit': limit,
            'offset': offset
        })

    except Exception as e:
        logger.error(f"Error listing video jobs: {e}")
        return jsonify({'error': str(e)}), 500


@video_bp.route('/queue/status', methods=['GET'])
def get_queue_status():
    """Get video queue status."""
    pending = get_video_jobs_count(status='pending')
    processing = get_video_jobs_count(status='processing')
    completed = get_video_jobs_count(status='completed')
    failed = get_video_jobs_count(status='failed')

    return jsonify({
        'queue': {
            'pending': pending,
            'processing': processing,
            'completed': completed,
            'failed': failed
        },
        'worker_running': _worker_running
    })


# Start worker when blueprint is registered
@video_bp.record_once
def on_register(state):
    """Called when blueprint is registered with app."""
    start_video_worker()
