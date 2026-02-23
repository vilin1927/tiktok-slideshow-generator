"""
Celery Tasks for Instagram Reel Generator

Handles batch video generation: text variations, combination generation,
video assembly, and Google Drive upload.
"""
import json
import os
import shutil
import tempfile
from datetime import datetime

from celery_app import celery_app
from database import (
    get_ig_job, get_ig_format, get_ig_asset,
    get_ig_assets_by_character, get_ig_character,
    update_ig_job_status, increment_ig_job_counter,
    create_ig_video, update_ig_video_status,
)
from google_drive import (
    create_folder, upload_file, set_folder_public, get_folder_link,
    GoogleDriveError
)
from reel_video_generator import (
    assemble_reel_video, generate_combinations, ReelVideoError
)
from text_variation_service import generate_text_variations
from image_transforms import transform_single_image_file

from logging_config import get_logger

logger = get_logger('ig_reel_tasks')

# Output directory
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), 'temp', 'ig_output')
os.makedirs(OUTPUT_DIR, exist_ok=True)


@celery_app.task(bind=True, max_retries=0)
def generate_reel_batch(self, job_id: str):
    """
    Orchestrate a batch of reel video generations.

    Steps:
    1. Load job config from DB
    2. Generate text variations (Claude)
    3. Generate combinations
    4. Create Google Drive folder
    5. Process each video sequentially (to avoid overloading VPS)
    6. Finalize job
    """
    logger.info(f"[Job {job_id[:8]}] Starting reel batch generation")

    job = get_ig_job(job_id)
    if not job:
        logger.error(f"[Job {job_id[:8]}] Job not found")
        return

    try:
        update_ig_job_status(job_id, 'processing', celery_task_id=self.request.id)

        # Step 1: Load format template
        fmt = get_ig_format(job['format_id'])
        if not fmt:
            raise ReelVideoError("Format template not found")

        clips = fmt.get('clips', [])
        if not clips:
            raise ReelVideoError("Format template has no clips")

        logger.info(f"[Job {job_id[:8]}] Format: {fmt['format_name']} ({len(clips)} clips)")

        # Step 2: Generate text variations (per-clip or legacy)
        hook_text = job.get('hook_text', '')
        cta_text = job.get('cta_text', '')
        num_text_variations = job.get('num_text_variations', 1)

        # Check for per-clip text config
        clip_texts_raw = job.get('clip_texts') or None
        if clip_texts_raw is None and job.get('clip_texts_json'):
            try:
                clip_texts_raw = json.loads(job['clip_texts_json'])
            except Exception as e:
                logger.warning(f"[Job {job_id[:8]}] Failed to parse clip_texts_json: {e}")

        clip_variations = None
        clip_texts = None

        if clip_texts_raw and isinstance(clip_texts_raw, list):
            # New per-clip mode: generate variations for each clip that has text
            clip_texts = clip_texts_raw
            clip_variations = []
            for ct in clip_texts:
                base_text = ct.get('text', '').strip()
                style = ct.get('style', 'hook')
                if base_text:
                    vars_list = generate_text_variations(base_text, num_text_variations, style)
                    clip_variations.append(vars_list)
                else:
                    clip_variations.append([''] * max(1, num_text_variations))

            all_variations = {'clip_texts': clip_texts, 'clip_variations': clip_variations}
            logger.info(f"[Job {job_id[:8]}] Per-clip text: {len(clip_texts)} clips, {num_text_variations} variations each")
        else:
            # Legacy mode: hook + cta
            hook_variations = generate_text_variations(hook_text, num_text_variations, 'hook')
            cta_variations = generate_text_variations(cta_text, max(1, num_text_variations), 'cta') if cta_text else ['']
            all_variations = {'hook': hook_variations, 'cta': cta_variations}
            logger.info(f"[Job {job_id[:8]}] Text variations: {len(hook_variations)} hooks, {len(cta_variations)} CTAs")

        # Save variations to DB
        update_ig_job_status(
            job_id, 'processing',
            text_variations_json=json.dumps(all_variations)
        )

        # Step 3: Load characters and their assets
        character_ids = []
        if job.get('character_ids_json'):
            try:
                character_ids = json.loads(job['character_ids_json'])
            except Exception as e:
                logger.warning(f"[Job {job_id[:8]}] Failed to parse character_ids_json: {e}")

        if not character_ids:
            raise ReelVideoError("No characters selected")

        characters = []
        for char_id in character_ids:
            char = get_ig_character(char_id)
            if not char:
                continue

            assets = get_ig_assets_by_character(char_id)
            char_data = {
                'id': char_id,
                'name': char['character_name'],
                'before_photos': [a for a in assets if a['asset_type'] == 'before_photo'],
                'after_photos': [a for a in assets if a['asset_type'] == 'after_photo'],
                'before_videos': [a for a in assets if a['asset_type'] == 'before_video'],
                'after_videos': [a for a in assets if a['asset_type'] == 'after_video'],
            }
            characters.append(char_data)

        if not characters:
            raise ReelVideoError("No valid characters with assets found")

        # Step 4: Generate combinations
        combo_kwargs = {
            'format_clips': clips,
            'characters': characters,
            'num_videos': job['num_videos'],
            'asset_type': job.get('asset_type', 'photos'),
        }
        if clip_variations is not None:
            combo_kwargs['clip_variations'] = clip_variations
            combo_kwargs['clip_texts'] = clip_texts
        else:
            combo_kwargs['text_variations'] = hook_variations
            combo_kwargs['cta_variations'] = cta_variations

        video_configs = generate_combinations(**combo_kwargs)

        logger.info(f"[Job {job_id[:8]}] Generated {len(video_configs)} video configs")

        # Step 5: Create Google Drive folder
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        folder_name = f"IG-Reel_{fmt['format_name']}_{timestamp}"

        try:
            drive_folder_id = create_folder(folder_name)
            set_folder_public(drive_folder_id)
            drive_folder_url = get_folder_link(drive_folder_id)
            update_ig_job_status(job_id, 'processing', drive_folder_url=drive_folder_url)
            logger.info(f"[Job {job_id[:8]}] Drive folder: {drive_folder_url}")
        except GoogleDriveError as e:
            raise ReelVideoError(f"Failed to create Drive folder: {e}")

        # Step 6: Create video records in DB
        video_ids = []
        for config in video_configs:
            vid = create_ig_video(
                job_id=job_id,
                video_number=config['video_number'],
                character_id=config.get('character_id'),
                before_asset_id=config.get('before_asset_id'),
                after_asset_id=config.get('after_asset_id'),
                text_variation_index=config.get('text_variation_index', 0)
            )
            video_ids.append(vid)
            config['video_id'] = vid

        # Step 7: Process each video
        job_output_dir = os.path.join(OUTPUT_DIR, job_id[:8])
        os.makedirs(job_output_dir, exist_ok=True)

        for config in video_configs:
            try:
                _process_single_video(
                    job_id=job_id,
                    video_config=config,
                    audio_path=fmt.get('audio_path'),
                    output_dir=job_output_dir,
                    drive_folder_id=drive_folder_id
                )
                increment_ig_job_counter(job_id, 'videos_completed')
            except Exception as e:
                logger.error(f"[Job {job_id[:8]}] Video {config['video_number']} failed: {e}")
                update_ig_video_status(
                    config['video_id'], 'failed',
                    error_message=str(e)[:500]
                )
                increment_ig_job_counter(job_id, 'videos_failed')

        # Step 8: Finalize
        final_job = get_ig_job(job_id)
        completed = final_job.get('videos_completed', 0)
        failed = final_job.get('videos_failed', 0)

        if completed == 0:
            update_ig_job_status(job_id, 'failed', error_message='All videos failed')
        else:
            update_ig_job_status(job_id, 'completed')

        logger.info(
            f"[Job {job_id[:8]}] Batch complete: {completed} succeeded, {failed} failed"
        )

        # Cleanup output directory
        try:
            shutil.rmtree(job_output_dir, ignore_errors=True)
        except Exception as e:
            logger.warning(f"[Job {job_id[:8]}] Failed to cleanup output dir {job_output_dir}: {e}")

    except ReelVideoError as e:
        logger.error(f"[Job {job_id[:8]}] Batch failed: {e}")
        update_ig_job_status(job_id, 'failed', error_message=str(e))
    except Exception as e:
        logger.error(f"[Job {job_id[:8]}] Unexpected error: {e}", exc_info=True)
        update_ig_job_status(job_id, 'failed', error_message=f"Unexpected error: {str(e)[:500]}")


