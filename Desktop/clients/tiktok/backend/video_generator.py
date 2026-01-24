"""
Video Generator Module
Creates 9:16 vertical videos from slideshow images + audio using FFmpeg
"""
import subprocess
import os
import tempfile
import shutil
from typing import List, Optional

from logging_config import get_logger

logger = get_logger('video_generator')


class VideoGeneratorError(Exception):
    """Exception raised for video generation errors"""
    pass


def check_ffmpeg_available() -> bool:
    """Check if ffmpeg is installed and available."""
    try:
        result = subprocess.run(
            ['ffmpeg', '-version'],
            capture_output=True,
            text=True
        )
        return result.returncode == 0
    except FileNotFoundError:
        return False


def get_audio_duration(audio_path: str) -> float:
    """
    Get duration of audio file in seconds using ffprobe.

    Args:
        audio_path: Path to audio file

    Returns:
        Duration in seconds
    """
    try:
        result = subprocess.run(
            [
                'ffprobe',
                '-v', 'error',
                '-show_entries', 'format=duration',
                '-of', 'default=noprint_wrappers=1:nokey=1',
                audio_path
            ],
            capture_output=True,
            text=True
        )
        if result.returncode == 0:
            return float(result.stdout.strip())
    except Exception as e:
        logger.warning(f"Could not get audio duration: {e}")

    return 0.0


def create_concat_file(image_paths: List[str], duration: float, output_dir: str) -> str:
    """
    Create FFmpeg concat demuxer file with image durations.

    Args:
        image_paths: List of image file paths in order
        duration: Duration per image in seconds
        output_dir: Directory to create concat file in

    Returns:
        Path to concat file
    """
    concat_path = os.path.join(output_dir, 'concat.txt')

    with open(concat_path, 'w') as f:
        for img_path in image_paths:
            # Use absolute path to avoid issues with FFmpeg working directory
            abs_path = os.path.abspath(img_path)
            # Escape single quotes in paths
            escaped_path = abs_path.replace("'", "'\\''")
            f.write(f"file '{escaped_path}'\n")
            f.write(f"duration {duration}\n")

        # FFmpeg concat demuxer quirk: repeat last image to avoid cut-off
        # Must include duration to prevent last slide from playing indefinitely
        if image_paths:
            abs_path = os.path.abspath(image_paths[-1])
            escaped_path = abs_path.replace("'", "'\\''")
            f.write(f"file '{escaped_path}'\n")
            f.write(f"duration 0.001\n")  # Minimal duration for FFmpeg quirk

    return concat_path


def create_video(
    image_paths: List[str],
    audio_path: Optional[str],
    output_path: str,
    slide_duration: float = 3.0,
    request_id: str = ""
) -> str:
    """
    Create 9:16 vertical video from images + audio using FFmpeg.

    - Images are scaled to 1080x1440 (3:4) and centered in 1080x1920 (9:16) frame
    - Black letterbox bars added top and bottom
    - Each image displays for slide_duration seconds
    - If audio provided, video length matches audio (or shorter)

    Args:
        image_paths: List of image file paths in slide order
        audio_path: Path to audio file (MP3), or None for silent video
        output_path: Output video file path (.mp4)
        slide_duration: Seconds per slide (default 3.0)
        request_id: Request ID for logging

    Returns:
        Path to created video file

    Raises:
        VideoGeneratorError: If video creation fails
    """
    log_prefix = f"[{request_id}] " if request_id else ""

    if not image_paths:
        raise VideoGeneratorError("No images provided")

    if not check_ffmpeg_available():
        raise VideoGeneratorError("FFmpeg is not installed or not in PATH")

    logger.info(f"{log_prefix}Creating video from {len(image_paths)} images")

    # Create temp directory for working files
    work_dir = tempfile.mkdtemp(prefix='video_gen_')

    try:
        # Fixed 3 seconds per slide (audio will be trimmed to match video length)
        if audio_path and os.path.exists(audio_path):
            logger.info(f"{log_prefix}Using fixed {slide_duration}s per slide (audio will be trimmed)")
        else:
            logger.info(f"{log_prefix}No audio provided, using {slide_duration}s per slide")
            audio_path = None

        # Create concat file
        concat_path = create_concat_file(image_paths, slide_duration, work_dir)

        # Build FFmpeg command
        # Video filter: scale to 3:4, pad to 9:16 with black bars centered
        video_filter = "scale=1080:1440:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black"

        cmd = [
            'ffmpeg',
            '-y',  # Overwrite output
            '-f', 'concat',
            '-safe', '0',
            '-i', concat_path,
        ]

        # Add audio input if available
        if audio_path:
            cmd.extend(['-i', audio_path])

        # Video encoding settings
        cmd.extend([
            '-vf', video_filter,
            '-c:v', 'libx264',
            '-preset', 'medium',
            '-crf', '23',
            '-pix_fmt', 'yuv420p',
        ])

        # Audio settings
        if audio_path:
            cmd.extend([
                '-c:a', 'aac',
                '-b:a', '128k',
                '-shortest',  # End when shortest stream ends
            ])

        # Output optimization
        cmd.extend([
            '-movflags', '+faststart',  # Web-optimized
            output_path
        ])

        logger.debug(f"{log_prefix}Running FFmpeg: {' '.join(cmd)}")

        # Execute FFmpeg
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300  # 5 minute timeout
        )

        if result.returncode != 0:
            error_msg = result.stderr[-500:] if len(result.stderr) > 500 else result.stderr
            logger.error(f"{log_prefix}FFmpeg failed: {error_msg}")
            raise VideoGeneratorError(f"FFmpeg failed: {error_msg}")

        # Verify output exists and has content
        if not os.path.exists(output_path):
            raise VideoGeneratorError("Output video file was not created")

        output_size = os.path.getsize(output_path)
        if output_size < 1000:  # Less than 1KB is suspicious
            raise VideoGeneratorError(f"Output video suspiciously small: {output_size} bytes")

        logger.info(f"{log_prefix}Video created successfully: {output_path} ({output_size / 1024 / 1024:.1f}MB)")

        return output_path

    finally:
        # Cleanup temp directory
        try:
            shutil.rmtree(work_dir)
        except Exception as e:
            logger.warning(f"{log_prefix}Failed to cleanup temp dir: {e}")


