"""
Video Copy Routes
Flask API endpoints for slideshow to video conversion.
"""
import os
import re
import uuid
from datetime import datetime, timedelta
from flask import Blueprint, request, jsonify
from werkzeug.utils import secure_filename

from logging_config import get_logger

logger = get_logger('tiktok_copy_routes')

# Create blueprint
tiktok_copy_bp = Blueprint('tiktok_copy', __name__, url_prefix='/api/video-copy')

# Import database functions
from database import (
    create_tiktok_copy_batch,
    create_tiktok_copy_job,
    get_tiktok_copy_batch,
    get_tiktok_copy_jobs,
    update_tiktok_copy_batch,
)

# Temp directory for uploaded product photos
UPLOAD_DIR = os.path.join(os.path.dirname(__file__), 'temp', 'tiktok_copy_uploads')


def validate_tiktok_url(url: str) -> bool:
    """
    Validate that a URL is a valid TikTok slideshow/video URL.

    Supports:
    - https://www.tiktok.com/@username/photo/123456
    - https://www.tiktok.com/t/ABC123/
    - https://vm.tiktok.com/ABC123/
    """
    patterns = [
        r'https?://(www\.)?tiktok\.com/@[\w.-]+/(photo|video)/\d+',
        r'https?://(www\.)?tiktok\.com/t/\w+',
        r'https?://vm\.tiktok\.com/\w+',
    ]

    for pattern in patterns:
        if re.match(pattern, url.strip()):
            return True
    return False


def extract_links(text: str) -> list[str]:
    """
    Extract and validate TikTok links from text input.

    Args:
        text: Raw text input (one link per line)

    Returns:
        List of valid TikTok URLs
    """
    links = []
    for line in text.strip().split('\n'):
        url = line.strip()
        if url and validate_tiktok_url(url):
            links.append(url)
    return links


@tiktok_copy_bp.route('/product-photos', methods=['GET'])
def list_product_photos_public():
    """
    List available product photos by category (public, no auth required).
    Used by Video Copy page to show photo selection grid.
    """
    from admin_routes import PRODUCT_CATEGORIES, _ensure_product_photo_dirs, _get_category_photos

    try:
        _ensure_product_photo_dirs()
        categories = []
        for slug, name in PRODUCT_CATEGORIES.items():
            photos = _get_category_photos(slug)
            categories.append({
                'slug': slug,
                'name': name,
                'photos': photos,
                'count': len(photos),
            })
        return jsonify({'categories': categories})
    except Exception as e:
        logger.error(f"Failed to list product photos: {e}")
        return jsonify({'error': str(e)}), 500


@tiktok_copy_bp.route('/convert', methods=['POST'])
def convert_tiktok():
    """
    Submit TikTok links for conversion to video.

    Supports two modes:
    1. Auto-replace (new): AI detects product slide and replaces it
    2. No-replacement: Just convert slideshow to video as-is

    Request (JSON):
        {
            "links": ["url1", "url2", ...],
            "mode": "auto-replace" | "no-replacement",
            "product_photo_path": "/path/to/photo.jpg"  (required for auto-replace)
        }

    Legacy (multipart/form-data) is still supported for backwards compatibility.
    """
    import json

    logger.info("Received convert request")

    # Detect request format: JSON (new) or multipart (legacy)
    if request.is_json:
        return _handle_json_convert(request.get_json())
    else:
        return _handle_legacy_convert()


