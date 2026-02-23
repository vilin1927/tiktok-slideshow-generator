"""
Celery Tasks for Batch Processing
Handles batch orchestration, link processing, and variation generation
"""
import os
import re
import uuid
import json
import tempfile
import shutil
from datetime import datetime
from celery import chain, group
from celery.exceptions import MaxRetriesExceededError, SoftTimeLimitExceeded

from celery_app import celery_app
from database import (
    get_batch, update_batch_status, get_batch_links, get_batch_link,
    update_batch_link_status, get_link_variations, get_variation,
    update_variation_status, create_variation
)
from tiktok_scraper import scrape_tiktok_slideshow, TikTokScraperError
from gemini_service_v2 import run_pipeline, run_pipeline_queued, GeminiServiceError, USE_QUEUE_MODE
from google_drive import (
    create_folder, upload_file, upload_files_parallel, set_folder_public, get_folder_link,
    GoogleDriveError
)
from video_generator import create_video, create_videos_for_variations, VideoGeneratorError

from logging_config import get_logger

logger = get_logger('tasks')

# Output directory for batch processing
BATCH_OUTPUT_DIR = os.path.join(os.path.dirname(__file__), '..', 'batch_output')


@celery_app.task(bind=True, max_retries=0)
def process_batch(self, batch_id: str):
    """
    Orchestrate processing of all links in a batch.
    Creates a Google Drive folder and dispatches link processing tasks.

    Args:
        batch_id: UUID of the batch to process
    """
    logger.info(f"[Batch {batch_id[:8]}] Starting batch processing")

    try:
        # Get batch info
        batch = get_batch(batch_id)
        if not batch:
            logger.error(f"[Batch {batch_id[:8]}] Batch not found")
            return {'status': 'error', 'message': 'Batch not found'}

        # Update batch status to processing
        update_batch_status(batch_id, 'processing')

        # Create main Google Drive folder for batch
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        folder_name = f"Batch_{timestamp}_{batch_id[:8]}"

        try:
            drive_folder_id = create_folder(folder_name)
            set_folder_public(drive_folder_id)
            drive_folder_url = get_folder_link(drive_folder_id)
            update_batch_status(batch_id, 'processing', drive_folder_url=drive_folder_url)
            logger.info(f"[Batch {batch_id[:8]}] Created Drive folder: {drive_folder_url}")
        except GoogleDriveError as e:
            logger.error(f"[Batch {batch_id[:8]}] Failed to create Drive folder: {e}")
            update_batch_status(batch_id, 'failed', error_message=f"Drive folder creation failed: {e}")
            return {'status': 'error', 'message': str(e)}

        # Get all links for this batch
        links = get_batch_links(batch_id)
        if not links:
            logger.warning(f"[Batch {batch_id[:8]}] No links found")
            update_batch_status(batch_id, 'completed')
            return {'status': 'completed', 'message': 'No links to process'}

        logger.info(f"[Batch {batch_id[:8]}] Dispatching {len(links)} link tasks")

        # Dispatch link processing tasks
        # Using group() to process links in parallel (respecting rate limits via task annotations)
        link_tasks = []
        for link in links:
            task = process_link.s(link['id'], drive_folder_id)
            link_tasks.append(task)

        # Execute all link tasks as a group, then call finalize_batch
        workflow = group(link_tasks) | finalize_batch.s(batch_id)
        workflow.apply_async()

        return {
            'status': 'dispatched',
            'batch_id': batch_id,
            'links_count': len(links),
            'drive_folder_url': drive_folder_url
        }

    except Exception as e:
        logger.error(f"[Batch {batch_id[:8]}] Batch processing failed: {e}", exc_info=True)
        update_batch_status(batch_id, 'failed', error_message=str(e))
        return {'status': 'error', 'message': str(e)}


