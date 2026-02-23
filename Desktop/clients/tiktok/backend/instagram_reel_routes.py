"""
Instagram Reel Generator Routes
Flask API endpoints for format management, asset management, and video generation.
"""
import json
import os
import shutil
import subprocess
import uuid
from functools import wraps

from flask import Blueprint, request, jsonify
from werkzeug.utils import secure_filename

from logging_config import get_logger
from database import (
    create_ig_format, get_ig_format, list_ig_formats, update_ig_format, delete_ig_format,
    create_ig_character, get_ig_character, list_ig_characters, delete_ig_character,
    create_ig_asset, get_ig_assets_by_character, get_ig_asset, delete_ig_asset,
    create_ig_job, get_ig_job, list_ig_jobs, get_ig_job_status,
    get_ig_videos_by_job,
)

logger = get_logger('ig_reel_routes')

ig_reel_bp = Blueprint('instagram_reel', __name__, url_prefix='/api/instagram-reel')

# Asset storage base directory
ASSETS_DIR = os.path.join(os.path.dirname(__file__), 'temp', 'ig_assets')
FORMATS_DIR = os.path.join(os.path.dirname(__file__), 'temp', 'ig_formats')

ALLOWED_PHOTO_EXT = {'jpg', 'jpeg', 'png', 'webp'}
ALLOWED_VIDEO_EXT = {'mp4', 'mov'}
ALLOWED_AUDIO_EXT = {'mp3', 'wav', 'aac', 'm4a'}

for d in [ASSETS_DIR, FORMATS_DIR]:
    os.makedirs(d, exist_ok=True)


def require_page_password(f):
    """Decorator to require PAGE_PASSWORD on write endpoints."""
    @wraps(f)
    def decorated(*args, **kwargs):
        page_password = os.environ.get('PAGE_PASSWORD')
        if not page_password:
            return f(*args, **kwargs)
        token = request.headers.get('X-Page-Password', '')
        if token != page_password:
            return jsonify({'error': 'Authentication required'}), 401
        return f(*args, **kwargs)
    return decorated


def _allowed_file(filename, extensions):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in extensions


# ============ Character (Asset Group) Endpoints ============

@ig_reel_bp.route('/characters', methods=['POST'])
@require_page_password
def create_character():
    """Create a new character (persona)."""
    data = request.get_json() or {}
    name = data.get('name', '').strip()

    if not name:
        return jsonify({'error': 'Character name is required'}), 400

    # Sanitize name for filesystem
    safe_name = secure_filename(name)
    if not safe_name:
        return jsonify({'error': 'Invalid character name'}), 400

    try:
        character_id = create_ig_character(safe_name)
    except Exception as e:
        if 'UNIQUE' in str(e):
            return jsonify({'error': f'Character "{safe_name}" already exists'}), 409
        raise

    # Create asset directories
    char_dir = os.path.join(ASSETS_DIR, safe_name)
    for subdir in ['before_photos', 'after_photos', 'before_videos', 'after_videos']:
        os.makedirs(os.path.join(char_dir, subdir), exist_ok=True)

    logger.info(f"Created character: {safe_name} ({character_id[:8]})")
    return jsonify({'id': character_id, 'name': safe_name}), 201


@ig_reel_bp.route('/characters', methods=['GET'])
def get_characters():
    """List all characters with asset counts."""
    characters = list_ig_characters()
    return jsonify({'characters': characters})


@ig_reel_bp.route('/characters/<character_id>', methods=['DELETE'])
@require_page_password
def remove_character(character_id):
    """Delete a character and all its assets."""
    character = get_ig_character(character_id)
    if not character:
        return jsonify({'error': 'Character not found'}), 404

    # Delete files from disk
    char_dir = os.path.join(ASSETS_DIR, character['character_name'])
    if os.path.exists(char_dir):
        shutil.rmtree(char_dir, ignore_errors=True)

    # Delete from DB (cascades to assets)
    delete_ig_character(character_id)

    logger.info(f"Deleted character: {character['character_name']}")
    return jsonify({'status': 'deleted', 'id': character_id})


# ============ Asset Endpoints ============

