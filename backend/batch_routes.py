"""
Batch Processing API Routes
Endpoints for creating and managing batch jobs
"""
import os
import re
import uuid
from flask import Blueprint, request, jsonify
from werkzeug.utils import secure_filename

from logging_config import get_logger, get_request_logger
from database import (
    create_batch, get_batch, get_batch_status, get_batch_links,
    create_batch_link, update_batch_status, cancel_pending_tasks,
    create_job, update_job_status
)
from tasks import process_batch, retry_failed_links
import json

logger = get_logger('batch_routes')

batch_bp = Blueprint('batch', __name__, url_prefix='/api/batch')

# Upload folder for batch product images
BATCH_UPLOAD_FOLDER = 'temp/batch_uploads'
os.makedirs(BATCH_UPLOAD_FOLDER, exist_ok=True)

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
MAX_LINKS_PER_BATCH = 100


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def validate_tiktok_url(url: str) -> tuple[bool, str]:
    """
    Validate a TikTok URL.

    Returns:
        (is_valid, error_message)
    """
    if not url:
        return False, "Empty URL"

    # Basic URL format check
    tiktok_patterns = [
        r'^https?://(www\.)?tiktok\.com/@[\w.-]+/video/\d+',
        r'^https?://(www\.)?tiktok\.com/@[\w.-]+/photo/\d+',
        r'^https?://vm\.tiktok\.com/[\w]+',
        r'^https?://(www\.)?tiktok\.com/t/[\w]+',
    ]

    for pattern in tiktok_patterns:
        if re.match(pattern, url):
            return True, ""

    return False, "Invalid TikTok URL format"


def parse_links(text: str) -> list[str]:
    """
    Parse links from text input (supports newlines, commas, tabs).

    Returns:
        List of URLs
    """
    # Split by newlines, commas, tabs, spaces
    links = re.split(r'[\n\r,\t]+', text)
    # Clean up whitespace and filter empty
    links = [link.strip() for link in links if link.strip()]
    return links