@celery_app.task(bind=True, max_retries=3, default_retry_delay=60)
def process_link(self, batch_link_id: str, parent_drive_folder_id: str):
    """
    Process a single link: scrape, run pipeline, upload to Drive.

    Args:
        batch_link_id: UUID of the batch link to process
        parent_drive_folder_id: Google Drive folder ID for the batch

    Returns:
        dict with processing result
    """
    logger.info(f"[Link {batch_link_id[:8]}] Starting link processing")

    try:
        # CRITICAL FIX: Cancel any stale queue tasks from previous retry attempts
        # When Celery retries a task, old queue tasks may still exist pointing to
        # deleted temp directories. Cancel them before submitting new tasks.
        if USE_QUEUE_MODE:
            from image_queue import get_global_queue
            queue = get_global_queue()
            cancelled = queue.cancel_job(batch_link_id)
            if cancelled.get('cancelled', 0) > 0:
                logger.info(f"[Link {batch_link_id[:8]}] Cancelled {cancelled['cancelled']} stale queue tasks from previous attempt")

        # Get link info
        link = get_batch_link(batch_link_id)
        if not link:
            logger.error(f"[Link {batch_link_id[:8]}] Link not found")
            return {'status': 'error', 'link_id': batch_link_id, 'message': 'Link not found'}

        link_url = link['link_url']
        link_index = link['link_index']
        product_description = link.get('product_description', '')
        product_photo_path = link.get('product_photo_path')

        # Update status to processing
        update_batch_link_status(batch_link_id, 'processing', celery_task_id=self.request.id)

        # Create temporary working directory
        work_dir = tempfile.mkdtemp(prefix=f"batch_link_{batch_link_id[:8]}_")
        scrape_dir = os.path.join(work_dir, 'scraped')
        output_dir = os.path.join(work_dir, 'output')
        os.makedirs(scrape_dir, exist_ok=True)
        os.makedirs(output_dir, exist_ok=True)

        try:
            # Step 1: Scrape TikTok slideshow
            logger.info(f"[Link {batch_link_id[:8]}] Scraping: {link_url[:50]}...")
            scrape_result = scrape_tiktok_slideshow(link_url, scrape_dir, request_id=batch_link_id[:8])
            slide_paths = scrape_result['images']
            logger.info(f"[Link {batch_link_id[:8]}] Scraped {len(slide_paths)} slides")

            if len(slide_paths) < 3:
                raise TikTokScraperError(f"Not enough slides: {len(slide_paths)} (need at least 3)")

            # Step 2: Validate product photo
            if not product_photo_path or not os.path.exists(product_photo_path):
                raise ValueError(f"Product photo not found: {product_photo_path}")

            # Get batch for variation settings
            batch = get_batch(link['batch_id'])

            # Parse variations config from batch
            variations_config = {}
            if batch and batch.get('variations_config'):
                try:
                    variations_config = json.loads(batch['variations_config']) if isinstance(batch['variations_config'], str) else batch['variations_config']
                except (json.JSONDecodeError, TypeError) as e:
                    logger.warning(f"[Link {batch_link_id[:8]}] Failed to parse variations_config: {e}")

            # Extract individual variation settings (default to 1)
            hook_photo_var = variations_config.get('hook_photo_var', 1)
            hook_text_var = variations_config.get('hook_text_var', 1)
            body_photo_var = variations_config.get('body_photo_var', 1)
            body_text_var = variations_config.get('body_text_var', 1)
            product_text_var = variations_config.get('product_text_var', 1)
            generate_video = variations_config.get('generate_video', False)
            preset_id = variations_config.get('preset_id', 'gemini')

            # Step 3: Run generation pipeline
            logger.info(f"[Link {batch_link_id[:8]}] Running pipeline with hook={hook_photo_var}x{hook_text_var}, body={body_photo_var}x{body_text_var}, preset={preset_id}")

            # Use queued pipeline if queue mode is enabled
            if USE_QUEUE_MODE:
                logger.info(f"[Link {batch_link_id[:8]}] Using QUEUED pipeline (global queue system)")
                pipeline_result = run_pipeline_queued(
                    slide_paths=slide_paths,
                    product_image_paths=[product_photo_path],
                    product_description=product_description,
                    output_dir=output_dir,
                    job_id=batch_link_id,  # Use link ID as job ID
                    hook_photo_var=hook_photo_var,
                    hook_text_var=hook_text_var,
                    body_photo_var=body_photo_var,
                    body_text_var=body_text_var,
                    product_text_var=product_text_var,
                    request_id=batch_link_id[:8],
                    preset_id=preset_id
                )
            else:
                logger.info(f"[Link {batch_link_id[:8]}] Using DIRECT pipeline (no queue)")
                pipeline_result = run_pipeline(
                    slide_paths=slide_paths,
                    product_image_paths=[product_photo_path],
                    product_description=product_description,
                    output_dir=output_dir,
                    hook_photo_var=hook_photo_var,
                    hook_text_var=hook_text_var,
                    body_photo_var=body_photo_var,
                    body_text_var=body_text_var,
                    product_text_var=product_text_var,
                    request_id=batch_link_id[:8],
                    preset_id=preset_id
                )

            generated_images = pipeline_result.get('generated_images', [])
            logger.info(f"[Link {batch_link_id[:8]}] Generated {len(generated_images)} images")

            # Step 4: Upload to Google Drive
            # Create subfolder for this link
            link_folder_name = f"Link_{link_index + 1}"
            link_folder_id = create_folder(link_folder_name, parent_drive_folder_id)
            set_folder_public(link_folder_id)
            link_folder_url = get_folder_link(link_folder_id)

            # Upload all generated images in parallel (5 concurrent uploads)
            uploaded_count, upload_failed = upload_files_parallel(
                generated_images,
                link_folder_id,
                max_workers=5,
                request_id=batch_link_id[:8]
            )
            logger.info(f"[Link {batch_link_id[:8]}] Uploaded {uploaded_count} images to Drive")

            # Upload audio file to Drive (so user has access to original TikTok audio)
            audio_path = scrape_result.get('audio')
            if audio_path and os.path.exists(audio_path):
                try:
                    upload_file(audio_path, link_folder_id)
                    logger.info(f"[Link {batch_link_id[:8]}] Uploaded audio to Drive")
                except Exception as e:
                    logger.warning(f"[Link {batch_link_id[:8]}] Failed to upload audio: {e}")

            # Step 5: Generate videos for each variation (same as single run)
            video_created = False
            if generate_video and generated_images:
                try:
                    audio_path = scrape_result.get('audio')
                    if audio_path and os.path.exists(audio_path):
                        logger.info(f"[Link {batch_link_id[:8]}] Creating variation videos with {len(generated_images)} images")
                        
                        # Use same video generation as single run - creates one video per variation set
                        video_paths = create_videos_for_variations(
                            generated_images=generated_images,
                            audio_path=audio_path,
                            output_dir=output_dir,
                            request_id=batch_link_id[:8]
                        )
                        
                        # Upload all videos to Drive
                        for video_path in video_paths:
                            if os.path.exists(video_path):
                                upload_file(video_path, link_folder_id)
                        
                        video_created = len(video_paths) > 0
                        logger.info(f"[Link {batch_link_id[:8]}] Created and uploaded {len(video_paths)} videos to Drive")
                    else:
                        logger.warning(f"[Link {batch_link_id[:8]}] No audio found for video generation")
                except VideoGeneratorError as e:
                    logger.error(f"[Link {batch_link_id[:8]}] Video generation failed: {e}")
                except Exception as e:
                    logger.error(f"[Link {batch_link_id[:8]}] Video error: {e}")

            # Update link status to completed
            update_batch_link_status(
                batch_link_id,
                'completed',
                drive_folder_url=link_folder_url
            )

            return {
                'status': 'completed',
                'link_id': batch_link_id,
                'images_generated': len(generated_images),
                'images_uploaded': uploaded_count,
                'video_created': video_created,
                'drive_folder_url': link_folder_url
            }

        finally:
            # Cleanup temporary directory
            try:
                shutil.rmtree(work_dir)
            except Exception as e:
                logger.warning(f"[Link {batch_link_id[:8]}] Failed to cleanup {work_dir}: {e}")

    except TikTokScraperError as e:
        logger.error(f"[Link {batch_link_id[:8]}] Scraping failed: {e}")
        update_batch_link_status(batch_link_id, 'failed', error_message=f"Scrape error: {e}")
        return {'status': 'error', 'link_id': batch_link_id, 'message': str(e)}

    except GeminiServiceError as e:
        error_msg = str(e)
        logger.error(f"[Link {batch_link_id[:8]}] Pipeline failed: {e}")

        # Retry on rate limit errors
        if 'rate' in error_msg.lower() or '429' in error_msg:
            try:
                logger.info(f"[Link {batch_link_id[:8]}] Retrying due to rate limit...")
                raise self.retry(exc=e, countdown=120)  # Wait 2 minutes before retry
            except MaxRetriesExceededError:
                logger.error(f"[Link {batch_link_id[:8]}] Max retries exceeded for rate limit")

        update_batch_link_status(batch_link_id, 'failed', error_message=f"Pipeline error: {e}")
        return {'status': 'error', 'link_id': batch_link_id, 'message': str(e)}

    except GoogleDriveError as e:
        logger.error(f"[Link {batch_link_id[:8]}] Drive upload failed: {e}")
        update_batch_link_status(batch_link_id, 'failed', error_message=f"Upload error: {e}")
        return {'status': 'error', 'link_id': batch_link_id, 'message': str(e)}

    except Exception as e:
        logger.error(f"[Link {batch_link_id[:8]}] Unexpected error: {e}", exc_info=True)
        update_batch_link_status(batch_link_id, 'failed', error_message=str(e))
        return {'status': 'error', 'link_id': batch_link_id, 'message': str(e)}