@ig_reel_bp.route('/characters/<character_id>/assets', methods=['POST'])
@require_page_password
def upload_assets(character_id):
    """
    Upload assets for a character.

    Form data:
        asset_type: 'before_photo', 'after_photo', 'before_video', 'after_video'
        files: multipart file uploads
    """
    character = get_ig_character(character_id)
    if not character:
        return jsonify({'error': 'Character not found'}), 404

    asset_type = request.form.get('asset_type', '')
    valid_types = ['before_photo', 'after_photo', 'before_video', 'after_video']
    if asset_type not in valid_types:
        return jsonify({'error': f'Invalid asset_type. Must be one of: {valid_types}'}), 400

    files = request.files.getlist('files')
    if not files:
        return jsonify({'error': 'No files provided'}), 400

    # Determine allowed extensions
    is_video = asset_type.endswith('_video')
    allowed_ext = ALLOWED_VIDEO_EXT if is_video else ALLOWED_PHOTO_EXT

    # Save files
    char_dir = os.path.join(ASSETS_DIR, character['character_name'])
    type_dir = os.path.join(char_dir, asset_type + 's')  # before_photo → before_photos
    os.makedirs(type_dir, exist_ok=True)

    uploaded = []
    for f in files:
        if not f or not f.filename:
            continue
        if not _allowed_file(f.filename, allowed_ext):
            continue

        original_name = secure_filename(f.filename)
        unique_name = f"{uuid.uuid4().hex[:8]}_{original_name}"
        file_path = os.path.join(type_dir, unique_name)
        f.save(file_path)

        # Validate file content matches expected type
        if is_video:
            try:
                result = subprocess.run(
                    ['ffprobe', '-v', 'error', file_path],
                    capture_output=True, timeout=10
                )
                if result.returncode != 0:
                    logger.warning(f"Invalid video file skipped: {original_name}")
                    os.remove(file_path)
                    continue
            except subprocess.TimeoutExpired:
                logger.warning(f"ffprobe timeout on video file: {original_name}")
                os.remove(file_path)
                continue
        else:
            try:
                from PIL import Image
                img = Image.open(file_path)
                img.verify()
            except Exception:
                logger.warning(f"Invalid image file skipped: {original_name}")
                os.remove(file_path)
                continue

        asset_id = create_ig_asset(
            character_id=character_id,
            asset_type=asset_type,
            file_path=file_path,
            original_filename=original_name
        )
        uploaded.append({'id': asset_id, 'filename': original_name, 'type': asset_type})

    logger.info(f"Uploaded {len(uploaded)} {asset_type} assets for {character['character_name']}")
    return jsonify({'uploaded': uploaded, 'count': len(uploaded)})


@ig_reel_bp.route('/characters/<character_id>/assets', methods=['GET'])
def get_assets(character_id):
    """Get all assets for a character, grouped by type."""
    character = get_ig_character(character_id)
    if not character:
        return jsonify({'error': 'Character not found'}), 404

    assets = get_ig_assets_by_character(character_id)

    # Group by type
    grouped = {
        'before_photo': [],
        'after_photo': [],
        'before_video': [],
        'after_video': [],
    }
    for a in assets:
        t = a.get('asset_type', '')
        if t in grouped:
            grouped[t].append(a)

    return jsonify({'character': character, 'assets': grouped})


@ig_reel_bp.route('/assets/<asset_id>', methods=['DELETE'])
@require_page_password
def remove_asset(asset_id):
    """Delete a single asset."""
    asset = get_ig_asset(asset_id)
    if not asset:
        return jsonify({'error': 'Asset not found'}), 404

    # Delete file from disk
    if os.path.exists(asset['file_path']):
        try:
            os.remove(asset['file_path'])
        except Exception as e:
            logger.warning(f"Failed to delete file {asset['file_path']}: {e}")

    delete_ig_asset(asset_id)
    return jsonify({'status': 'deleted', 'id': asset_id})


# ============ Format Template Endpoints ============

