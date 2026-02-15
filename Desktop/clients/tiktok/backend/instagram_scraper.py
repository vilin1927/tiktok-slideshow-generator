"""
Instagram Reel Scraper

Downloads IG reels via RapidAPI, extracts audio + scene cuts via FFmpeg,
and uses Gemini to analyze clip structure (before/after/CTA).
"""
import json
import os
import re
import subprocess
import tempfile
import uuid

import requests
from dotenv import load_dotenv

load_dotenv()

from logging_config import get_logger

logger = get_logger('instagram_scraper')

RAPIDAPI_HOST = 'instagram-reels-downloader-api.p.rapidapi.com'


class InstagramScraperError(Exception):
    """Exception raised for Instagram scraping errors."""
    pass


def download_reel(url: str, output_dir: str) -> str:
    """
    Download an Instagram reel video using RapidAPI Instagram Reels Downloader.

    Args:
        url: Instagram reel URL
        output_dir: Directory to save the video

    Returns:
        Path to downloaded video file

    Raises:
        InstagramScraperError: If download fails
    """
    os.makedirs(output_dir, exist_ok=True)

    api_key = os.getenv('RAPIDAPI_IG_KEY', '').strip()
    if not api_key:
        raise InstagramScraperError("RAPIDAPI_IG_KEY not set in environment")

    logger.info(f"Downloading reel via RapidAPI: {url[:60]}...")

    # Step 1: Get video download URL from RapidAPI
    try:
        resp = requests.get(
            f"https://{RAPIDAPI_HOST}/download",
            params={"url": url},
            headers={
                "x-rapidapi-key": api_key,
                "x-rapidapi-host": RAPIDAPI_HOST,
            },
            timeout=30,
        )
        resp.raise_for_status()
        raw = resp.json()
    except requests.exceptions.Timeout:
        raise InstagramScraperError("RapidAPI request timed out (30s)")
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else 'unknown'
        raise InstagramScraperError(f"RapidAPI error (HTTP {status}): {e}")
    except Exception as e:
        raise InstagramScraperError(f"RapidAPI request failed: {e}")

    # Response is wrapped: {"success": true, "data": {..., "medias": [...]}}
    data = raw.get('data', raw)

    # Step 2: Extract video URL from response
    medias = data.get('medias', [])
    video_url = None
    for media in medias:
        if media.get('type') == 'video' or '.mp4' in media.get('url', ''):
            video_url = media['url']
            break
    if not video_url and medias:
        video_url = medias[0].get('url')
    if not video_url:
        raise InstagramScraperError(f"No video URL in API response: {json.dumps(raw)[:300]}")

    # Step 3: Download the actual video file
    reel_id = data.get('shortcode') or data.get('url', '').split('/')[-2] if data.get('url') else uuid.uuid4().hex[:10]
    video_path = os.path.join(output_dir, f'reel_{reel_id}.mp4')

    try:
        video_resp = requests.get(video_url, timeout=120, stream=True)
        video_resp.raise_for_status()
        with open(video_path, 'wb') as f:
            for chunk in video_resp.iter_content(chunk_size=8192):
                f.write(chunk)
    except Exception as e:
        raise InstagramScraperError(f"Failed to download video file: {e}")

    if not os.path.exists(video_path) or os.path.getsize(video_path) < 1000:
        raise InstagramScraperError("Download completed but video file is empty or too small")

    size_mb = os.path.getsize(video_path) / 1024 / 1024
    logger.info(f"Downloaded: reel_{reel_id}.mp4 ({size_mb:.1f}MB)")
    return video_path


def get_video_duration(video_path: str) -> float:
    """Get video duration in seconds using ffprobe."""
    try:
        result = subprocess.run(
            [
                'ffprobe',
                '-v', 'error',
                '-show_entries', 'format=duration',
                '-of', 'default=noprint_wrappers=1:nokey=1',
                video_path
            ],
            capture_output=True,
            text=True,
            timeout=30
        )
        if result.returncode == 0:
            return float(result.stdout.strip())
    except Exception as e:
        logger.warning(f"Could not get video duration: {e}")
    return 0.0