@celery_app.task(bind=True)
def finalize_batch(self, link_results: list, batch_id: str):
    """
    Called after all link tasks complete. Updates batch status.

    Args:
        link_results: List of results from process_link tasks
        batch_id: UUID of the batch
    """
    logger.info(f"[Batch {batch_id[:8]}] Finalizing batch")

    try:
        # Count results - treat None/missing results as failures
        completed = sum(1 for r in link_results if r and r.get('status') == 'completed')
        total = len(link_results)
        failed = total - completed  # Everything not completed is failed

        logger.info(f"[Batch {batch_id[:8]}] Results: {completed}/{total} completed, {failed} failed")

        # Determine final status
        if completed == 0:
            final_status = 'failed'
            error_msg = 'All links failed to process'
        elif failed > 0:
            final_status = 'completed'  # Partial success
            error_msg = f'{failed}/{total} links failed'
        else:
            final_status = 'completed'
            error_msg = None

        update_batch_status(
            batch_id,
            final_status,
            error_message=error_msg,
            completed_links=completed,
            failed_links=failed
        )

        return {
            'batch_id': batch_id,
            'status': final_status,
            'total': total,
            'completed': completed,
            'failed': failed
        }

    except Exception as e:
        logger.error(f"[Batch {batch_id[:8]}] Finalization failed: {e}", exc_info=True)
        update_batch_status(batch_id, 'failed', error_message=f"Finalization error: {e}")
        return {'status': 'error', 'message': str(e)}