@ig_reel_bp.route('/formats/scrape', methods=['POST'])
@require_page_password
def scrape_format():
    """
    Scrape an Instagram reel and create a format template.

    JSON body:
        {"url": "https://instagram.com/reel/...", "format_name": "face-fat-transformation"}
    """
    data = request.get_json() or {}
    url = data.get('url', '').strip()
    format_name = data.get('format_name', '').strip()

    if not url:
        return jsonify({'error': 'Instagram URL is required'}), 400
    if not format_name:
        return jsonify({'error': 'Format name is required'}), 400

    # Sanitize format name for filesystem safety
    safe_format_name = secure_filename(format_name)
    if not safe_format_name:
        return jsonify({'error': 'Invalid format name (contains only special characters)'}), 400

    # Check for duplicate format name
    existing_formats = list_ig_formats()
    for ef in existing_formats:
        if ef.get('format_name') == safe_format_name:
            return jsonify({'error': f'Format "{safe_format_name}" already exists'}), 409

    try:
        from instagram_scraper import scrape_and_create_format, InstagramScraperError

        # Try to get API key manager for Gemini analysis
        api_key_manager = None
        try:
            from api_key_manager import get_api_key_manager
            api_key_manager = get_api_key_manager()
        except Exception:
            pass

        template = scrape_and_create_format(
            url=url,
            format_name=safe_format_name,
            output_base_dir=FORMATS_DIR,
            api_key_manager=api_key_manager
        )

        # Save to database
        format_id = create_ig_format(
            format_name=template['format_name'],
            instagram_url=template['instagram_url'],
            audio_path=template['audio_path'],
            total_duration=template['total_duration'],
            clips_json=json.dumps(template['clips'])
        )

        logger.info(f"Created format: {safe_format_name} ({format_id[:8]})")
        return jsonify({
            'id': format_id,
            'format_name': safe_format_name,
            'total_duration': template['total_duration'],
            'clips': template['clips']
        }), 201

    except Exception as e:
        error_msg = str(e)
        logger.error(f"Format scrape failed: {error_msg}")
        # Return user-friendly error for scrape/API failures
        if any(kw in error_msg.lower() for kw in ['rapidapi', 'download', 'scrape', '500', '404', '403', 'timeout']):
            return jsonify({'error': 'Could not download reel. The URL may be invalid, deleted, or private.'}), 400
        return jsonify({'error': f'Format creation failed: {error_msg}'}), 500


@ig_reel_bp.route('/formats/upload', methods=['POST'])
@require_page_password
def upload_format():
    """
    Create a format template from an uploaded reel video file.

    Multipart form:
        file: video file (mp4, mov)
        format_name: name for the template
    """
    if 'file' not in request.files:
        return jsonify({'error': 'Video file is required'}), 400

    file = request.files['file']
    format_name = request.form.get('format_name', '').strip()

    if not format_name:
        return jsonify({'error': 'Format name is required'}), 400

    # Sanitize format name for filesystem safety
    safe_format_name = secure_filename(format_name)
    if not safe_format_name:
        return jsonify({'error': 'Invalid format name (contains only special characters)'}), 400

    # Check for duplicate format name
    existing_formats = list_ig_formats()
    for ef in existing_formats:
        if ef.get('format_name') == safe_format_name:
            return jsonify({'error': f'Format "{safe_format_name}" already exists'}), 409

    ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else ''
    if ext not in ('mp4', 'mov', 'webm', 'mkv'):
        return jsonify({'error': 'Only video files (mp4, mov, webm, mkv) are allowed'}), 400

    try:
        from instagram_scraper import create_format_from_upload

        # Save uploaded file temporarily
        import tempfile
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=f'.{ext}')
        file.save(tmp.name)
        tmp.close()

        api_key_manager = None
        try:
            from api_key_manager import get_api_key_manager
            api_key_manager = get_api_key_manager()
        except Exception:
            pass

        template = create_format_from_upload(
            video_path=tmp.name,
            format_name=safe_format_name,
            output_base_dir=FORMATS_DIR,
            api_key_manager=api_key_manager
        )

        # Clean up temp file
        try:
            os.remove(tmp.name)
        except Exception:
            pass

        format_id = create_ig_format(
            format_name=template['format_name'],
            instagram_url=template['instagram_url'],
            audio_path=template.get('audio_path') or '',
            total_duration=template['total_duration'],
            clips_json=json.dumps(template['clips'])
        )

        logger.info(f"Created format from upload: {safe_format_name} ({format_id[:8]})")
        return jsonify({
            'id': format_id,
            'format_name': safe_format_name,
            'total_duration': template['total_duration'],
            'clips': template['clips']
        }), 201

    except Exception as e:
        logger.error(f"Format upload failed: {e}")
        return jsonify({'error': str(e)}), 500


@ig_reel_bp.route('/formats', methods=['GET'])
def get_formats():
    """List all saved format templates."""
    formats = list_ig_formats()
    return jsonify({'formats': formats})


@ig_reel_bp.route('/formats/<format_id>', methods=['GET'])
def get_format(format_id):
    """Get a single format template."""
    fmt = get_ig_format(format_id)
    if not fmt:
        return jsonify({'error': 'Format not found'}), 404
    return jsonify(fmt)