def extract_audio(video_path: str, output_path: str) -> str:
    """
    Extract audio track from video file.

    Args:
        video_path: Path to video file
        output_path: Path for output audio file (.mp3)

    Returns:
        Path to extracted audio file

    Raises:
        InstagramScraperError: If extraction fails
    """
    cmd = [
        'ffmpeg', '-y',
        '-i', video_path,
        '-vn',
        '-acodec', 'libmp3lame',
        '-ab', '192k',
        output_path
    ]

    logger.info("Extracting audio...")

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            raise InstagramScraperError(f"Audio extraction failed: {result.stderr[-200:]}")
    except subprocess.TimeoutExpired:
        raise InstagramScraperError("Audio extraction timed out")

    if not os.path.exists(output_path) or os.path.getsize(output_path) < 100:
        raise InstagramScraperError("Audio extraction produced no output")

    logger.info(f"Audio extracted: {output_path}")
    return output_path


def detect_scene_cuts(video_path: str, threshold: float = 0.4) -> list[float]:
    """
    Detect scene cuts (hard transitions) in a video using FFmpeg.

    Args:
        video_path: Path to video file
        threshold: Scene detection sensitivity (0-1, lower = more sensitive)

    Returns:
        List of cut timestamps in seconds
    """
    cmd = [
        'ffmpeg',
        '-i', video_path,
        '-vf', f'select=gt(scene\\,{threshold}),showinfo',
        '-f', 'null',
        '-'
    ]

    logger.info(f"Detecting scene cuts (threshold={threshold})...")

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        logger.warning("Scene detection timed out")
        return []

    # Parse timestamps from FFmpeg showinfo output
    cuts = []
    for match in re.finditer(r'pts_time:(\d+\.?\d*)', result.stderr):
        ts = float(match.group(1))
        if ts > 0.1:  # Skip near-zero timestamps
            cuts.append(round(ts, 2))

    # Remove duplicates and sort
    cuts = sorted(set(cuts))
    logger.info(f"Detected {len(cuts)} scene cuts: {cuts}")
    return cuts


def extract_clip_screenshots(video_path: str, cuts: list[float], total_duration: float) -> list[str]:
    """
    Extract one screenshot from the middle of each clip for Gemini analysis.

    Args:
        video_path: Path to video file
        cuts: List of cut timestamps
        total_duration: Total video duration

    Returns:
        List of screenshot file paths
    """
    # Build clip boundaries: [0, cut1, cut2, ..., total_duration]
    boundaries = [0.0] + cuts + [total_duration]

    screenshots = []
    output_dir = tempfile.mkdtemp(prefix='ig_screenshots_')

    for i in range(len(boundaries) - 1):
        start = boundaries[i]
        end = boundaries[i + 1]
        mid = (start + end) / 2

        output_path = os.path.join(output_dir, f'clip_{i:02d}.jpg')

        cmd = [
            'ffmpeg', '-y',
            '-ss', str(mid),
            '-i', video_path,
            '-vframes', '1',
            '-q:v', '2',
            output_path
        ]

        try:
            subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            if os.path.exists(output_path):
                screenshots.append(output_path)
            else:
                logger.warning(f"Screenshot for clip {i} not created")
        except Exception as e:
            logger.warning(f"Failed to extract screenshot for clip {i}: {e}")

    logger.info(f"Extracted {len(screenshots)} clip screenshots")
    return screenshots


