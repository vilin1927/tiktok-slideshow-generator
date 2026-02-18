"""
Admin API Routes
Endpoints for managing API keys with password protection
"""
import os
import re
import secrets
import subprocess
import time
from functools import wraps
from flask import Blueprint, request, jsonify
from dotenv import load_dotenv, set_key, find_dotenv
from werkzeug.utils import secure_filename

from logging_config import get_logger
from database import (
    list_video_jobs, get_video_jobs_count,
    list_tiktok_copy_batches, get_tiktok_copy_batches_count,
    list_ig_jobs, get_ig_jobs_count, get_ig_videos_by_job, get_ig_job, delete_ig_job
)

logger = get_logger('admin')

admin_bp = Blueprint('admin', __name__, url_prefix='/api/admin')

# Simple session store (in production, use Redis or similar)
# Format: {token: {'expires': timestamp}}
active_sessions = {}
SESSION_DURATION = 3600  # 1 hour


def cleanup_expired_sessions():
    """Remove expired sessions"""
    now = time.time()
    expired = [token for token, data in active_sessions.items() if data['expires'] < now]
    for token in expired:
        del active_sessions[token]


def require_auth(f):
    """Decorator to require valid admin session"""
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get('X-Admin-Token')
        if not token:
            return jsonify({'error': 'No auth token provided'}), 401

        cleanup_expired_sessions()

        if token not in active_sessions:
            return jsonify({'error': 'Invalid or expired token'}), 401

        # Extend session on activity
        active_sessions[token]['expires'] = time.time() + SESSION_DURATION

        return f(*args, **kwargs)
    return decorated


def mask_key(key: str) -> str:
    """Mask API key for display (show first 4 and last 4 chars)"""
    if not key or len(key) < 12:
        return '****'
    return f"{key[:4]}...{key[-4:]}"


def restart_worker_services() -> bool:
    """Restart image-queue-processor and celery-worker to pick up new env vars"""
    try:
        # Only restart on Linux (production server)
        if os.name != 'posix' or not os.path.exists('/etc/systemd/system'):
            logger.info("Not on systemd server, skipping service restart")
            return True

        services = ['image-queue-processor', 'celery-worker']
        for service in services:
            result = subprocess.run(
                ['systemctl', 'restart', service],
                capture_output=True,
                text=True,
                timeout=30
            )
            if result.returncode != 0:
                logger.error(f"Failed to restart {service}: {result.stderr}")
            else:
                logger.info(f"Restarted {service}")

        return True
    except Exception as e:
        logger.error(f"Failed to restart services: {e}")
        return False


def update_env_file(key: str, value: str) -> bool:
    """Update a key in the .env file"""
    try:
        # Find the .env file
        dotenv_path = find_dotenv()
        if not dotenv_path:
            # Try common locations
            possible_paths = [
                os.path.join(os.path.dirname(__file__), '.env'),
                '/root/tiktok-slideshow-generator/Desktop/clients/tiktok/backend/.env'
            ]
            for path in possible_paths:
                if os.path.exists(path):
                    dotenv_path = path
                    break

        if not dotenv_path or not os.path.exists(dotenv_path):
            logger.error("Could not find .env file")
            return False

        # Update the key in .env file
        set_key(dotenv_path, key, value)

        # Reload environment
        load_dotenv(dotenv_path, override=True)

        # Also update os.environ directly
        os.environ[key] = value

        logger.info(f"Updated {key} in .env file")
        return True

    except Exception as e:
        logger.error(f"Failed to update .env: {e}")
        return False


@admin_bp.route('/login', methods=['POST'])
def admin_login():
    """
    Authenticate with admin password.

    Expected JSON:
    - password: Admin password

    Returns:
    - token: Session token (valid for 1 hour)
    """
    try:
        data = request.get_json() or {}
        password = data.get('password', '')

        admin_password = os.getenv('ADMIN_PASSWORD')

        if not admin_password:
            logger.error("ADMIN_PASSWORD not configured")
            return jsonify({'error': 'Admin not configured'}), 500

        if password != admin_password:
            logger.warning("Failed admin login attempt")
            return jsonify({'error': 'Invalid password'}), 401

        # Generate session token
        token = secrets.token_urlsafe(32)
        active_sessions[token] = {
            'expires': time.time() + SESSION_DURATION
        }

        logger.info("Admin login successful")

        return jsonify({
            'status': 'authenticated',
            'token': token,
            'expires_in': SESSION_DURATION
        })

    except Exception as e:
        logger.error(f"Login error: {e}")
        return jsonify({'error': 'Server error'}), 500


