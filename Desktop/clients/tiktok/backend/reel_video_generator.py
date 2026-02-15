"""
Reel Video Generator

Assembles Instagram reel videos from photo/video assets + text overlays + audio.
Uses FFmpeg for all video operations.
"""
import itertools
import os
import random
import re
import shutil
import subprocess
import tempfile
import uuid
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

from logging_config import get_logger

logger = get_logger('reel_video_generator')

# Output dimensions (9:16 vertical)
OUTPUT_WIDTH = 1080
OUTPUT_HEIGHT = 1920


class ReelVideoError(Exception):
    """Exception raised for reel video generation errors."""
    pass


# ============ Text Rendering ============

def _get_font(size: int) -> ImageFont.FreeTypeFont:
    """Get a font, falling back to default if not available."""
    font_paths = [
        '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
        '/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf',
        '/System/Library/Fonts/Helvetica.ttc',
        '/usr/share/fonts/TTF/DejaVuSans-Bold.ttf',
    ]
    for path in font_paths:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


# Lazy-loaded emoji font
_emoji_font_cache = None
_emoji_font_loaded = False


def _get_emoji_font():
    """Lazy-load emoji font for Apple-style emoji rendering."""
    global _emoji_font_cache, _emoji_font_loaded
    if not _emoji_font_loaded:
        try:
            from text_renderer import load_emoji_font
            _emoji_font_cache = load_emoji_font()
            if _emoji_font_cache:
                logger.info("Loaded emoji font for IG Reel text rendering")
            else:
                logger.warning("No emoji font available — emoji will be skipped")
        except Exception as e:
            logger.warning(f"Could not load emoji font: {e}")
            _emoji_font_cache = None
        _emoji_font_loaded = True
    return _emoji_font_cache


def _render_text_overlay(
    text: str,
    style: str,
    width: int = OUTPUT_WIDTH,
    height: int = OUTPUT_HEIGHT,
    position: str = 'bottom'
) -> Image.Image:
    """
    Render text overlay as transparent RGBA image with Apple emoji support.

    Used by both photo clips (PIL composite) and video clips (FFmpeg overlay).

    Args:
        text: Text to render (may contain emoji)
        style: 'hook' (white on black semi-transparent) or 'cta' (black on white)
        width: Canvas width
        height: Canvas height
        position: 'top', 'center', or 'bottom'

    Returns:
        RGBA PIL Image with text on transparent background
    """
    from text_renderer import (
        render_text_with_emojis, wrap_text,
        get_text_width_with_emojis
    )

    overlay = Image.new('RGBA', (width, height), (0, 0, 0, 0))

    if not text or not text.strip():
        return overlay

    draw = ImageDraw.Draw(overlay)

    font_size = int(height * 0.035)
    font = _get_font(font_size)
    emoji_font = _get_emoji_font()
    target_emoji_size = font_size

    # Word wrap with emoji-aware width calculation
    max_width = int(width * 0.85)
    lines = wrap_text(text, font, max_width, emoji_font=emoji_font, font_size=target_emoji_size)

    # Measure text block
    line_spacing = 8
    line_height = font_size + line_spacing
    text_h = len(lines) * line_height - line_spacing

    # Calculate widest line
    temp_img = Image.new('RGB', (1, 1))
    temp_draw = ImageDraw.Draw(temp_img)
    text_w = 0
    for line in lines:
        lw = get_text_width_with_emojis(line, font, emoji_font, temp_draw, target_emoji_size)
        text_w = max(text_w, lw)

    padding_x = 30
    padding_y = 20
    bg_w = text_w + padding_x * 2
    bg_h = text_h + padding_y * 2
    bg_x = (width - bg_w) // 2

    if position == 'top':
        bg_y = int(height * 0.12)
    elif position == 'center':
        bg_y = (height - bg_h) // 2
    else:  # bottom
        bg_y = int(height * 0.75) + 40

    # Draw background
    if style == 'hook':
        bg_color = (0, 0, 0, 180)
        text_color = (255, 255, 255, 255)
    else:  # cta
        bg_color = (255, 255, 255, 240)
        text_color = (0, 0, 0, 255)

    draw.rounded_rectangle(
        [bg_x, bg_y, bg_x + bg_w, bg_y + bg_h],
        radius=12,
        fill=bg_color
    )

    # Draw text line by line with emoji support
    y_cursor = bg_y + padding_y
    for line in lines:
        line_w = get_text_width_with_emojis(line, font, emoji_font, temp_draw, target_emoji_size)
        line_x = bg_x + padding_x + (text_w - line_w) // 2

        render_text_with_emojis(
            draw, line, (line_x, y_cursor),
            font, emoji_font, text_color,
            target_image=overlay,
            target_emoji_size=target_emoji_size
        )
        y_cursor += line_height

    return overlay