@celery_app.task(bind=True, rate_limit='10/m', max_retries=3, default_retry_delay=60)
def generate_variation(self, variation_id: str, slide_paths: list, product_image_path: str,
                       product_description: str, output_dir: str, variation_num: int):
    """
    Generate a single variation. Used for fine-grained rate limiting.

    This task is rate-limited to 10/minute to respect Gemini API limits.

    Args:
        variation_id: UUID of the variation record
        slide_paths: List of scraped slide image paths
        product_image_path: Path to product image
        product_description: Product description text
        output_dir: Directory for output
        variation_num: Variation number (1-based)
    """
    logger.info(f"[Variation {variation_id[:8]}] Generating variation #{variation_num}")

    try:
        update_variation_status(variation_id, 'processing', celery_task_id=self.request.id)

        # Run pipeline for single variation
        result = run_pipeline(
            slide_paths=slide_paths,
            product_image_path=product_image_path,
            product_description=product_description,
            output_dir=output_dir,
            hook_variations=1,
            body_variations=1,
            request_id=f"var_{variation_id[:8]}"
        )

        generated_images = result.get('generated_images', [])

        update_variation_status(
            variation_id,
            'completed',
            output_path=output_dir
        )

        return {
            'status': 'completed',
            'variation_id': variation_id,
            'images': generated_images
        }

    except GeminiServiceError as e:
        error_msg = str(e)
        logger.error(f"[Variation {variation_id[:8]}] Generation failed: {e}")

        # Retry on rate limit
        if 'rate' in error_msg.lower() or '429' in error_msg:
            try:
                raise self.retry(exc=e, countdown=120)
            except MaxRetriesExceededError:
                logger.error(f"[Variation {variation_id[:8]}] Max retries exceeded for rate limit")

        update_variation_status(variation_id, 'failed', error_message=str(e))
        return {'status': 'error', 'variation_id': variation_id, 'message': str(e)}

    except Exception as e:
        logger.error(f"[Variation {variation_id[:8]}] Unexpected error: {e}", exc_info=True)
        update_variation_status(variation_id, 'failed', error_message=str(e))
        return {'status': 'error', 'variation_id': variation_id, 'message': str(e)}