@batch_bp.route('', methods=['POST'])
def create_batch_job():
    """
    Create a new batch processing job.

    Expected form data:
    - links: Text with TikTok URLs (newline/comma separated)
    - photo_variations: Number of photo variations (1-10)
    - text_variations: Number of text variations (1-10)
    - products: JSON mapping of link index to {description, photo_field_name}

    For each link, there should be a file upload field named 'product_photo_{index}'
    """
    request_id = str(uuid.uuid4())[:8]
    log = get_request_logger('batch', request_id)

    try:
        # Parse links from text input
        links_text = request.form.get('links', '')
        links = parse_links(links_text)

        if not links:
            return jsonify({'error': 'No links provided'}), 400

        if len(links) > MAX_LINKS_PER_BATCH:
            return jsonify({
                'error': f'Too many links. Maximum is {MAX_LINKS_PER_BATCH}, got {len(links)}'
            }), 400

        log.info(f"Creating batch with {len(links)} links")

        # Validate all links
        valid_links = []
        invalid_links = []

        for i, link in enumerate(links):
            is_valid, error = validate_tiktok_url(link)
            if is_valid:
                valid_links.append((i, link))
            else:
                invalid_links.append({'index': i, 'url': link, 'error': error})

        if not valid_links:
            return jsonify({
                'error': 'No valid links found',
                'invalid_links': invalid_links
            }), 400

        # Get variation settings - detailed Photo × Text per slide type
        # Support both old format (photo_variations/text_variations) and new detailed format
        hook_photo_var = int(request.form.get('hook_photo_var', request.form.get('photo_variations', 1)))
        hook_text_var = int(request.form.get('hook_text_var', request.form.get('text_variations', 1)))
        body_photo_var = int(request.form.get('body_photo_var', request.form.get('photo_variations', 1)))
        body_text_var = int(request.form.get('body_text_var', request.form.get('text_variations', 1)))
        product_text_var = int(request.form.get('product_text_var', request.form.get('text_variations', 1)))

        # Clamp to valid range (1-5)
        hook_photo_var = max(1, min(5, hook_photo_var))
        hook_text_var = max(1, min(5, hook_text_var))
        body_photo_var = max(1, min(5, body_photo_var))
        body_text_var = max(1, min(5, body_text_var))
        product_text_var = max(1, min(5, product_text_var))

        # Legacy support: keep photo_variations/text_variations for existing batch table
        photo_variations = max(hook_photo_var, body_photo_var)
        text_variations = max(hook_text_var, body_text_var, product_text_var)

        # Get video generation flag
        generate_video = request.form.get('generate_video', 'false').lower() in ('true', '1', 'yes')

        # Get text preset (default: gemini)
        preset_id = request.form.get('preset_id', 'gemini')

        # Build variations config
        variations_config = {
            'hook_photo_var': hook_photo_var,
            'hook_text_var': hook_text_var,
            'body_photo_var': body_photo_var,
            'body_text_var': body_text_var,
            'product_text_var': product_text_var,
            'generate_video': generate_video,
            'preset_id': preset_id
        }

        log.debug(f"Variations: hook={hook_photo_var}x{hook_text_var}, body={body_photo_var}x{body_text_var}, product=x{product_text_var}, video={generate_video}, preset={preset_id}")

        # Create job entry in unified jobs table first (for Job History)
        job_id = create_job(
            job_type='batch',
            total_links=len(valid_links),
            folder_name=f"Batch_{str(uuid.uuid4())[:8]}",
            variations_config=json.dumps(variations_config)
        )

        # Create batch in database (legacy table) with link to job
        batch_id = create_batch(
            total_links=len(valid_links),
            photo_variations=photo_variations,
            text_variations=text_variations,
            variations_config=json.dumps(variations_config),
            job_id=job_id
        )

        # Update job folder_name with batch_id
        from database import get_db
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'UPDATE jobs SET folder_name = ? WHERE id = ?',
                (f"Batch_{batch_id[:8]}", job_id)
            )

        log.info(f"Created batch: {batch_id}, job: {job_id}")

        # Create upload directory for this batch
        batch_upload_dir = os.path.join(BATCH_UPLOAD_FOLDER, batch_id)
        os.makedirs(batch_upload_dir, exist_ok=True)

        # Process each link with its product info
        created_links = []

        for i, link_url in valid_links:
            # Get product description
            description_key = f'product_description_{i}'
            product_description = request.form.get(description_key, '')

            # Handle product photo upload
            photo_key = f'product_photo_{i}'
            product_photo_path = None

            if photo_key in request.files:
                photo = request.files[photo_key]
                if photo and photo.filename and allowed_file(photo.filename):
                    filename = secure_filename(photo.filename)
                    product_photo_path = os.path.join(batch_upload_dir, f'link_{i}_{filename}')
                    photo.save(product_photo_path)
                    log.debug(f"Saved product photo for link {i}: {filename}")

            # Check for "apply to all" case - use first photo for all
            if not product_photo_path and i > 0:
                # Look for photo from link 0
                first_photo_key = 'product_photo_0'
                if first_photo_key in request.files:
                    # Check if we already saved link 0's photo
                    existing_photos = [f for f in os.listdir(batch_upload_dir) if f.startswith('link_0_')]
                    if existing_photos:
                        product_photo_path = os.path.join(batch_upload_dir, existing_photos[0])

            # Validate required fields
            if not product_photo_path:
                log.warning(f"Link {i} missing product photo, skipping")
                invalid_links.append({
                    'index': i,
                    'url': link_url,
                    'error': 'Missing product photo'
                })
                continue

            # Create batch link in database
            link_id = create_batch_link(
                batch_id=batch_id,
                link_index=i,
                link_url=link_url,
                product_photo_path=product_photo_path,
                product_description=product_description
            )

            created_links.append({
                'link_id': link_id,
                'index': i,
                'url': link_url,
                'has_photo': bool(product_photo_path),
                'has_description': bool(product_description)
            })

        if not created_links:
            return jsonify({
                'error': 'No links could be created (missing product photos)',
                'invalid_links': invalid_links
            }), 400

        log.info(f"Created {len(created_links)} links for batch {batch_id[:8]}")

        # Dispatch batch processing task
        task = process_batch.delay(batch_id)
        log.info(f"Dispatched batch task: {task.id}")

        return jsonify({
            'status': 'created',
            'batch_id': batch_id,
            'job_id': job_id,
            'total_links': len(created_links),
            'invalid_links': invalid_links if invalid_links else None,
            'variations': {
                'hook_photo_var': hook_photo_var,
                'hook_text_var': hook_text_var,
                'body_photo_var': body_photo_var,
                'body_text_var': body_text_var,
                'product_text_var': product_text_var
            },
            'generate_video': generate_video,
            'message': f'Batch created with {len(created_links)} links. Processing started.'
        })

    except ValueError as e:
        log.error(f"Invalid input: {e}")
        return jsonify({'error': f'Invalid input: {e}'}), 400
    except Exception as e:
        log.error(f"Failed to create batch: {e}", exc_info=True)
        return jsonify({'error': f'Server error: {e}'}), 500


@batch_bp.route('/validate', methods=['POST'])
def validate_links():
    """
    Validate TikTok links without creating a batch.
    Used for real-time UI validation.

    Expected JSON:
    - links: Array of TikTok URLs or text with URLs

    Returns validation results for each link.
    """
    try:
        data = request.get_json() or {}
        links_input = data.get('links', [])

        # Handle both array and text input
        if isinstance(links_input, str):
            links = parse_links(links_input)
        else:
            links = links_input

        if not links:
            return jsonify({
                'valid_count': 0,
                'invalid_count': 0,
                'results': []
            })

        results = []
        valid_count = 0
        invalid_count = 0

        for i, link in enumerate(links):
            is_valid, error = validate_tiktok_url(link)
            results.append({
                'index': i,
                'url': link,
                'valid': is_valid,
                'error': error if not is_valid else None
            })
            if is_valid:
                valid_count += 1
            else:
                invalid_count += 1

        return jsonify({
            'valid_count': valid_count,
            'invalid_count': invalid_count,
            'results': results
        })

    except Exception as e:
        logger.error(f"Validation error: {e}", exc_info=True)
        return jsonify({'error': f'Validation error: {e}'}), 500