def _handle_json_convert(data):
    """Handle new simplified JSON convert request."""
    links = data.get('links', [])
    mode = data.get('mode', 'auto-replace')
    product_photo_path = data.get('product_photo_path')

    if mode not in ('auto-replace', 'no-replacement'):
        return jsonify({'error': 'Invalid mode. Use "auto-replace" or "no-replacement"'}), 400

    if mode == 'auto-replace' and not product_photo_path:
        return jsonify({'error': 'Product photo required for auto-replace mode'}), 400

    # Validate product photo exists
    if product_photo_path and not os.path.exists(product_photo_path):
        return jsonify({'error': 'Product photo not found on server'}), 400

    if not links or not isinstance(links, list):
        return jsonify({'error': 'No links provided'}), 400

    # Validate URLs
    valid_links = []
    for url in links:
        url = url.strip()
        if url and validate_tiktok_url(url):
            valid_links.append(url)

    if not valid_links:
        return jsonify({
            'error': 'No valid links provided',
            'hint': 'Check your URLs are valid slideshow links'
        }), 400

    logger.info(f"Valid links: {len(valid_links)}, mode: {mode}")

    # Create batch with mode
    batch_id = create_tiktok_copy_batch(
        mode=mode,
        product_photo_path=product_photo_path
    )
    logger.info(f"Created batch: {batch_id} (mode={mode})")

    # Create jobs — same photo for all links, detection happens at processing time
    jobs = []
    for url in valid_links:
        job_id = create_tiktok_copy_job(
            batch_id=batch_id,
            tiktok_url=url,
            product_photo_path=product_photo_path if mode == 'auto-replace' else None
        )
        jobs.append({
            'id': job_id,
            'url': url,
            'status': 'pending',
            'mode': mode
        })

    logger.info(f"Created {len(jobs)} jobs for batch {batch_id}")

    # Queue batch for processing
    try:
        from tasks import process_tiktok_copy_batch
        process_tiktok_copy_batch.delay(batch_id)
        logger.info(f"Queued batch {batch_id} for processing")
    except ImportError as e:
        logger.warning(f"Celery task import failed: {e}")
    except Exception as e:
        logger.warning(f"Failed to queue batch: {e}")

    return jsonify({
        'batch_id': batch_id,
        'jobs': jobs,
        'mode': mode
    })


def _handle_legacy_convert():
    """Handle legacy multipart/form-data convert request (backwards compatible)."""
    import json

    links_config_str = request.form.get('links_config', '')

    if not links_config_str:
        return jsonify({
            'error': 'No links configuration provided',
            'hint': 'Submit links_config JSON array'
        }), 400

    try:
        links_config = json.loads(links_config_str)
    except json.JSONDecodeError as e:
        return jsonify({
            'error': 'Invalid JSON in links_config',
            'hint': str(e)
        }), 400

    if not links_config or not isinstance(links_config, list):
        return jsonify({
            'error': 'No links provided',
            'hint': 'links_config must be a non-empty array'
        }), 400

    # Validate all URLs first
    valid_configs = []
    for idx, cfg in enumerate(links_config):
        url = cfg.get('url', '').strip()
        if not url:
            continue
        if not validate_tiktok_url(url):
            return jsonify({
                'error': f'Invalid URL at index {idx}',
                'url': url
            }), 400
        valid_configs.append(cfg)

    if not valid_configs:
        return jsonify({
            'error': 'No valid links provided',
            'hint': 'Check your URLs are valid slideshow links'
        }), 400

    logger.info(f"Valid links (legacy): {len(valid_configs)}")

    # Save uploaded photos with index mapping
    photo_paths = {}
    for key in request.files:
        if key.startswith('product_photo_'):
            try:
                idx = int(key.split('_')[-1])
            except ValueError:
                continue

            file = request.files[key]
            if file and file.filename:
                filename = secure_filename(file.filename)
                unique_filename = f"{uuid.uuid4().hex[:8]}_{filename}"
                os.makedirs(UPLOAD_DIR, exist_ok=True)
                photo_path = os.path.join(UPLOAD_DIR, unique_filename)
                file.save(photo_path)
                photo_paths[idx] = photo_path
                logger.info(f"Saved product photo {idx}: {photo_path}")

    # Create batch in database (legacy mode — no auto-detect)
    batch_id = create_tiktok_copy_batch(mode='manual')
    logger.info(f"Created batch: {batch_id} (legacy/manual)")

    # Create jobs with per-link settings
    jobs = []
    for cfg in valid_configs:
        url = cfg.get('url', '').strip()
        replace_slide = cfg.get('replace_slide')
        photo_index = cfg.get('photo_index')

        # Validate replace_slide
        if replace_slide is not None:
            try:
                replace_slide = int(replace_slide)
                if replace_slide < 1:
                    return jsonify({
                        'error': 'Invalid slide number',
                        'hint': 'Slide number must be a positive integer',
                        'url': url
                    }), 400
            except (ValueError, TypeError):
                return jsonify({
                    'error': 'Invalid slide number',
                    'hint': 'Slide number must be a positive integer',
                    'url': url
                }), 400

        # Get photo path for this job
        product_photo_path = None
        if photo_index is not None and photo_index in photo_paths:
            product_photo_path = photo_paths[photo_index]

        # Validate: if replace_slide is set, need product photo
        if replace_slide and not product_photo_path:
            return jsonify({
                'error': 'Product photo required for slide replacement',
                'hint': 'Upload a product photo for this link',
                'url': url
            }), 400

        # Create job with per-link settings
        job_id = create_tiktok_copy_job(
            batch_id=batch_id,
            tiktok_url=url,
            replace_slide=replace_slide,
            product_photo_path=product_photo_path
        )
        jobs.append({
            'id': job_id,
            'url': url,
            'status': 'pending',
            'replace_slide': replace_slide,
            'has_photo': bool(product_photo_path)
        })

    # Update batch total
    update_tiktok_copy_batch(batch_id)

    logger.info(f"Created {len(jobs)} jobs for batch {batch_id}")

    # Queue jobs for processing (Celery task)
    try:
        from tasks import process_tiktok_copy_batch
        process_tiktok_copy_batch.delay(batch_id)
        logger.info(f"Queued batch {batch_id} for processing")
    except ImportError as e:
        logger.warning(f"Celery task import failed (will use sync): {e}")
    except Exception as e:
        logger.warning(f"Failed to queue batch (will process sync): {e}")

    return jsonify({
        'batch_id': batch_id,
        'jobs': jobs,
        'total_photos': len(photo_paths)
    })