# Utility task for retrying failed links
@celery_app.task(bind=True)
def retry_failed_links(self, batch_id: str):
    """
    Retry all failed links in a batch.

    Args:
        batch_id: UUID of the batch
    """
    logger.info(f"[Batch {batch_id[:8]}] Retrying failed links")

    try:
        batch = get_batch(batch_id)
        if not batch:
            return {'status': 'error', 'message': 'Batch not found'}

        # Get Drive folder from batch
        drive_folder_url = batch.get('drive_folder_url', '')
        # Extract folder ID from URL
        drive_folder_id = drive_folder_url.split('/')[-1] if drive_folder_url else None

        if not drive_folder_id:
            return {'status': 'error', 'message': 'No Drive folder found for batch'}

        # Get failed links
        failed_links = get_batch_links(batch_id, status='failed')

        if not failed_links:
            logger.info(f"[Batch {batch_id[:8]}] No failed links to retry")
            return {'status': 'completed', 'message': 'No failed links'}

        logger.info(f"[Batch {batch_id[:8]}] Retrying {len(failed_links)} failed links")

        # Reset failed links to pending
        from database import reset_failed_links
        reset_failed_links(batch_id)

        # Dispatch retry tasks
        link_tasks = []
        for link in failed_links:
            task = process_link.s(link['id'], drive_folder_id)
            link_tasks.append(task)

        # Execute retries
        workflow = group(link_tasks) | finalize_batch.s(batch_id)
        workflow.apply_async()

        return {
            'status': 'dispatched',
            'batch_id': batch_id,
            'retry_count': len(failed_links)
        }

    except Exception as e:
        logger.error(f"[Batch {batch_id[:8]}] Retry failed: {e}", exc_info=True)
        return {'status': 'error', 'message': str(e)}


# =============================================================================
# TikTok Copy Tool Tasks
# =============================================================================

from database import (
    get_tiktok_copy_batch, get_tiktok_copy_jobs,
    update_tiktok_copy_job, update_tiktok_copy_batch
)
from tiktok_copy_service import process_tiktok_copy, TikTokCopyError