@ig_reel_bp.route('/formats/<format_id>', methods=['PUT'])
@require_page_password
def edit_format(format_id):
    """
    Update a format template (edit clip durations, types, etc.).

    JSON body (all optional):
        {"format_name": str, "clips": [...], "total_duration": float}
    """
    fmt = get_ig_format(format_id)
    if not fmt:
        return jsonify({'error': 'Format not found'}), 404

    data = request.get_json() or {}

    update_kwargs = {}
    if 'format_name' in data:
        update_kwargs['format_name'] = data['format_name']
    if 'clips' in data:
        clips = data['clips']
        if not isinstance(clips, list) or len(clips) == 0:
            return jsonify({'error': 'Format must have at least one clip'}), 400
        for clip in clips:
            duration = clip.get('duration', 0)
            if not isinstance(duration, (int, float)) or duration <= 0:
                return jsonify({'error': f'Clip duration must be positive, got {duration}'}), 400
        update_kwargs['clips_json'] = json.dumps(clips)
        # Recalculate total duration from clips
        update_kwargs['total_duration'] = sum(c.get('duration', 0) for c in clips)
    if 'total_duration' in data and 'clips' not in data:
        update_kwargs['total_duration'] = data['total_duration']

    if update_kwargs:
        update_ig_format(format_id, **update_kwargs)
        logger.info(f"Updated format {format_id[:8]}: {list(update_kwargs.keys())}")

    return jsonify(get_ig_format(format_id))


@ig_reel_bp.route('/formats/<format_id>', methods=['DELETE'])
@require_page_password
def remove_format(format_id):
    """Delete a format template and its audio file."""
    fmt = get_ig_format(format_id)
    if not fmt:
        return jsonify({'error': 'Format not found'}), 404

    # Delete audio file
    if fmt.get('audio_path') and os.path.exists(fmt['audio_path']):
        try:
            os.remove(fmt['audio_path'])
        except Exception:
            pass

    delete_ig_format(format_id)
    logger.info(f"Deleted format: {fmt['format_name']}")
    return jsonify({'status': 'deleted', 'id': format_id})


@ig_reel_bp.route('/formats/<format_id>/audio', methods=['POST'])
@require_page_password
def upload_custom_audio(format_id):
    """Upload custom audio file for a format template."""
    fmt = get_ig_format(format_id)
    if not fmt:
        return jsonify({'error': 'Format not found'}), 404

    file = request.files.get('audio')
    if not file or not file.filename:
        return jsonify({'error': 'No audio file provided'}), 400

    if not _allowed_file(file.filename, ALLOWED_AUDIO_EXT):
        return jsonify({'error': 'Invalid audio format. Allowed: mp3, wav, aac, m4a'}), 400

    # Save audio file
    format_dir = os.path.join(FORMATS_DIR, fmt['format_name'].replace(' ', '_'))
    os.makedirs(format_dir, exist_ok=True)

    filename = secure_filename(file.filename)
    audio_path = os.path.join(format_dir, f'custom_audio_{filename}')
    file.save(audio_path)

    # Delete old audio if it exists
    if fmt.get('audio_path') and os.path.exists(fmt['audio_path']) and fmt['audio_path'] != audio_path:
        try:
            os.remove(fmt['audio_path'])
        except Exception:
            pass

    update_ig_format(format_id, audio_path=audio_path)
    logger.info(f"Updated audio for format {fmt['format_name']}")

    return jsonify({'status': 'updated', 'audio_path': audio_path})


# ============ Generation Endpoints ============