@admin_bp.route('/logout', methods=['POST'])
@require_auth
def admin_logout():
    """Invalidate current session"""
    token = request.headers.get('X-Admin-Token')
    if token in active_sessions:
        del active_sessions[token]
    return jsonify({'status': 'logged_out'})


@admin_bp.route('/keys', methods=['GET'])
@require_auth
def get_api_keys():
    """
    Get current API keys (masked for security).
    """
    try:
        gemini_key = os.getenv('GEMINI_API_KEY', '')
        rapidapi_key = os.getenv('RAPIDAPI_KEY', '')

        return jsonify({
            'keys': {
                'GEMINI_API_KEY': {
                    'masked': mask_key(gemini_key),
                    'is_set': bool(gemini_key)
                },
                'RAPIDAPI_KEY': {
                    'masked': mask_key(rapidapi_key),
                    'is_set': bool(rapidapi_key)
                }
            }
        })

    except Exception as e:
        logger.error(f"Failed to get keys: {e}")
        return jsonify({'error': 'Server error'}), 500


@admin_bp.route('/keys', methods=['PUT'])
@require_auth
def update_api_keys():
    """
    Update API keys.

    Expected JSON:
    - GEMINI_API_KEY: New Gemini key (optional)
    - RAPIDAPI_KEY: New RapidAPI key (optional)

    Only provided keys will be updated.
    """
    try:
        data = request.get_json() or {}

        updated = []
        errors = []

        # Update Gemini key if provided
        if 'GEMINI_API_KEY' in data:
            new_key = data['GEMINI_API_KEY'].strip()
            if new_key:
                if update_env_file('GEMINI_API_KEY', new_key):
                    updated.append('GEMINI_API_KEY')
                    logger.info("Gemini API key updated")
                else:
                    errors.append('GEMINI_API_KEY')

        # Update RapidAPI key if provided
        if 'RAPIDAPI_KEY' in data:
            new_key = data['RAPIDAPI_KEY'].strip()
            if new_key:
                if update_env_file('RAPIDAPI_KEY', new_key):
                    updated.append('RAPIDAPI_KEY')
                    logger.info("RapidAPI key updated")
                else:
                    errors.append('RAPIDAPI_KEY')

        if errors:
            return jsonify({
                'status': 'partial',
                'updated': updated,
                'errors': errors,
                'message': f'Failed to update: {", ".join(errors)}'
            }), 500

        if not updated:
            return jsonify({
                'status': 'no_changes',
                'message': 'No keys provided to update'
            })

        # Restart worker services to pick up new API keys
        services_restarted = restart_worker_services()

        return jsonify({
            'status': 'updated',
            'updated': updated,
            'services_restarted': services_restarted,
            'message': f'Updated: {", ".join(updated)}. Workers restarted: {services_restarted}'
        })

    except Exception as e:
        logger.error(f"Failed to update keys: {e}")
        return jsonify({'error': f'Server error: {e}'}), 500


@admin_bp.route('/verify', methods=['GET'])
@require_auth
def verify_session():
    """Verify current session is valid"""
    return jsonify({'status': 'valid'})


@admin_bp.route('/video-jobs', methods=['GET'])
@require_auth
def list_video_jobs_api():
    """
    List video jobs for admin panel.

    Query params:
    - limit: Max jobs to return (default 50)
    - offset: Pagination offset (default 0)
    - status: Filter by status (optional)
    """
    try:
        limit = request.args.get('limit', 50, type=int)
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
        logger.error(f"Failed to list video jobs: {e}")
        return jsonify({'error': str(e)}), 500