@tiktok_copy_bp.route('/status/<batch_id>', methods=['GET'])
def get_status(batch_id: str):
    """
    Get status of a batch conversion.

    Response:
        {
            "batch_id": "xxx",
            "status": "processing",
            "total_jobs": 3,
            "completed_jobs": 1,
            "failed_jobs": 0,
            "drive_folder_url": "...",
            "jobs": [...]
        }
    """
    batch = get_tiktok_copy_batch(batch_id)
    if not batch:
        return jsonify({'error': 'Batch not found'}), 404

    jobs = get_tiktok_copy_jobs(batch_id)

    # Auto-fail stale jobs: if a job has been pending/processing for >10 min,
    # it's a zombie (Celery task crashed without updating DB). Mark it failed.
    stale_cutoff = datetime.utcnow() - timedelta(minutes=10)
    for j in jobs:
        if j['status'] in ('pending', 'processing'):
            created = j.get('created_at')
            if created:
                if isinstance(created, str):
                    try:
                        created = datetime.fromisoformat(created)
                    except ValueError:
                        continue
                if created < stale_cutoff:
                    from database import update_tiktok_copy_job
                    update_tiktok_copy_job(
                        j['id'], 'failed',
                        error_message=f"Auto-timeout: job stuck in '{j['status']}' for >10 min"
                    )
                    j['status'] = 'failed'
                    j['error_message'] = f"Auto-timeout: job stuck in '{j['status']}' for >10 min"
                    logger.warning(f"Auto-failed stale job {j['id'][:8]} (was '{j['status']}' since {created})")

    # Calculate status from jobs
    completed = sum(1 for j in jobs if j['status'] == 'completed')
    failed = sum(1 for j in jobs if j['status'] == 'failed')
    total = len(jobs)

    # Determine overall status
    if failed == total:
        status = 'failed'
    elif completed == total:
        status = 'completed'
    elif completed > 0 or failed > 0:
        status = 'processing'
    else:
        status = 'pending'

    return jsonify({
        'batch_id': batch_id,
        'status': status,
        'mode': batch.get('mode', 'manual'),
        'total_jobs': total,
        'completed_jobs': completed,
        'failed_jobs': failed,
        'drive_folder_url': batch.get('drive_folder_url'),
        'jobs': [
            {
                'id': j['id'],
                'url': j['tiktok_url'],
                'status': j['status'],
                'replace_slide': j.get('replace_slide'),
                'product_slide_detected': j.get('product_slide_detected'),
                'detection_skipped': bool(j.get('detection_skipped')),
                'drive_url': j.get('drive_url'),
                'error': j.get('error_message')
            }
            for j in jobs
        ]
    })


@tiktok_copy_bp.route('/validate', methods=['POST'])
def validate_links():
    """
    Validate TikTok links without processing.

    Request:
        { "links": "url1\\nurl2\\n..." }

    Response:
        { "valid": ["url1"], "invalid": ["url2"] }
    """
    data = request.get_json() or {}
    links_text = data.get('links', '')

    valid = []
    invalid = []

    for line in links_text.strip().split('\n'):
        url = line.strip()
        if not url:
            continue
        if validate_tiktok_url(url):
            valid.append(url)
        else:
            invalid.append(url)

    return jsonify({
        'valid': valid,
        'invalid': invalid,
        'valid_count': len(valid),
        'invalid_count': len(invalid)
    })