def render_text_on_image(
    image_path: str,
    text: str,
    style: str,
    output_path: str,
    position: str = 'bottom'
) -> str:
    """
    Render text overlay on an image using Pillow with Apple emoji support.

    Args:
        image_path: Path to source image (1080x1920)
        text: Text to render (may contain emoji)
        style: 'hook' (white on black semi-transparent) or 'cta' (black on white)
        output_path: Path for output image
        position: 'top', 'center', or 'bottom'

    Returns:
        Path to output image
    """
    img = Image.open(image_path).convert('RGBA')
    overlay = _render_text_overlay(text, style, img.width, img.height, position)
    result = Image.alpha_composite(img, overlay)
    result.convert('RGB').save(output_path, quality=95)
    return output_path


def _wrap_text(draw, text: str, font, max_width: int) -> list[str]:
    """Word-wrap text to fit within max_width."""
    words = text.split()
    lines = []
    current_line = []

    for word in words:
        test_line = ' '.join(current_line + [word])
        bbox = draw.textbbox((0, 0), test_line, font=font)
        if bbox[2] - bbox[0] <= max_width:
            current_line.append(word)
        else:
            if current_line:
                lines.append(' '.join(current_line))
            current_line = [word]

    if current_line:
        lines.append(' '.join(current_line))

    return lines if lines else [text]


# ============ Asset Preparation ============

