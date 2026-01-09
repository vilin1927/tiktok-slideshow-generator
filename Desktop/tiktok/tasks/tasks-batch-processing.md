# Batch Processing Feature - Task List

## Relevant Files

### Backend - Infrastructure
- `backend/celery_app.py` - Celery application configuration
- `backend/celery_config.py` - Celery settings (broker, backend, rate limits)
- `backend/tasks.py` - Celery task definitions (process_link, generate_variation)
- `backend/worker.sh` - Script to start Celery worker

### Backend - Database
- `backend/database.py` - SQLite connection and schema setup
- `backend/models.py` - Batch, BatchLink, BatchVariation models

### Backend - API
- `backend/app.py` - Add batch endpoints to existing Flask app
- `backend/batch_routes.py` - Batch-specific route handlers

### Frontend
- `frontend/index.html` - Add batch mode toggle and UI components
- `frontend/batch.js` - Batch mode JavaScript logic (or inline in index.html)

### Deployment
- `requirements.txt` - Add celery, redis dependencies
- `start_worker.sh` - Celery worker startup script for VPS
- `supervisord.conf` (optional) - Process management config

## Instructions for Completing Tasks

**IMPORTANT:** As you complete each task, check it off by changing `- [ ]` to `- [x]`.

---

## Tasks

- [x] 0.0 Create feature branch
  - [x] 0.1 Create and checkout branch: `git checkout -b feature/batch-processing`

- [x] 1.0 Infrastructure Setup (Redis + Celery)
  - [x] 1.1 Add dependencies to requirements.txt: `celery`, `redis`, `flower` (optional)
  - [x] 1.2 Create `backend/celery_app.py` with Celery instance configuration
  - [x] 1.3 Create `backend/celery_config.py` with broker URL, result backend, rate limits (merged into celery_app.py)
  - [x] 1.4 Create `backend/worker.sh` script to start Celery worker
  - [ ] 1.5 Test Celery connection to Redis locally (if possible) or on VPS
  - [ ] 1.6 Install dependencies on VPS: `pip install celery redis`

- [x] 2.0 Database Schema & Models
  - [x] 2.1 Create `backend/database.py` with SQLite connection helper
  - [x] 2.2 Define schema: `batches` table (id, status, total_links, created_at, drive_folder_url)
  - [x] 2.3 Define schema: `batch_links` table (id, batch_id, link_url, product_photo, product_desc, status, error, celery_task_id)
  - [x] 2.4 Define schema: `batch_variations` table (id, batch_link_id, variation_num, status, celery_task_id, output_path)
  - [x] 2.5 Create `init_db()` function to create tables if not exist
  - [x] 2.6 Create helper functions: `create_batch()`, `get_batch()`, `update_batch_status()`, etc.

- [x] 3.0 Celery Tasks & Queue Logic
  - [x] 3.1 Create `@celery.task` for `process_batch(batch_id)` - orchestrates all links
  - [x] 3.2 Create `@celery.task` for `process_link(batch_link_id)` - processes single link with all variations
  - [x] 3.3 Create `@celery.task` for `generate_variation(batch_link_id, variation_num)` - single variation
  - [x] 3.4 Implement rate limiting: `rate_limit='10/m'` on generate_variation task
  - [x] 3.5 Implement retry logic with exponential backoff for rate limit errors
  - [x] 3.6 Integrate with existing `run_pipeline()` from `gemini_service_v2.py`
  - [x] 3.7 Update task status in database as tasks progress
  - [x] 3.8 Upload completed variations to Google Drive with folder structure
  - [x] 3.9 Handle task failures - update status, store error message

- [x] 4.0 Backend API Endpoints
  - [x] 4.1 `POST /api/batch` - Create new batch (accepts links array + products mapping)
  - [x] 4.2 Validate links (TikTok URL format, slideshow detection, duplicates, max 100)
  - [x] 4.3 Store batch and links in database, return batch_id
  - [x] 4.4 Trigger `process_batch.delay(batch_id)` to start queue
  - [x] 4.5 `GET /api/batch/<batch_id>/status` - Return progress (total, completed, failed, in_progress)
  - [x] 4.6 `POST /api/batch/<batch_id>/cancel` - Cancel pending tasks, update status
  - [x] 4.7 `POST /api/batch/<batch_id>/retry-failed` - Retry failed links
  - [x] 4.8 Handle product photo uploads (save to temp, store path in DB)

- [x] 5.0 Frontend - Batch Mode UI
  - [x] 5.1 Add mode toggle: "Single Link" / "Batch Mode" buttons/tabs
  - [x] 5.2 Create batch link input textarea (large, placeholder text)
  - [x] 5.3 Implement link parsing (newlines, commas, tabs)
  - [x] 5.4 Implement real-time validation with count display: "✅ X valid | ❌ Y invalid"
  - [x] 5.5 Add "View Invalid" button showing problem links with reasons
  - [x] 5.6 Create product mapping table UI (link, photo upload, description input)
  - [x] 5.7 Add "Apply to All" checkbox for same product across all links
  - [x] 5.8 Add variation dropdowns: Photo (1-10), Text (1-10)
  - [x] 5.9 Show calculated totals: "X variations per link, Y total"
  - [x] 5.10 "Start Batch" button - validates all data, calls POST /api/batch

- [x] 6.0 Frontend - Progress & Results
  - [x] 6.1 Create progress screen with progress bar
  - [x] 6.2 Display counts: completed, in_progress, failed, queued
  - [x] 6.3 Implement polling: call GET /api/batch/{id}/status every 3-5 seconds
  - [ ] 6.4 Show recent activity log (last 5-10 completions)
  - [ ] 6.5 Add estimated time remaining (based on completion rate)
  - [x] 6.6 Add "Cancel Batch" button
  - [x] 6.7 Create completion screen with summary
  - [x] 6.8 Show Google Drive folder link
  - [ ] 6.9 List failed links with error reasons
  - [x] 6.10 Add "Retry Failed" and "Start New Batch" buttons

- [ ] 7.0 Integration & Testing
  - [ ] 7.1 Test with 5 links end-to-end locally (if possible)
  - [ ] 7.2 Test rate limiting - verify tasks don't exceed limits
  - [ ] 7.3 Test failure handling - invalid link, API error, etc.
  - [ ] 7.4 Test cancel functionality
  - [ ] 7.5 Test retry failed functionality
  - [ ] 7.6 Verify Google Drive folder structure is correct
  - [ ] 7.7 Test with 20+ links to verify queue behavior

- [ ] 8.0 Deployment
  - [ ] 8.1 Deploy backend code to VPS
  - [ ] 8.2 Install Python dependencies on VPS
  - [ ] 8.3 Create systemd service for Celery worker (or use supervisor)
  - [ ] 8.4 Start Celery worker on VPS
  - [ ] 8.5 Verify worker connects to Redis
  - [ ] 8.6 Test batch creation from live frontend
  - [ ] 8.7 Monitor first real batch processing
  - [ ] 8.8 Document worker restart procedure