@admin_bp.route('/video-copy-jobs', methods=['GET'])
@require_auth
def list_video_copy_jobs_api():
    """
    List Video Copy batches with their jobs for admin panel.

    Query params:
    - limit: Max batches to return (default 50)
    - offset: Pagination offset (default 0)

    Response:
    {
        "batches": [
            {
                "id": "xxx",
                "status": "completed",
                "total_jobs": 3,
                "completed_jobs": 2,
                "failed_jobs": 1,
                "drive_folder_url": "...",
                "created_at": "...",
                "jobs": [
                    {"id": "...", "tiktok_url": "...", "status": "completed", ...}
                ]
            }
        ],
        "total": 10
    }
    """
    try:
        limit = request.args.get('limit', 50, type=int)
        offset = request.args.get('offset', 0, type=int)

        batches = list_tiktok_copy_batches(limit=limit, offset=offset)
        total = get_tiktok_copy_batches_count()

        return jsonify({
            'batches': batches,
            'total': total,
            'limit': limit,
            'offset': offset
        })

    except Exception as e:
        logger.error(f"Failed to list Video Copy batches: {e}")
        return jsonify({'error': str(e)}), 500


# ============ Instagram Reel Jobs ============

@admin_bp.route('/ig-reel-jobs', methods=['GET'])
@require_auth
def list_ig_reel_jobs_api():
    """List Instagram Reel generation jobs for admin panel."""
    try:
        limit = request.args.get('limit', 50, type=int)
        offset = request.args.get('offset', 0, type=int)

        jobs = list_ig_jobs(limit=limit, offset=offset)

        # Attach video details to each job
        for job in jobs:
            job['videos'] = get_ig_videos_by_job(job['id'])

        return jsonify({
            'jobs': jobs,
            'total': get_ig_jobs_count(),
            'limit': limit,
            'offset': offset
        })

    except Exception as e:
        logger.error(f"Failed to list IG Reel jobs: {e}")
        return jsonify({'error': str(e)}), 500


@admin_bp.route('/ig-reel-jobs/<job_id>', methods=['DELETE'])
@require_auth
def delete_ig_reel_job(job_id):
    """Delete an IG Reel job and revoke its Celery task."""
    try:
        job = get_ig_job(job_id)
        if not job:
            return jsonify({'error': 'Job not found'}), 404

        # Revoke Celery task if running
        task_ids = [job['celery_task_id']] if job.get('celery_task_id') else []
        revoked = 0
        if task_ids:
            try:
                from celery_utils import revoke_tasks
                revoked = revoke_tasks(task_ids)
            except Exception as e:
                logger.warning(f"Failed to revoke Celery task: {e}")

        if delete_ig_job(job_id):
            logger.info(f"Deleted IG Reel job {job_id[:8]}, revoked {revoked} tasks")
            return jsonify({'status': 'deleted', 'job_id': job_id, 'tasks_revoked': revoked})
        return jsonify({'error': 'Failed to delete'}), 500

    except Exception as e:
        logger.error(f"Failed to delete IG Reel job: {e}")
        return jsonify({'error': str(e)}), 500


# ============ Product Photos Management ============

PRODUCT_CATEGORIES = {
    'face_tape': 'Face Tape',
    'gua_sha': 'Gua Sha',
    'steam_eye_mask': 'Steam Eye Mask',
}

ALLOWED_PHOTO_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp'}
MAX_PHOTOS_PER_CATEGORY = 10

PRODUCT_PHOTOS_DIR = os.path.join(os.path.dirname(__file__), 'static', 'product_photos')


def _ensure_product_photo_dirs():
    """Create product photo directories if they don't exist."""
    for slug in PRODUCT_CATEGORIES:
        os.makedirs(os.path.join(PRODUCT_PHOTOS_DIR, slug), exist_ok=True)


def _get_category_photos(slug: str) -> list:
    """Get list of photos for a category."""
    category_dir = os.path.join(PRODUCT_PHOTOS_DIR, slug)
    if not os.path.exists(category_dir):
        return []
    photos = []
    for filename in sorted(os.listdir(category_dir)):
        ext = os.path.splitext(filename)[1].lower()
        if ext in ALLOWED_PHOTO_EXTENSIONS:
            photos.append({
                'filename': filename,
                'url': f'/static/product_photos/{slug}/{filename}',
                'path': os.path.join(category_dir, filename),
            })
    return photos


@admin_bp.route('/product-photos', methods=['GET'])
@require_auth
def list_product_photos():
    """List all product photo categories with their photos."""
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