def analyze_clips_with_gemini(screenshots: list[str], api_key_manager=None) -> list[dict]:
    """
    Use Gemini to analyze what each clip represents.

    Args:
        screenshots: List of screenshot file paths
        api_key_manager: Optional API key manager instance

    Returns:
        List of clip analysis dicts: [{"index": 0, "type": "before", "detected_text": "...", "text_position": "bottom"}, ...]
    """
    if not screenshots:
        return []

    try:
        from google import genai
        from google.genai import types
    except ImportError:
        logger.error("google-genai package not installed")
        return _fallback_clip_analysis(len(screenshots))

    # Get API key
    api_key = None
    if api_key_manager:
        key_result = api_key_manager.get_available_key('text')
        if key_result:
            # get_available_key returns a string (the key itself)
            api_key = key_result if isinstance(key_result, str) else key_result.get('key', '')

    if not api_key:
        api_key = os.getenv('GEMINI_API_KEY', '').strip().strip("'")

    if not api_key:
        logger.error("No Gemini API key available")
        return _fallback_clip_analysis(len(screenshots))

    client = genai.Client(api_key=api_key)

    # Build content with screenshots
    prompt = """Analyze these screenshots from an Instagram reel. Each image is from a different clip in the video.

For EACH clip screenshot, identify:
1. "type": Is this clip showing a "before" state, "after" state, "cta" (call-to-action), or "transition"?
2. "detected_text": What text is visible on screen? (null if none)
3. "text_position": Where is the text? "top", "center", or "bottom" (null if no text)

Return ONLY a JSON array, one object per clip, in order:
[
  {"index": 0, "type": "before", "detected_text": "POV: you lost face fat", "text_position": "center"},
  {"index": 1, "type": "after", "detected_text": null, "text_position": null}
]

Rules:
- "before" = shows initial/untreated state
- "after" = shows result/transformed state
- "cta" = call-to-action slide (text like "see how", "link in bio", etc.)
- "transition" = transition effect or unclear
- If unsure, default to "before" for first clip, "after" for last clip
"""

    contents = [prompt]
    for screenshot_path in screenshots:
        try:
            with open(screenshot_path, 'rb') as f:
                image_data = f.read()
            contents.append(types.Part.from_bytes(data=image_data, mime_type='image/jpeg'))
        except Exception as e:
            logger.warning(f"Failed to read screenshot {screenshot_path}: {e}")

    try:
        from config import GeminiConfig
        response = client.models.generate_content(
            model=GeminiConfig.TEXT_MODEL,
            contents=contents
        )

        # Parse JSON from response
        text = response.text.strip()
        # Strip markdown code blocks if present
        if text.startswith('```'):
            text = re.sub(r'^```\w*\n?', '', text)
            text = re.sub(r'\n?```$', '', text)

        clips = json.loads(text)
        logger.info(f"Gemini analysis: {len(clips)} clips analyzed")
        return clips

    except json.JSONDecodeError as e:
        logger.warning(f"Failed to parse Gemini response as JSON: {e}")
        return _fallback_clip_analysis(len(screenshots))
    except Exception as e:
        logger.error(f"Gemini analysis failed: {e}")
        return _fallback_clip_analysis(len(screenshots))


def _fallback_clip_analysis(num_clips: int) -> list[dict]:
    """Generate a basic fallback clip analysis when Gemini is unavailable."""
    clips = []
    for i in range(num_clips):
        if i == 0:
            clip_type = 'before'
        elif i == num_clips - 1 and num_clips > 2:
            clip_type = 'cta'
        else:
            clip_type = 'after'

        clips.append({
            'index': i,
            'type': clip_type,
            'detected_text': None,
            'text_position': 'bottom'
        })

    logger.info(f"Using fallback clip analysis for {num_clips} clips")
    return clips


def scrape_and_create_format(url: str, format_name: str, output_base_dir: str, api_key_manager=None) -> dict:
    """
    Full pipeline: download IG reel → extract audio → detect scenes → analyze clips → return format template.

    Args:
        url: Instagram reel URL
        format_name: Name for this format template
        output_base_dir: Base directory for storing format assets
        api_key_manager: Optional API key manager for Gemini

    Returns:
        Dict with format template data ready to save to DB:
        {
            "format_name": str,
            "instagram_url": str,
            "audio_path": str,
            "total_duration": float,
            "clips": [{"index": 0, "duration": 2.5, "type": "before", "text_position": "bottom"}, ...]
        }

    Raises:
        InstagramScraperError: If any step fails
    """
    # Create output directory for this format
    format_dir = os.path.join(output_base_dir, format_name.replace(' ', '_'))
    os.makedirs(format_dir, exist_ok=True)

    # Step 1: Download reel
    video_path = download_reel(url, format_dir)

    # Step 2: Get duration
    total_duration = get_video_duration(video_path)
    if total_duration <= 0:
        raise InstagramScraperError("Could not determine video duration")

    logger.info(f"Video duration: {total_duration:.2f}s")

    # Step 3: Extract audio
    audio_path = os.path.join(format_dir, 'audio.mp3')
    extract_audio(video_path, audio_path)

    # Step 4: Detect scene cuts
    cuts = detect_scene_cuts(video_path)

    # Step 5: Extract screenshots for analysis
    screenshots = extract_clip_screenshots(video_path, cuts, total_duration)

    # Step 6: Analyze clips with Gemini
    clip_analysis = analyze_clips_with_gemini(screenshots, api_key_manager)

    # Step 7: Build clip structure
    boundaries = [0.0] + cuts + [total_duration]
    clips = []

    for i in range(len(boundaries) - 1):
        duration = round(boundaries[i + 1] - boundaries[i], 2)

        # Get Gemini analysis for this clip (if available)
        analysis = {}
        if i < len(clip_analysis):
            analysis = clip_analysis[i]

        clips.append({
            'index': i,
            'duration': duration,
            'type': analysis.get('type', 'before' if i == 0 else 'after'),
            'detected_text': analysis.get('detected_text'),
            'text_position': analysis.get('text_position', 'bottom')
        })

    # Auto-split: if only 1 clip detected, create before/after/cta structure
    if len(clips) == 1:
        d = total_duration
        clips = [
            {'index': 0, 'duration': round(d * 0.40, 2), 'type': 'before',
             'detected_text': None, 'text_position': 'bottom'},
            {'index': 1, 'duration': round(d * 0.40, 2), 'type': 'after',
             'detected_text': None, 'text_position': 'bottom'},
            {'index': 2, 'duration': round(d * 0.20, 2), 'type': 'cta',
             'detected_text': None, 'text_position': 'center'},
        ]
        logger.info(f"Auto-split single clip into before/after/cta: {[c['duration'] for c in clips]}")

    # Clean up downloaded video (keep only audio)
    try:
        os.remove(video_path)
    except Exception:
        pass

    # Clean up screenshots
    for s in screenshots:
        try:
            os.remove(s)
        except Exception:
            pass

    template = {
        'format_name': format_name,
        'instagram_url': url,
        'audio_path': audio_path,
        'total_duration': total_duration,
        'clips': clips
    }

    logger.info(f"Format template created: {format_name} ({len(clips)} clips, {total_duration:.1f}s)")
    return template