@batch_bp.route('/<batch_id>/status', methods=['GET'])
def get_batch_job_status(batch_id: str):
    """
    Get detailed status of a batch job.

    Returns:
    - Batch info (status, created_at, etc.)
    - Link counts by status
    - Variation counts by status
    - Drive folder URL when available
    """
    try:
        status = get_batch_status(batch_id)

        if not status:
            return jsonify({'error': 'Batch not found'}), 404

        # Calculate progress percentage
        total_links = status['links']['total']
        completed_links = status['links']['completed']
        failed_links = status['links']['failed']
        processing_links = status['links']['processing']

        if total_links > 0:
            progress = int((completed_links + failed_links) / total_links * 100)
        else:
            progress = 0

        return jsonify({
            'batch_id': batch_id,
            'status': status['status'],
            'progress': progress,
            'created_at': status['created_at'],
            'started_at': status['started_at'],
            'completed_at': status['completed_at'],
            'drive_folder_url': status['drive_folder_url'],
            'error_message': status['error_message'],
            'links': {
                'total': total_links,
                'completed': completed_links,
                'failed': failed_links,
                'processing': processing_links,
                'pending': status['links']['pending']
            },
            'variations': status['variations'],
            'settings': {
                'photo_variations': status['photo_variations'],
                'text_variations': status['text_variations']
            }
        })

    except Exception as e:
        logger.error(f"Failed to get batch status: {e}", exc_info=True)
        return jsonify({'error': f'Server error: {e}'}), 500


@batch_bp.route('/<batch_id>/links', methods=['GET'])
def get_batch_links_list(batch_id: str):
    """
    Get list of all links in a batch with their status.
    """
    try:
        batch = get_batch(batch_id)
        if not batch:
            return jsonify({'error': 'Batch not found'}), 404

        links = get_batch_links(batch_id)

        return jsonify({
            'batch_id': batch_id,
            'links': [{
                'link_id': link['id'],
                'index': link['link_index'],
                'url': link['link_url'],
                'status': link['status'],
                'error_message': link['error_message'],
                'drive_folder_url': link['drive_folder_url'],
                'created_at': link['created_at'],
                'completed_at': link['completed_at']
            } for link in links]
        })

    except Exception as e:
        logger.error(f"Failed to get batch links: {e}", exc_info=True)
        return jsonify({'error': f'Server error: {e}'}), 500


@batch_bp.route('/<batch_id>/cancel', methods=['POST'])
def cancel_batch_job(batch_id: str):
    """
    Cancel a batch job. Marks pending tasks as cancelled.
    In-progress tasks will complete.
    """
    try:
        batch = get_batch(batch_id)
        if not batch:
            return jsonify({'error': 'Batch not found'}), 404

        if batch['status'] in ('completed', 'failed', 'cancelled'):
            return jsonify({
                'error': f'Cannot cancel batch with status: {batch["status"]}'
            }), 400

        # Cancel pending tasks in database
        cancel_pending_tasks(batch_id)

        # Update batch status
        update_batch_status(batch_id, 'cancelled')

        logger.info(f"Batch {batch_id[:8]} cancelled")

        return jsonify({
            'status': 'cancelled',
            'batch_id': batch_id,
            'message': 'Batch cancelled. Pending tasks will not be processed.'
        })

    except Exception as e:
        logger.error(f"Failed to cancel batch: {e}", exc_info=True)
        return jsonify({'error': f'Server error: {e}'}), 500


@batch_bp.route('/<batch_id>/retry-failed', methods=['POST'])
def retry_failed_batch_links(batch_id: str):
    """
    Retry all failed links in a batch.
    """
    try:
        batch = get_batch(batch_id)
        if not batch:
            return jsonify({'error': 'Batch not found'}), 404

        # Get count of failed links
        failed_links = get_batch_links(batch_id, status='failed')

        if not failed_links:
            return jsonify({
                'status': 'no_retry_needed',
                'message': 'No failed links to retry'
            })

        # Dispatch retry task
        task = retry_failed_links.delay(batch_id)

        logger.info(f"Batch {batch_id[:8]} retry dispatched: {len(failed_links)} links")

        return jsonify({
            'status': 'retry_started',
            'batch_id': batch_id,
            'retry_count': len(failed_links),
            'task_id': task.id,
            'message': f'Retrying {len(failed_links)} failed links'
        })

    except Exception as e:
        logger.error(f"Failed to retry batch: {e}", exc_info=True)
        return jsonify({'error': f'Server error: {e}'}), 500


@batch_bp.route('/list', methods=['GET'])
def list_batches():
    """
    List recent batches (for debugging/admin).
    """
    try:
        from database import get_db

        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT id, status, total_links, created_at, completed_at, drive_folder_url
                FROM batches
                ORDER BY created_at DESC
                LIMIT 20
            ''')
            batches = [dict(row) for row in cursor.fetchall()]

        return jsonify({
            'batches': batches
        })

    except Exception as e:
        logger.error(f"Failed to list batches: {e}", exc_info=True)
        return jsonify({'error': f'Server error: {e}'}), 500
