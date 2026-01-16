"""
Admin API Routes
Endpoints for managing API keys with password protection
"""
import os
import re
import secrets
import time
from functools import wraps
from flask import Blueprint, request, jsonify
from dotenv import load_dotenv, set_key, find_dotenv

from logging_config import get_logger
from database import list_video_jobs, get_video_jobs_count

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


def update_env_file(key: str, value: str) -> bool:
    """Update a key in the .env file"""
    try:
        # Find the .env file
        dotenv_path = find_dotenv()
        if not dotenv_path:
            # Try common locations
            possible_paths = [
                os.path.join(os.path.dirname(__file__), '.env'),
                '/root/tiktok-slideshow-generator/Desktop/tiktok/backend/.env'
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

        return jsonify({
            'status': 'updated',
            'updated': updated,
            'message': f'Updated: {", ".join(updated)}'
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