def create_format_from_upload(video_path: str, format_name: str, output_base_dir: str, api_key_manager=None) -> dict:
    """
    Create format template from an uploaded video file (skips yt-dlp download).
    Same pipeline as scrape_and_create_format but starts from a local file.
    """
    format_dir = os.path.join(output_base_dir, format_name.replace(' ', '_'))
    os.makedirs(format_dir, exist_ok=True)

    # Copy uploaded video to format dir
    import shutil
    dest_video = os.path.join(format_dir, os.path.basename(video_path))
    if os.path.abspath(video_path) != os.path.abspath(dest_video):
        shutil.copy2(video_path, dest_video)
    video_path = dest_video

    total_duration = get_video_duration(video_path)
    if total_duration <= 0:
        raise InstagramScraperError("Could not determine video duration")

    logger.info(f"Uploaded video duration: {total_duration:.2f}s")

    # Extract audio
    audio_path = os.path.join(format_dir, 'audio.mp3')
    try:
        extract_audio(video_path, audio_path)
    except InstagramScraperError:
        logger.warning("No audio in uploaded video - format will have no audio")
        audio_path = None

    # Detect scene cuts
    cuts = detect_scene_cuts(video_path)

    # Extract screenshots for analysis
    screenshots = extract_clip_screenshots(video_path, cuts, total_duration)

    # Analyze clips with Gemini
    clip_analysis = analyze_clips_with_gemini(screenshots, api_key_manager)

    # Build clip structure
    boundaries = [0.0] + cuts + [total_duration]
    clips = []
    for i in range(len(boundaries) - 1):
        duration = round(boundaries[i + 1] - boundaries[i], 2)
        analysis = clip_analysis[i] if i < len(clip_analysis) else {}
        clips.append({
            'index': i,
            'duration': duration,
            'type': analysis.get('type', 'before' if i == 0 else 'after'),
            'detected_text': analysis.get('detected_text'),
            'text_position': analysis.get('text_position', 'bottom')
        })

    # Auto-split: if only 1 clip detected, create before/after/cta structure
    if len(clips) == 1:
        d = total_duration
        clips = [
            {'index': 0, 'duration': round(d * 0.40, 2), 'type': 'before',
             'detected_text': None, 'text_position': 'bottom'},
            {'index': 1, 'duration': round(d * 0.40, 2), 'type': 'after',
             'detected_text': None, 'text_position': 'bottom'},
            {'index': 2, 'duration': round(d * 0.20, 2), 'type': 'cta',
             'detected_text': None, 'text_position': 'center'},
        ]
        logger.info(f"Auto-split single clip into before/after/cta: {[c['duration'] for c in clips]}")

    # Clean up video and screenshots
    try:
        os.remove(video_path)
    except Exception:
        pass
    for s in screenshots:
        try:
            os.remove(s)
        except Exception:
            pass

    template = {
        'format_name': format_name,
        'instagram_url': 'uploaded',
        'audio_path': audio_path,
        'total_duration': total_duration,
        'clips': clips
    }

    logger.info(f"Format from upload: {format_name} ({len(clips)} clips, {total_duration:.1f}s)")
    return template