def create_videos_for_variations(
    generated_images: List[str],
    audio_path: Optional[str],
    output_dir: str,
    request_id: str = ""
) -> List[str]:
    """
    Create videos for each variation set from generated slideshow images.

    Identifies unique variation sets (e.g., p1_t1, p1_t2, p2_t1) and creates
    one video per set.

    Args:
        generated_images: List of all generated image paths
        audio_path: Path to audio file
        output_dir: Directory for output videos
        request_id: Request ID for logging

    Returns:
        List of created video file paths
    """
    log_prefix = f"[{request_id}] " if request_id else ""

    # Group images by variation key
    # Naming convention: {slide_type}_p{photo}_t{text}.jpg
    # e.g., hook_p1_t1.jpg, body_1_p1_t1.jpg, product_p1_t1.jpg

    variation_groups = {}

    for img_path in generated_images:
        filename = os.path.basename(img_path)

        # Extract variation key (e.g., "p1_t1")
        # Look for pattern like _p1_t1 or _p2_t2 at end of filename
        import re
        match = re.search(r'_p(\d+)_t(\d+)\.(jpg|png)$', filename, re.IGNORECASE)

        if match:
            var_key = f"p{match.group(1)}_t{match.group(2)}"
            if var_key not in variation_groups:
                variation_groups[var_key] = []
            variation_groups[var_key].append(img_path)

    if not variation_groups:
        logger.warning(f"{log_prefix}No variation patterns found in images")
        # Fallback: create single video from all images
        variation_groups['default'] = generated_images

    logger.info(f"{log_prefix}Found {len(variation_groups)} variation sets")

    # Sort images: hook → body slides (except last) → product → last body slide
    def sort_slides_for_video(images: List[str]) -> List[str]:
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

    # Create video for each variation
    video_paths = []

    for var_key, images in sorted(variation_groups.items()):
        # Sort images in slide order
        sorted_images = sort_slides_for_video(images)

        # Output path
        video_filename = f"slideshow_{var_key}.mp4"
        video_path = os.path.join(output_dir, video_filename)

        try:
            logger.info(f"{log_prefix}Creating video for variation {var_key} ({len(sorted_images)} slides)")
            create_video(
                image_paths=sorted_images,
                audio_path=audio_path,
                output_path=video_path,
                request_id=request_id
            )
            video_paths.append(video_path)
        except VideoGeneratorError as e:
            logger.error(f"{log_prefix}Failed to create video for {var_key}: {e}")
            # Continue with other variations

    return video_paths


# Test function
if __name__ == '__main__':
    # Quick test
    print("FFmpeg available:", check_ffmpeg_available())