def prepare_photo_clip(
    photo_path: str,
    duration: float,
    output_path: str,
    text: str = None,
    text_style: str = None,
    text_position: str = 'bottom'
) -> str:
    """
    Convert a photo to a video clip with blurred background padding for 9:16.

    Args:
        photo_path: Path to source photo
        duration: Clip duration in seconds
        output_path: Output video path
        text: Optional text overlay
        text_style: 'hook' or 'cta'
        text_position: 'top', 'center', or 'bottom'

    Returns:
        Path to output video clip
    """
    work_dir = tempfile.mkdtemp(prefix='photo_clip_')

    try:
        # If text needs to be rendered, do it on the image first
        prepared_image = photo_path
        if text and text_style:
            # First scale the photo to 1080x1920 with blurred bg
            scaled_path = os.path.join(work_dir, 'scaled.png')
            _scale_image_with_blur_bg(photo_path, scaled_path)
            prepared_image = os.path.join(work_dir, 'with_text.png')
            render_text_on_image(scaled_path, text, text_style, prepared_image, text_position)
        else:
            # Just scale
            prepared_image = os.path.join(work_dir, 'scaled.png')
            _scale_image_with_blur_bg(photo_path, prepared_image)

        # Convert image to video clip
        cmd = [
            'ffmpeg', '-y',
            '-loop', '1',
            '-i', prepared_image,
            '-t', str(duration),
            '-vf', f'scale={OUTPUT_WIDTH}:{OUTPUT_HEIGHT}:force_original_aspect_ratio=decrease,pad={OUTPUT_WIDTH}:{OUTPUT_HEIGHT}:(ow-iw)/2:(oh-ih)/2:black',
            '-c:v', 'libx264',
            '-preset', 'medium',
            '-crf', '23',
            '-pix_fmt', 'yuv420p',
            '-r', '30',
            output_path
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            raise ReelVideoError(f"Photo clip creation failed: {result.stderr[-200:]}")

        return output_path

    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def _scale_image_with_blur_bg(input_path: str, output_path: str):
    """
    Scale image to 1080x1920 with blurred background padding.
    Image is centered at full height, empty space filled with blurred version.
    """
    img = Image.open(input_path).convert('RGB')
    w, h = img.size

    # Target dimensions
    tw, th = OUTPUT_WIDTH, OUTPUT_HEIGHT
    target_ratio = tw / th
    img_ratio = w / h

    if abs(img_ratio - target_ratio) < 0.01:
        # Already correct aspect ratio, just resize
        img = img.resize((tw, th), Image.LANCZOS)
        img.save(output_path, quality=95)
        return

    # Create blurred background
    bg = img.resize((tw, th), Image.LANCZOS)
    from PIL import ImageFilter
    bg = bg.filter(ImageFilter.GaussianBlur(radius=30))

    # Scale image to fit within frame (maintain aspect ratio)
    if img_ratio > target_ratio:
        # Wider than target - fit to width
        new_w = tw
        new_h = int(tw / img_ratio)
    else:
        # Taller than target - fit to height
        new_h = th
        new_w = int(th * img_ratio)

    img_resized = img.resize((new_w, new_h), Image.LANCZOS)

    # Center on blurred background
    x_offset = (tw - new_w) // 2
    y_offset = (th - new_h) // 2
    bg.paste(img_resized, (x_offset, y_offset))

    bg.save(output_path, quality=95)


def prepare_video_clip(
    video_path: str,
    target_duration: float,
    output_path: str,
    text: str = None,
    text_style: str = None,
    text_position: str = 'bottom'
) -> str:
    """
    Scale and trim/loop a video clip to target duration and 9:16 aspect ratio.

    Text is rendered as a transparent PNG overlay (with Apple emoji support)
    and composited via FFmpeg overlay filter.

    Args:
        video_path: Path to source video
        target_duration: Target clip duration
        output_path: Output video path
        text: Optional text overlay (may contain emoji)
        text_style: 'hook' or 'cta'
        text_position: 'top', 'center', or 'bottom'

    Returns:
        Path to output video clip
    """
    scale_chain = (
        f'scale={OUTPUT_WIDTH}:{OUTPUT_HEIGHT}:force_original_aspect_ratio=decrease,'
        f'pad={OUTPUT_WIDTH}:{OUTPUT_HEIGHT}:(ow-iw)/2:(oh-ih)/2:black'
    )

    overlay_path = None
    try:
        if text and text_style:
            # Render text overlay as transparent PNG (with emoji support)
            overlay_img = _render_text_overlay(text, text_style, position=text_position)
            overlay_path = os.path.join(
                tempfile.gettempdir(), f'text_overlay_{uuid.uuid4().hex[:8]}.png'
            )
            overlay_img.save(overlay_path, 'PNG')

            cmd = [
                'ffmpeg', '-y',
                '-stream_loop', '-1',
                '-i', video_path,
                '-i', overlay_path,
                '-t', str(target_duration),
                '-filter_complex',
                f'[0:v]{scale_chain}[bg];[bg][1:v]overlay=0:0',
                '-c:v', 'libx264',
                '-preset', 'medium',
                '-crf', '23',
                '-pix_fmt', 'yuv420p',
                '-an',
                '-r', '30',
                output_path
            ]
        else:
            cmd = [
                'ffmpeg', '-y',
                '-stream_loop', '-1',
                '-i', video_path,
                '-t', str(target_duration),
                '-vf', scale_chain,
                '-c:v', 'libx264',
                '-preset', 'medium',
                '-crf', '23',
                '-pix_fmt', 'yuv420p',
                '-an',
                '-r', '30',
                output_path
            ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            raise ReelVideoError(f"Video clip preparation failed: {result.stderr[-200:]}")

        return output_path
    finally:
        if overlay_path and os.path.exists(overlay_path):
            os.remove(overlay_path)


# ============ Video Assembly ============

def assemble_reel_video(
    clips_config: list[dict],
    audio_path: str,
    output_path: str,
    request_id: str = ""
) -> str:
    """
    Assemble a complete reel video from clips + audio.

    Args:
        clips_config: List of clip configs:
            [{"asset_path": str, "asset_type": "photo"|"video", "duration": float,
              "text": str|None, "text_style": "hook"|"cta"|None, "text_position": str}]
        audio_path: Path to audio file
        output_path: Output video file path
        request_id: For logging

    Returns:
        Path to assembled video

    Raises:
        ReelVideoError: If assembly fails
    """
    log_prefix = f"[{request_id}] " if request_id else ""
    work_dir = tempfile.mkdtemp(prefix='reel_assembly_')

    try:
        clip_paths = []

        # Step 1: Prepare each clip
        for i, clip in enumerate(clips_config):
            clip_output = os.path.join(work_dir, f'clip_{i:02d}.mp4')

            asset_path = clip['asset_path']
            duration = clip['duration']
            text = clip.get('text')
            text_style = clip.get('text_style')
            text_position = clip.get('text_position', 'bottom')

            if clip.get('asset_type') == 'video':
                prepare_video_clip(
                    asset_path, duration, clip_output,
                    text, text_style, text_position
                )
            else:
                prepare_photo_clip(
                    asset_path, duration, clip_output,
                    text, text_style, text_position
                )

            clip_paths.append(clip_output)
            logger.debug(f"{log_prefix}Prepared clip {i}: {duration}s")

        # Step 2: Create concat file
        concat_path = os.path.join(work_dir, 'concat.txt')
        with open(concat_path, 'w') as f:
            for cp in clip_paths:
                f.write(f"file '{os.path.abspath(cp)}'\n")

        # Step 3: Concatenate clips + add audio
        total_duration = sum(c['duration'] for c in clips_config)

        cmd = [
            'ffmpeg', '-y',
            '-f', 'concat',
            '-safe', '0',
            '-i', concat_path,
        ]

        if audio_path and os.path.exists(audio_path):
            cmd.extend(['-i', audio_path])

        cmd.extend(['-t', str(total_duration)])

        cmd.extend([
            '-c:v', 'libx264',
            '-preset', 'medium',
            '-crf', '23',
            '-pix_fmt', 'yuv420p',
        ])

        if audio_path and os.path.exists(audio_path):
            cmd.extend([
                '-c:a', 'aac',
                '-b:a', '192k',
                '-shortest',
            ])

        cmd.extend([
            '-movflags', '+faststart',
            output_path
        ])

        logger.info(f"{log_prefix}Assembling reel: {len(clips_config)} clips, {total_duration:.1f}s")

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            raise ReelVideoError(f"Video assembly failed: {result.stderr[-300:]}")

        if not os.path.exists(output_path) or os.path.getsize(output_path) < 1000:
            raise ReelVideoError("Output video is empty or too small")

        size_mb = os.path.getsize(output_path) / 1024 / 1024
        logger.info(f"{log_prefix}Reel assembled: {output_path} ({size_mb:.1f}MB)")

        return output_path

    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


# ============ Combination Generator ============

def generate_combinations(
    format_clips: list[dict],
    characters: list[dict],
    text_variations: list[str],
    cta_variations: list[str],
    num_videos: int,
    asset_type: str = 'photos'
) -> list[dict]:
    """
    Generate unique video combinations from format template + assets + text variations.

    Args:
        format_clips: Clip definitions from format template
        characters: List of character dicts with their assets:
            [{"id": str, "before_photos": [asset_dicts], "after_photos": [...],
              "before_videos": [...], "after_videos": [...]}]
        text_variations: List of hook text variations
        cta_variations: List of CTA text variations
        num_videos: Number of videos to generate
        asset_type: 'photos', 'videos', or 'both'

    Returns:
        List of video config dicts ready for assembly
    """
    if not characters:
        raise ReelVideoError("No characters with assets provided")
    if not text_variations:
        text_variations = ['']
    if not cta_variations:
        cta_variations = ['']

    all_combos = []

    for character in characters:
        char_id = character['id']

        # Get available assets based on type filter
        before_assets = []
        after_assets = []

        if asset_type in ('photos', 'both'):
            before_assets.extend(character.get('before_photos', []))
            after_assets.extend(character.get('after_photos', []))
        if asset_type in ('videos', 'both'):
            before_assets.extend(character.get('before_videos', []))
            after_assets.extend(character.get('after_videos', []))

        if not before_assets or not after_assets:
            logger.warning(f"Character {char_id} has no {asset_type} assets, skipping")
            continue

        # Generate all possible combos for this character
        for before_asset, after_asset, hook_idx, cta_idx in itertools.product(
            before_assets, after_assets,
            range(len(text_variations)), range(len(cta_variations))
        ):
            combo = {
                'character_id': char_id,
                'before_asset': before_asset,
                'after_asset': after_asset,
                'text_variation_index': hook_idx,
                'hook_text': text_variations[hook_idx],
                'cta_text': cta_variations[cta_idx],
            }
            all_combos.append(combo)

    if not all_combos:
        raise ReelVideoError("No valid combinations could be generated")

    # Deduplicate by what's actually visible (assets used by clip types + text)
    clip_types_used = set(c.get('type', 'before') for c in format_clips)
    seen = set()
    unique_combos = []
    for combo in all_combos:
        # Build a key from only the assets/text that will appear in the video
        key_parts = [combo['character_id'], combo['hook_text'], combo['cta_text']]
        if 'before' in clip_types_used or 'transition' in clip_types_used:
            key_parts.append(combo['before_asset'].get('id', ''))
        if 'after' in clip_types_used or 'cta' in clip_types_used:
            key_parts.append(combo['after_asset'].get('id', ''))
        key = tuple(key_parts)
        if key not in seen:
            seen.add(key)
            unique_combos.append(combo)

    # Shuffle and limit to requested number
    random.shuffle(unique_combos)

    if len(unique_combos) < num_videos:
        logger.warning(f"Only {len(unique_combos)} unique combos available (requested {num_videos})")

    selected = unique_combos[:num_videos]

    # Build full video configs with clip assignments
    video_configs = []
    for i, combo in enumerate(selected):
        clips_config = []
        for clip_def in format_clips:
            clip_type = clip_def.get('type', 'before')

            if clip_type == 'before':
                asset = combo['before_asset']
                text = combo['hook_text']
                text_style = 'hook'
            elif clip_type == 'after':
                asset = combo['after_asset']
                text = None
                text_style = None
            elif clip_type == 'cta':
                asset = combo['after_asset']  # Use after asset as bg for CTA
                text = combo['cta_text']
                text_style = 'cta'
            else:  # transition or unknown
                asset = combo['before_asset']
                text = None
                text_style = None

            # Determine if asset is photo or video
            asset_path = asset.get('file_path', '')
            is_video = asset_path.lower().endswith(('.mp4', '.mov', '.avi', '.webm'))

            clips_config.append({
                'asset_path': asset_path,
                'asset_type': 'video' if is_video else 'photo',
                'duration': clip_def['duration'],
                'text': text,
                'text_style': text_style,
                'text_position': clip_def.get('text_position', 'bottom'),
            })

        video_configs.append({
            'video_number': i + 1,
            'character_id': combo['character_id'],
            'before_asset_id': combo['before_asset'].get('id'),
            'after_asset_id': combo['after_asset'].get('id'),
            'text_variation_index': combo['text_variation_index'],
            'clips_config': clips_config,
        })

    logger.info(f"Generated {len(video_configs)} video combinations")
    return video_configs