@admin_bp.route('/product-photos/<category>', methods=['POST'])
@require_auth
def upload_product_photo(category):
    """Upload a photo to a product category."""
    try:
        if category not in PRODUCT_CATEGORIES:
            return jsonify({'error': f'Invalid category: {category}'}), 400

        _ensure_product_photo_dirs()

        # Check current photo count
        existing = _get_category_photos(category)
        if len(existing) >= MAX_PHOTOS_PER_CATEGORY:
            return jsonify({'error': f'Maximum {MAX_PHOTOS_PER_CATEGORY} photos per category'}), 400

        if 'photo' not in request.files:
            return jsonify({'error': 'No photo file provided'}), 400

        file = request.files['photo']
        if not file.filename:
            return jsonify({'error': 'No file selected'}), 400

        ext = os.path.splitext(file.filename)[1].lower()
        if ext not in ALLOWED_PHOTO_EXTENSIONS:
            return jsonify({'error': f'Invalid file type. Allowed: {", ".join(ALLOWED_PHOTO_EXTENSIONS)}'}), 400

        # Generate unique filename
        filename = secure_filename(file.filename)
        # Avoid collisions
        base, ext = os.path.splitext(filename)
        category_dir = os.path.join(PRODUCT_PHOTOS_DIR, category)
        final_path = os.path.join(category_dir, filename)
        counter = 1
        while os.path.exists(final_path):
            filename = f"{base}_{counter}{ext}"
            final_path = os.path.join(category_dir, filename)
            counter += 1

        file.save(final_path)
        logger.info(f"Uploaded product photo: {category}/{filename}")

        return jsonify({
            'filename': filename,
            'url': f'/static/product_photos/{category}/{filename}',
            'path': final_path,
        })

    except Exception as e:
        logger.error(f"Failed to upload product photo: {e}")
        return jsonify({'error': str(e)}), 500


@admin_bp.route('/product-photos/<category>/<filename>', methods=['DELETE'])
@require_auth
def delete_product_photo(category, filename):
    """Delete a product photo."""
    try:
        if category not in PRODUCT_CATEGORIES:
            return jsonify({'error': f'Invalid category: {category}'}), 400

        filename = secure_filename(filename)
        filepath = os.path.join(PRODUCT_PHOTOS_DIR, category, filename)

        if not os.path.exists(filepath):
            return jsonify({'error': 'Photo not found'}), 404

        os.remove(filepath)
        logger.info(f"Deleted product photo: {category}/{filename}")

        return jsonify({'success': True})

    except Exception as e:
        logger.error(f"Failed to delete product photo: {e}")
        return jsonify({'error': str(e)}), 500


# ============ API Key Rotation Status ============

@admin_bp.route('/api-keys/status', methods=['GET'])
@require_auth
def get_api_keys_status():
    """
    Get status of all Gemini API keys with usage stats.

    Returns:
    {
        "total_keys": 5,
        "available_keys": 4,
        "total_rpm_available": 65,
        "total_daily_available": 980,
        "seconds_until_daily_reset": 28800,
        "keys": [
            {
                "key_id": "AIzaSyBY",
                "rpm_used": 5,
                "rpm_limit": 18,
                "daily_used": 50,
                "daily_limit": 250,
                "is_available": true
            },
            ...
        ]
    }
    """
    try:
        from api_key_manager import get_api_key_manager
        manager = get_api_key_manager()
        summary = manager.get_summary()

        # Add image generation totals from key data
        image_data = summary.get('image', {})
        keys = image_data.get('keys', [])
        total_images_today = sum(k.get('daily_used', 0) for k in keys)
        # Only count usable keys for remaining/capacity (exclude exhausted and free-tier)
        usable_keys = [k for k in keys if not k.get('is_daily_exhausted') and not k.get('is_free_tier')]
        total_daily_capacity = sum(k.get('daily_limit', 0) for k in usable_keys)
        total_images_remaining = sum(
            max(0, k.get('daily_limit', 0) - k.get('daily_used', 0))
            for k in usable_keys
        )

        summary['image_stats'] = {
            'total_images_today': total_images_today,
            'total_daily_capacity': total_daily_capacity,
            'total_images_remaining': total_images_remaining,
            'estimated_links_remaining': total_images_remaining // 8,  # ~8 images per link avg
        }

        # Add link processing stats from SQLite
        try:
            from database import get_today_processing_stats
            summary['processing_stats'] = get_today_processing_stats()
        except Exception as db_err:
            logger.warning(f"Could not get processing stats: {db_err}")
            summary['processing_stats'] = None

        # Flatten image keys to top-level for backwards compat
        if 'image' in summary and 'keys' in summary['image']:
            summary['keys'] = summary['image']['keys']
            summary['available_keys'] = summary['image']['available_keys']
            summary['total_rpm_available'] = summary['image']['total_rpm_available']
            summary['total_daily_available'] = summary['image']['total_daily_available']

        return jsonify(summary)
    except ImportError:
        # Manager not available, return single-key status
        gemini_key = os.getenv('GEMINI_API_KEY', '')
        return jsonify({
            'total_keys': 1 if gemini_key else 0,
            'available_keys': 1 if gemini_key else 0,
            'total_rpm_available': 18 if gemini_key else 0,
            'total_daily_available': 250 if gemini_key else 0,
            'seconds_until_daily_reset': 0,
            'keys': [{
                'key_id': mask_key(gemini_key) if gemini_key else 'N/A',
                'rpm_used': 0,
                'rpm_limit': 18,
                'daily_used': 0,
                'daily_limit': 250,
                'is_available': bool(gemini_key)
            }] if gemini_key else []
        })
    except Exception as e:
        logger.error(f"Failed to get API keys status: {e}")
        return jsonify({'error': str(e)}), 500