@celery_app.task(bind=True, max_retries=0)
def process_tiktok_copy_batch(self, batch_id: str):
    """
    Orchestrate processing of all jobs in a TikTok copy batch.
    Creates a Google Drive folder and dispatches job processing tasks.

    Args:
        batch_id: UUID of the batch to process
    """
    logger.info(f"[TikTokCopy {batch_id[:8]}] Starting batch processing")

    try:
        # Get batch info
        batch = get_tiktok_copy_batch(batch_id)
        if not batch:
            logger.error(f"[TikTokCopy {batch_id[:8]}] Batch not found")
            return {'status': 'error', 'message': 'Batch not found'}

        # Get jobs for this batch
        jobs = get_tiktok_copy_jobs(batch_id)
        if not jobs:
            logger.warning(f"[TikTokCopy {batch_id[:8]}] No jobs found")
            update_tiktok_copy_batch(batch_id, status='completed')
            return {'status': 'completed', 'message': 'No jobs to process'}

        # Create main Google Drive folder for batch
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        folder_name = f"Video_Copy_{timestamp}"

        try:
            drive_folder_id = create_folder(folder_name)
            set_folder_public(drive_folder_id)
            drive_folder_url = get_folder_link(drive_folder_id)
            update_tiktok_copy_batch(batch_id, drive_folder_url=drive_folder_url)
            logger.info(f"[TikTokCopy {batch_id[:8]}] Created Drive folder: {drive_folder_url}")
        except GoogleDriveError as e:
            logger.error(f"[TikTokCopy {batch_id[:8]}] Failed to create Drive folder: {e}")
            update_tiktok_copy_batch(batch_id, status='failed')
            return {'status': 'error', 'message': str(e)}

        logger.info(f"[TikTokCopy {batch_id[:8]}] Dispatching {len(jobs)} job tasks (rate-limited to 12/min)")

        # Dispatch job processing tasks
        # rate_limit='12/m' on the task prevents thundering herd (max 12 tasks started/min)
        # Combined with Redis rate limiter in scraper (1 req/sec), RapidAPI 429s are avoided
        job_tasks = []
        for job in jobs:
            task = process_tiktok_copy_job.s(job['id'], drive_folder_id)
            job_tasks.append(task)

        workflow = group(job_tasks) | finalize_tiktok_copy_batch.s(batch_id)
        workflow.apply_async()

        return {
            'status': 'dispatched',
            'batch_id': batch_id,
            'jobs_count': len(jobs),
            'drive_folder_url': drive_folder_url
        }

    except Exception as e:
        logger.error(f"[TikTokCopy {batch_id[:8]}] Batch processing failed: {e}", exc_info=True)
        update_tiktok_copy_batch(batch_id, status='failed')
        return {'status': 'error', 'message': str(e)}