def _process_single_video(
    job_id: str,
    video_config: dict,
    audio_path: str,
    output_dir: str,
    drive_folder_id: str
):
    """
    Process a single video: apply transforms, assemble, upload.

    Args:
        job_id: Parent job ID
        video_config: Video configuration dict with clips_config
        audio_path: Path to audio file
        output_dir: Output directory
        drive_folder_id: Google Drive folder ID
    """
    video_id = video_config['video_id']
    video_num = video_config['video_number']

    update_ig_video_status(video_id, 'processing')
    logger.info(f"[Job {job_id[:8]}] Processing video {video_num}")

    # Apply image transforms for uniqueness (photo assets only)
    # IMPORTANT: Work on copies so original assets are never modified
    variation_key = f"ig_{job_id[:8]}_{video_num}"
    clips_config = video_config['clips_config']
    transform_dir = tempfile.mkdtemp(prefix=f'ig_transform_{video_num}_')

    try:
        for i, clip in enumerate(clips_config):
            if clip['asset_type'] == 'photo' and clip['asset_path']:
                ext = os.path.splitext(clip['asset_path'])[1]
                copy_path = os.path.join(transform_dir, f'clip_{i:02d}{ext}')
                shutil.copy2(clip['asset_path'], copy_path)
                transform_single_image_file(copy_path, variation_key, i)
                clip['asset_path'] = copy_path

        # Assemble video
        output_filename = f"reel_{video_num:03d}.mp4"
        output_path = os.path.join(output_dir, output_filename)

        assemble_reel_video(
            clips_config=clips_config,
            audio_path=audio_path,
            output_path=output_path,
            request_id=f"{job_id[:8]}_v{video_num}"
        )

        # Upload to Google Drive
        try:
            file_id = upload_file(output_path, drive_folder_id)
            drive_url = f"https://drive.google.com/file/d/{file_id}/view"

            update_ig_video_status(
                video_id, 'completed',
                output_path=output_path,
                drive_url=drive_url
            )

            logger.info(f"[Job {job_id[:8]}] Video {video_num} uploaded to Drive")

        except GoogleDriveError as e:
            # Video was created but upload failed — still mark as completed with local path
            logger.warning(f"[Job {job_id[:8]}] Drive upload failed for video {video_num}: {e}")
            update_ig_video_status(
                video_id, 'completed',
                output_path=output_path,
                error_message=f"Drive upload failed: {e}"
            )

        # Cleanup local file after upload
        try:
            os.remove(output_path)
        except Exception as e:
            logger.warning(f"[Job {job_id[:8]}] Failed to cleanup output file {output_path}: {e}")

    finally:
        # Always cleanup transform temp directory
        shutil.rmtree(transform_dir, ignore_errors=True)
