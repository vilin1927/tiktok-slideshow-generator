"""
Admin API Routes
Endpoints for managing API keys with password protection

Security:
- Constant-time password comparison to prevent timing attacks
- Rate limiting on login attempts to prevent brute force
- Session tokens with expiration
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

# Rate limiting for login attempts
# Format: {ip_address: {'attempts': count, 'lockout_until': timestamp}}
login_attempts = {}
MAX_LOGIN_ATTEMPTS = 5
LOCKOUT_DURATION = 300  # 5 minutes


def _get_client_ip():
    """Get client IP address, handling proxies."""
    # Check X-Forwarded-For header for proxied requests
    forwarded = request.headers.get('X-Forwarded-For')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.remote_addr or 'unknown'


def _check_rate_limit(ip: str) -> tuple[bool, int]:
    """
    Check if IP is rate limited.
    Returns (is_allowed, seconds_until_unlock)
    """
    now = time.time()

    if ip not in login_attempts:
        return True, 0

    record = login_attempts[ip]

    # Check if still in lockout period
    if record.get('lockout_until', 0) > now:
        seconds_left = int(record['lockout_until'] - now)
        return False, seconds_left

    # Reset if lockout has expired
    if record.get('lockout_until', 0) <= now and record.get('lockout_until', 0) > 0:
        login_attempts[ip] = {'attempts': 0, 'lockout_until': 0}

    return True, 0


def _record_login_attempt(ip: str, success: bool):
    """Record a login attempt and apply lockout if needed."""
    now = time.time()

    if ip not in login_attempts:
        login_attempts[ip] = {'attempts': 0, 'lockout_until': 0}

    if success:
        # Reset on successful login
        login_attempts[ip] = {'attempts': 0, 'lockout_until': 0}
    else:
        # Increment failed attempts
        login_attempts[ip]['attempts'] += 1

        # Apply lockout if max attempts exceeded
        if login_attempts[ip]['attempts'] >= MAX_LOGIN_ATTEMPTS:
            login_attempts[ip]['lockout_until'] = now + LOCKOUT_DURATION
            logger.warning(f"IP {ip} locked out for {LOCKOUT_DURATION}s after {MAX_LOGIN_ATTEMPTS} failed attempts")


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
        # Find the .env file - only use relative path from current file
        dotenv_path = find_dotenv()
        if not dotenv_path:
            # Fall back to .env in same directory as this file
            dotenv_path = os.path.join(os.path.dirname(__file__), '.env')

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

    Security:
    - Rate limited: 5 attempts per 5 minutes
    - Constant-time password comparison to prevent timing attacks

    Expected JSON:
    - password: Admin password

    Returns:
    - token: Session token (valid for 1 hour)
    """
    try:
        # Check rate limit first
        client_ip = _get_client_ip()
        is_allowed, seconds_left = _check_rate_limit(client_ip)

        if not is_allowed:
            logger.warning(f"Rate limited login attempt from {client_ip}")
            return jsonify({
                'error': 'Too many login attempts. Please try again later.',
                'retry_after': seconds_left
            }), 429

        data = request.get_json() or {}
        password = data.get('password', '')

        admin_password = os.getenv('ADMIN_PASSWORD')

        if not admin_password:
            logger.error("ADMIN_PASSWORD not configured")
            return jsonify({'error': 'Admin not configured'}), 500

        # Use constant-time comparison to prevent timing attacks
        # Both values must be strings of equal length for proper comparison
        password_bytes = password.encode('utf-8')
        admin_password_bytes = admin_password.encode('utf-8')

        if not secrets.compare_digest(password_bytes, admin_password_bytes):
            _record_login_attempt(client_ip, success=False)
            logger.warning(f"Failed admin login attempt from {client_ip}")
            return jsonify({'error': 'Invalid password'}), 401

        # Successful login - reset rate limit counter
        _record_login_attempt(client_ip, success=True)

        # Generate session token
        token = secrets.token_urlsafe(32)
        active_sessions[token] = {
            'expires': time.time() + SESSION_DURATION
        }

        logger.info(f"Admin login successful from {client_ip}")

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