@celery_app.task(bind=True, max_retries=3, rate_limit='12/m')
def process_tiktok_copy_job(
    self,
    job_id: str,
    parent_drive_folder_id: str
):
    """
    Process a single TikTok copy job: scrape, auto-detect product slide, convert to video, upload to Drive.

    Supports 3 modes (set on the batch):
    - 'auto-replace': AI detects product slide and replaces it with the selected product photo
    - 'no-replacement': Just convert slideshow to video as-is
    - 'manual': Legacy mode — uses per-job replace_slide number set by user

    Args:
        job_id: UUID of the job to process
        parent_drive_folder_id: Google Drive folder ID for the batch

    Returns:
        dict with processing result
    """
    from database import get_tiktok_copy_job
    from gemini_service_v2 import detect_product_slide

    logger.info(f"[TikTokCopy Job {job_id[:8]}] Starting job processing")

    try:
        # Get job info from database
        job = get_tiktok_copy_job(job_id)
        if not job:
            logger.error(f"[TikTokCopy Job {job_id[:8]}] Job not found")
            return {'status': 'error', 'job_id': job_id, 'message': 'Job not found'}

        # Get batch to determine mode
        batch = get_tiktok_copy_batch(job['batch_id'])
        mode = batch.get('mode', 'manual') if batch else 'manual'

        tiktok_url = job['tiktok_url']
        replace_slide = job.get('replace_slide')  # May be None for auto-replace mode
        product_photo_path = job.get('product_photo_path')

        logger.info(f"[TikTokCopy Job {job_id[:8]}] Mode: {mode}")

        # Update status to processing
        update_tiktok_copy_job(job_id, 'processing')

        # Create temporary working directory
        work_dir = tempfile.mkdtemp(prefix=f"tiktok_copy_{job_id[:8]}_")
        scrape_dir = os.path.join(work_dir, 'scraped')
        output_dir = os.path.join(work_dir, 'output')
        os.makedirs(scrape_dir, exist_ok=True)
        os.makedirs(output_dir, exist_ok=True)

        try:
            # Step 1: Scrape TikTok slideshow
            logger.info(f"[TikTokCopy Job {job_id[:8]}] Scraping: {tiktok_url[:50]}...")
            scrape_result = scrape_tiktok_slideshow(tiktok_url, scrape_dir, request_id=job_id[:8])
            slide_images = scrape_result['images']
            logger.info(f"[TikTokCopy Job {job_id[:8]}] Scraped {len(slide_images)} slides")

            # Step 2: Auto-detect product slide (only in auto-replace mode)
            detection_skipped = False
            product_slide_detected = None

            if mode == 'auto-replace':
                try:
                    logger.info(f"[TikTokCopy Job {job_id[:8]}] Running AI slide detection...")
                    detection_result = detect_product_slide(slide_images)
                    detected_slide = detection_result.get('slide_number')
                    confidence = detection_result.get('confidence', 'low')
                    reason = detection_result.get('reason', '')

                    if detected_slide and confidence in ('high', 'medium'):
                        replace_slide = detected_slide
                        product_slide_detected = detected_slide
                        logger.info(f"[TikTokCopy Job {job_id[:8]}] Detected product slide #{detected_slide} "
                                    f"({confidence}): {reason}")
                    else:
                        detection_skipped = True
                        logger.info(f"[TikTokCopy Job {job_id[:8]}] No product slide detected "
                                    f"(confidence={confidence}), skipping replacement")
                except Exception as e:
                    detection_skipped = True
                    logger.warning(f"[TikTokCopy Job {job_id[:8]}] Detection failed: {e}, skipping replacement")

            elif mode == 'no-replacement':
                # No replacement mode — just convert as-is
                replace_slide = None
                product_photo_path = None
                detection_skipped = True
                logger.info(f"[TikTokCopy Job {job_id[:8]}] No-replacement mode, converting as-is")

            elif mode == 'manual' and replace_slide:
                # Legacy manual mode — use the slide number from the job
                logger.info(f"[TikTokCopy Job {job_id[:8]}] Manual mode: replacing slide {replace_slide}")

            # If detection skipped, don't pass photo for replacement
            if detection_skipped:
                replace_slide = None
                product_photo_path = None

            # Step 3: Generate video with FFmpeg
            video_filename = f"video_{job_id[:8]}.mp4"
            video_path = process_tiktok_copy(
                scraped_data=scrape_result,
                output_dir=output_dir,
                video_filename=video_filename,
                replace_slide_number=replace_slide,
                product_photo_path=product_photo_path,
                request_id=job_id[:8]
            )
            logger.info(f"[TikTokCopy Job {job_id[:8]}] Video created: {video_path}")

            # Step 4: Upload video to Google Drive
            file_id = upload_file(video_path, parent_drive_folder_id, video_filename)
            video_drive_url = f"https://drive.google.com/file/d/{file_id}/view"
            logger.info(f"[TikTokCopy Job {job_id[:8]}] Uploaded to Drive: {video_drive_url}")

            # Update job status to completed with detection results
            update_tiktok_copy_job(
                job_id, 'completed',
                drive_url=video_drive_url,
                product_slide_detected=product_slide_detected,
                detection_skipped=detection_skipped
            )

            return {
                'status': 'completed',
                'job_id': job_id,
                'drive_url': video_drive_url,
                'product_slide_detected': product_slide_detected,
                'detection_skipped': detection_skipped
            }

        finally:
            # Cleanup temporary directory
            try:
                shutil.rmtree(work_dir)
            except Exception as e:
                logger.warning(f"[TikTokCopy Job {job_id[:8]}] Failed to cleanup {work_dir}: {e}")

    except TikTokScraperError as e:
        error_msg = str(e)
        logger.error(f"[TikTokCopy Job {job_id[:8]}] Scraping failed: {e}")

        # Retry on rate limit with exponential backoff (60s, 120s, 240s)
        if '429' in error_msg or 'rate' in error_msg.lower():
            try:
                backoff = 60 * (2 ** self.request.retries)  # 60s, 120s, 240s
                logger.info(f"[TikTokCopy Job {job_id[:8]}] Retrying due to rate limit (attempt {self.request.retries + 1}, backoff {backoff}s)...")
                raise self.retry(exc=e, countdown=backoff)
            except MaxRetriesExceededError:
                logger.error(f"[TikTokCopy Job {job_id[:8]}] Max retries exceeded for rate limit")

        update_tiktok_copy_job(job_id, 'failed', error_message=f"Scrape error: {e}")
        return {'status': 'error', 'job_id': job_id, 'message': str(e)}

    except TikTokCopyError as e:
        logger.error(f"[TikTokCopy Job {job_id[:8]}] Video generation failed: {e}")
        update_tiktok_copy_job(job_id, 'failed', error_message=f"Video error: {e}")
        return {'status': 'error', 'job_id': job_id, 'message': str(e)}

    except GoogleDriveError as e:
        logger.error(f"[TikTokCopy Job {job_id[:8]}] Drive upload failed: {e}")
        update_tiktok_copy_job(job_id, 'failed', error_message=f"Upload error: {e}")
        return {'status': 'error', 'job_id': job_id, 'message': str(e)}

    except Exception as e:
        logger.error(f"[TikTokCopy Job {job_id[:8]}] Unexpected error: {e}", exc_info=True)
        update_tiktok_copy_job(job_id, 'failed', error_message=str(e))
        return {'status': 'error', 'job_id': job_id, 'message': str(e)}