@ig_reel_bp.route('/generate', methods=['POST'])
@require_page_password
def start_generation():
    """
    Start a reel generation job.

    JSON body:
        {
            "format_id": "uuid",
            "num_videos": 10,
            "num_text_variations": 3,
            "asset_type": "photos",
            "hook_text": "POV: you lose face fat",
            "cta_text": "see how I did it ⬇️",
            "character_ids": ["uuid1", "uuid2"]
        }
    """
    data = request.get_json() or {}

    format_id = data.get('format_id')
    num_videos = data.get('num_videos', 10)
    num_text_variations = data.get('num_text_variations', 1)
    asset_type = data.get('asset_type', 'photos')
    hook_text = data.get('hook_text', '')
    cta_text = data.get('cta_text', '')
    character_ids = data.get('character_ids', [])
    clip_texts = data.get('clip_texts')  # New per-clip text array

    # Validate
    if not format_id:
        return jsonify({'error': 'format_id is required'}), 400

    fmt = get_ig_format(format_id)
    if not fmt:
        return jsonify({'error': 'Format not found'}), 404

    if not character_ids:
        return jsonify({'error': 'At least one character_id is required'}), 400

    num_videos = max(1, min(50, int(num_videos)))
    num_text_variations = max(1, min(10, int(num_text_variations)))

    if asset_type not in ('photos', 'videos', 'both'):
        return jsonify({'error': 'asset_type must be photos, videos, or both'}), 400

    # Validate that selected characters have assets matching the requested type
    required_types = set()
    if asset_type in ('photos', 'both'):
        required_types.update(['before_photo', 'after_photo'])
    if asset_type in ('videos', 'both'):
        required_types.update(['before_video', 'after_video'])

    has_valid_character = False
    for char_id in character_ids:
        assets = get_ig_assets_by_character(char_id)
        asset_types_present = {a['asset_type'] for a in assets}
        if asset_type == 'photos' and 'before_photo' in asset_types_present and 'after_photo' in asset_types_present:
            has_valid_character = True
            break
        elif asset_type == 'videos' and 'before_video' in asset_types_present and 'after_video' in asset_types_present:
            has_valid_character = True
            break
        elif asset_type == 'both':
            has_photos = 'before_photo' in asset_types_present and 'after_photo' in asset_types_present
            has_videos = 'before_video' in asset_types_present and 'after_video' in asset_types_present
            if has_photos or has_videos:
                has_valid_character = True
                break

    if not has_valid_character:
        type_label = {'photos': 'photo', 'videos': 'video', 'both': 'photo or video'}.get(asset_type, asset_type)
        return jsonify({
            'error': f'No selected characters have {type_label} assets. Upload {type_label}s first or change Asset Type.'
        }), 400

    # Handle clip_texts: new per-clip mode or legacy hook_text/cta_text
    clip_texts_json = None
    if clip_texts and isinstance(clip_texts, list):
        # Validate: at least one clip must have non-empty text
        has_any_text = any(
            ct.get('text', '').strip()
            for ct in clip_texts
            if isinstance(ct, dict)
        )
        if not has_any_text:
            return jsonify({'error': 'At least one clip must have non-empty text'}), 400
        clip_texts_json = json.dumps(clip_texts)
    else:
        # Legacy mode: require hook_text
        if not hook_text:
            return jsonify({'error': 'hook_text is required'}), 400

    # Create job
    job_id = create_ig_job(
        format_id=format_id,
        num_videos=num_videos,
        hook_text=hook_text,
        cta_text=cta_text,
        num_text_variations=num_text_variations,
        asset_type=asset_type,
        character_ids_json=json.dumps(character_ids),
        clip_texts_json=clip_texts_json
    )

    logger.info(f"Created IG reel job: {job_id[:8]} ({num_videos} videos, {num_text_variations} text vars)")

    # Queue Celery task
    try:
        from instagram_reel_tasks import generate_reel_batch
        generate_reel_batch.delay(job_id)
        logger.info(f"Queued job {job_id[:8]} for processing")
    except Exception as e:
        logger.error(f"Failed to queue job: {e}")
        return jsonify({'error': f'Failed to queue job: {e}'}), 500

    return jsonify({'job_id': job_id, 'status': 'queued'})


@ig_reel_bp.route('/jobs', methods=['GET'])
def get_jobs():
    """List all generation jobs."""
    status = request.args.get('status')
    limit = request.args.get('limit', 50, type=int)
    offset = request.args.get('offset', 0, type=int)

    jobs = list_ig_jobs(status=status, limit=limit, offset=offset)
    return jsonify({'jobs': jobs})


@ig_reel_bp.route('/jobs/<job_id>', methods=['GET'])
def get_job_detail(job_id):
    """Get job details with video-level status."""
    job = get_ig_job(job_id)
    if not job:
        return jsonify({'error': 'Job not found'}), 404

    videos = get_ig_videos_by_job(job_id)
    job['videos'] = videos

    return jsonify(job)


@ig_reel_bp.route('/jobs/<job_id>/status', methods=['GET'])
def get_job_poll(job_id):
    """Lightweight polling endpoint for job progress."""
    status = get_ig_job_status(job_id)
    if not status:
        return jsonify({'error': 'Job not found'}), 404
    return jsonify(status)