@admin_bp.route('/api-keys/reset', methods=['POST'])
@require_auth
def reset_api_keys():
    """
    Reset usage counters for all API keys (or a specific key).

    Expected JSON (optional):
    - key_id: Specific key ID to reset (first 8 chars). If not provided, resets all.

    Returns:
    - status: "reset"
    - message: Description
    """
    try:
        from api_key_manager import get_api_key_manager
        manager = get_api_key_manager()

        data = request.get_json() or {}
        key_id = data.get('key_id')

        if key_id:
            manager.reset_key(key_id)
            return jsonify({
                'status': 'reset',
                'message': f'Reset counters for key {key_id}'
            })
        else:
            manager.reset_all_keys()
            return jsonify({
                'status': 'reset',
                'message': 'Reset counters for all keys'
            })

    except ImportError:
        return jsonify({'error': 'API key manager not available'}), 500
    except Exception as e:
        logger.error(f"Failed to reset API keys: {e}")
        return jsonify({'error': str(e)}), 500


# ============ Stuck Jobs Cleanup ============

@admin_bp.route('/cleanup-stuck', methods=['POST'])
@require_auth
def cleanup_stuck_jobs():
    """
    Find and clean up jobs stuck in 'processing' state.

    Expected JSON (optional):
    - action: 'fail' (default), 'reset', or 'delete'
    - threshold_minutes: Minutes to consider stuck (default: 15)
    - dry_run: If true, only report what would be cleaned (default: false)

    Returns:
    - stuck_jobs: list of stuck job summaries
    - stuck_batches: list of stuck batch summaries
    - action_taken: what was done
    """
    try:
        from database import get_db
        from datetime import datetime, timedelta

        data = request.get_json() or {}
        action = data.get('action', 'fail')
        threshold = data.get('threshold_minutes', 15)
        dry_run = data.get('dry_run', False)

        threshold_time = datetime.utcnow() - timedelta(minutes=threshold)
        threshold_str = threshold_time.isoformat()

        with get_db() as conn:
            cursor = conn.cursor()

            # Find stuck jobs
            cursor.execute("""
                SELECT id, job_type, status, tiktok_url, folder_name, created_at, started_at
                FROM jobs
                WHERE status = 'processing'
                AND (started_at < ? OR (started_at IS NULL AND created_at < ?))
            """, (threshold_str, threshold_str))
            stuck_jobs = [dict(row) for row in cursor.fetchall()]

            # Find stuck batch links
            cursor.execute("""
                SELECT id, batch_id, link_url, status, created_at, started_at
                FROM batch_links
                WHERE status = 'processing'
                AND (started_at < ? OR (started_at IS NULL AND created_at < ?))
            """, (threshold_str, threshold_str))
            stuck_links = [dict(row) for row in cursor.fetchall()]

            # Find stuck batches
            cursor.execute("""
                SELECT id, status, total_links, created_at, started_at
                FROM batches
                WHERE status = 'processing'
                AND (started_at < ? OR (started_at IS NULL AND created_at < ?))
            """, (threshold_str, threshold_str))
            stuck_batches = [dict(row) for row in cursor.fetchall()]

            # Find stuck IG Reel jobs
            cursor.execute("""
                SELECT id, status, num_videos, created_at, started_at
                FROM ig_jobs
                WHERE status = 'processing'
                AND (started_at < ? OR (started_at IS NULL AND created_at < ?))
            """, (threshold_str, threshold_str))
            stuck_ig_jobs = [dict(row) for row in cursor.fetchall()]

            cleaned = {'jobs': 0, 'links': 0, 'batches': 0, 'ig_jobs': 0}

            if not dry_run and (stuck_jobs or stuck_links or stuck_batches or stuck_ig_jobs):
                now_str = datetime.utcnow().isoformat()

                if action == 'fail':
                    cursor.execute("""
                        UPDATE jobs SET status = 'failed',
                            error_message = 'Auto-cleaned: stuck in processing',
                            completed_at = ?
                        WHERE status = 'processing'
                        AND (started_at < ? OR (started_at IS NULL AND created_at < ?))
                    """, (now_str, threshold_str, threshold_str))
                    cleaned['jobs'] = cursor.rowcount

                    cursor.execute("""
                        UPDATE batch_links SET status = 'failed',
                            error_message = 'Auto-cleaned: stuck in processing',
                            completed_at = ?
                        WHERE status = 'processing'
                        AND (started_at < ? OR (started_at IS NULL AND created_at < ?))
                    """, (now_str, threshold_str, threshold_str))
                    cleaned['links'] = cursor.rowcount

                    cursor.execute("""
                        UPDATE batches SET status = 'failed',
                            error_message = 'Auto-cleaned: stuck in processing',
                            completed_at = ?
                        WHERE status = 'processing'
                        AND (started_at < ? OR (started_at IS NULL AND created_at < ?))
                    """, (now_str, threshold_str, threshold_str))
                    cleaned['batches'] = cursor.rowcount

                    cursor.execute("""
                        UPDATE ig_jobs SET status = 'failed',
                            error_message = 'Auto-cleaned: stuck in processing',
                            completed_at = ?
                        WHERE status = 'processing'
                        AND (started_at < ? OR (started_at IS NULL AND created_at < ?))
                    """, (now_str, threshold_str, threshold_str))
                    cleaned['ig_jobs'] = cursor.rowcount

                elif action == 'reset':
                    cursor.execute("""
                        UPDATE jobs SET status = 'pending', started_at = NULL
                        WHERE status = 'processing'
                        AND (started_at < ? OR (started_at IS NULL AND created_at < ?))
                    """, (threshold_str, threshold_str))
                    cleaned['jobs'] = cursor.rowcount

                    cursor.execute("""
                        UPDATE batch_links SET status = 'pending', started_at = NULL
                        WHERE status = 'processing'
                        AND (started_at < ? OR (started_at IS NULL AND created_at < ?))
                    """, (threshold_str, threshold_str))
                    cleaned['links'] = cursor.rowcount

                    cursor.execute("""
                        UPDATE batches SET status = 'pending', started_at = NULL
                        WHERE status = 'processing'
                        AND (started_at < ? OR (started_at IS NULL AND created_at < ?))
                    """, (threshold_str, threshold_str))
                    cleaned['batches'] = cursor.rowcount

                    cursor.execute("""
                        UPDATE ig_jobs SET status = 'pending', started_at = NULL
                        WHERE status = 'processing'
                        AND (started_at < ? OR (started_at IS NULL AND created_at < ?))
                    """, (threshold_str, threshold_str))
                    cleaned['ig_jobs'] = cursor.rowcount

        return jsonify({
            'stuck_jobs': stuck_jobs,
            'stuck_links': stuck_links,
            'stuck_batches': stuck_batches,
            'stuck_ig_jobs': stuck_ig_jobs,
            'total_stuck': len(stuck_jobs) + len(stuck_links) + len(stuck_batches) + len(stuck_ig_jobs),
            'dry_run': dry_run,
            'action': action,
            'threshold_minutes': threshold,
            'cleaned': cleaned
        })

    except Exception as e:
        logger.error(f"Failed to cleanup stuck jobs: {e}")
        return jsonify({'error': str(e)}), 500