@celery_app.task(bind=True)
def finalize_tiktok_copy_batch(self, job_results: list, batch_id: str):
    """
    Called after all TikTok copy jobs complete. Updates batch status.
    Also recovers zombie jobs (None results from crashed tasks) by marking them failed in DB.

    Args:
        job_results: List of results from process_tiktok_copy_job tasks
        batch_id: UUID of the batch
    """
    logger.info(f"[TikTokCopy {batch_id[:8]}] Finalizing batch")

    try:
        # Count results - treat None/missing results as failures
        completed = sum(1 for r in job_results if r and r.get('status') == 'completed')
        total = len(job_results)
        failed = total - completed

        # Auto-recover zombie jobs: any result that is None means the Celery task
        # crashed without updating the DB. Mark these as failed so they don't stay
        # "processing" forever.
        zombie_count = sum(1 for r in job_results if r is None)
        if zombie_count > 0:
            logger.warning(f"[TikTokCopy {batch_id[:8]}] Detected {zombie_count} zombie jobs (crashed tasks), marking as failed")
            jobs = get_tiktok_copy_jobs(batch_id)
            for job in jobs:
                if job.get('status') == 'processing':
                    update_tiktok_copy_job(
                        job['id'], 'failed',
                        error_message='Task crashed (zombie recovery)'
                    )
                    logger.info(f"[TikTokCopy {batch_id[:8]}] Recovered zombie job {job['id'][:8]}")

        logger.info(f"[TikTokCopy {batch_id[:8]}] Results: {completed}/{total} completed, {failed} failed (zombies: {zombie_count})")

        # Determine final status
        if completed == 0:
            final_status = 'failed'
        elif completed == total:
            final_status = 'completed'
        else:
            final_status = 'completed'  # Partial success

        update_tiktok_copy_batch(batch_id, status=final_status)

        return {
            'batch_id': batch_id,
            'status': final_status,
            'total': total,
            'completed': completed,
            'failed': failed,
            'zombies_recovered': zombie_count
        }

    except Exception as e:
        logger.error(f"[TikTokCopy {batch_id[:8]}] Finalization failed: {e}", exc_info=True)
        update_tiktok_copy_batch(batch_id, status='failed')
        return {'status': 'error', 'message': str(e)}
