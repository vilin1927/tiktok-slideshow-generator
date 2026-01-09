# PRD: Batch Processing Feature

## Overview

Enable processing of up to 100 TikTok slideshow links at once, with each link mapped to its own product. The system will use a queue to respect API rate limits and upload results to separate Google Drive folders per link.

**Contract**: $180 | **Timeline**: Urgent (today)

---

## Goals

1. Allow users to process up to 100 TikTok links in a single batch
2. Map each link to its own product (photo + description)
3. Support photo and text variations per link (each variation = complete regeneration)
4. Implement queue system to respect Gemini API rate limits
5. Upload results to Google Drive with organized folder structure
6. Provide real-time progress tracking

---

## User Stories

1. **As Jenny**, I want to paste 100 TikTok links at once so I don't have to process them one by one.

2. **As Jenny**, I want to assign different products to different links so each slideshow features the correct product.

3. **As Jenny**, I want to set how many variations I need per link so I get multiple options to choose from.

4. **As Jenny**, I want to see progress in real-time so I know how long the batch will take.

5. **As Jenny**, I want results organized in Drive folders so I can easily find each link's outputs.

---

## Functional Requirements

### FR1: UI Mode Toggle
1.1. Add toggle between "Single Link" (current) and "Batch Mode" in the UI
1.2. Single Link mode remains unchanged (current functionality)
1.3. Batch Mode shows the new bulk interface

### FR2: Batch Link Input
2.1. Large text area for pasting multiple TikTok links
2.2. Accept links separated by: newlines, commas, tabs, or any mix
2.3. Real-time validation as user types/pastes
2.4. Display count: "✅ X valid | ❌ Y invalid"
2.5. "View Invalid" button shows problem links with reasons
2.6. Maximum 100 links per batch
2.7. Detect and flag duplicate links

### FR3: Product Mapping Interface
3.1. After links are validated, show a mapping table:
```
| # | TikTok Link (truncated) | Product Photo | Product Description |
|---|-------------------------|---------------|---------------------|
| 1 | @user/photo/123...     | [Upload]      | [Text input]        |
| 2 | @user/photo/456...     | [Upload]      | [Text input]        |
```
3.2. Allow bulk upload of product photos (matched by order or filename)
3.3. Allow paste of descriptions from spreadsheet column
3.4. Show validation: which rows are complete vs missing data
3.5. "Apply to All" option for using same product across all links

### FR4: Variation Settings
4.1. "Photo Variations" dropdown: 1-10 (default: 3)
4.2. "Text Variations" dropdown: 1-10 (default: 2)
4.3. Display calculated totals:
    - "Variations per link: 6 (3 photo × 2 text)"
    - "Total for batch: 600 (100 links × 6)"
4.4. Each variation = complete new API call (full regeneration)

### FR5: Queue System
5.1. Implement job queue for batch processing
5.2. Process max 5 links concurrently (respect rate limits)
5.3. Queue persists - survives page refresh/close
5.4. Failed jobs retry automatically (max 3 attempts)
5.5. Jobs have states: pending, processing, completed, failed

### FR6: Progress Tracking
6.1. Show overall progress bar with percentage
6.2. Display counts: completed, in-progress, failed, remaining
6.3. Show estimated time remaining
6.4. Live activity log showing recent completions
6.5. Allow canceling remaining jobs (keeps completed ones)

### FR7: Google Drive Output
7.1. Create folder structure:
```
/TikTok Batch [timestamp]/
  /Link_001_@username/
    /variation_1/
      slide_0.jpg
      slide_1.jpg
      ...
    /variation_2/
      ...
  /Link_002_@username/
    ...
```
7.2. Use JPEG format (quality 85-90%)
7.3. Include analysis.json in each variation folder

### FR8: Completion & Results
8.1. Show summary when batch completes
8.2. Display success/failure counts
8.3. Link to Google Drive folder
8.4. List failed links with error reasons
8.5. "Retry Failed" button for failed links
8.6. "Start New Batch" button

### FR9: Settings Panel
9.1. Gemini API key input (masked, validated)
9.2. Output format toggle (JPEG recommended, PNG option)
9.3. Settings persist in localStorage

---

## Non-Goals (Out of Scope)

- Scheduling batches for future processing
- Email notifications when batch completes
- Batch history/archive
- Collaborative features (multiple users)
- Mobile-optimized UI

---

## Technical Considerations

### Queue Architecture: Celery + Redis

**Stack:**
- **Celery** - Distributed task queue
- **Redis** - Message broker + result backend
- **Flower** (optional) - Web UI for monitoring

**Job Hierarchy:**
```
BatchJob (user submits 100 links)
  └── LinkJob #1
        ├── VariationTask #1 (full pipeline: analysis + image gen)
        ├── VariationTask #2
        ├── VariationTask #3
        └── ... (photo_variations × text_variations)
  └── LinkJob #2
        └── ...
  └── LinkJob #100
        └── ...
```

**Celery Configuration:**
```python
# Rate limiting at task level
@celery.task(rate_limit='10/m')  # 10 tasks per minute
def generate_variation(link, product_photo, product_desc, variation_num):
    ...

# Concurrency
CELERY_WORKER_CONCURRENCY = 5  # Max 5 parallel tasks

# Retries
@celery.task(bind=True, max_retries=3, default_retry_delay=60)
def generate_variation(self, ...):
    try:
        ...
    except RateLimitError:
        self.retry(countdown=60)
```

**Task States:**
- PENDING - In queue, waiting
- STARTED - Worker picked it up
- SUCCESS - Completed successfully
- FAILURE - Failed after retries
- RETRY - Waiting to retry

### Rate Limit Management
- Gemini API: ~60 RPM limit
- Celery rate_limit='10/m' per task type
- 5 concurrent workers = max 50 API calls/min (safe margin)
- Exponential backoff on 429 errors

### Progress Tracking
- Redis stores task states (Celery result backend)
- Frontend polls `/api/batch/{id}/status` endpoint
- Returns: total, completed, failed, in_progress counts
- WebSocket upgrade possible later for real-time

### File Storage
- Product photos uploaded to `/tmp/batch_{id}/products/`
- Generated images saved to `/tmp/batch_{id}/outputs/`
- After link completes, upload to Google Drive
- Clean up temp files after batch completes

### Database Schema (SQLite)
```sql
CREATE TABLE batches (
    id TEXT PRIMARY KEY,
    status TEXT,  -- pending, processing, completed, cancelled
    total_links INTEGER,
    created_at TIMESTAMP,
    completed_at TIMESTAMP,
    drive_folder_url TEXT
);

CREATE TABLE batch_links (
    id TEXT PRIMARY KEY,
    batch_id TEXT,
    link_url TEXT,
    product_photo_path TEXT,
    product_description TEXT,
    status TEXT,  -- pending, processing, completed, failed
    error_message TEXT,
    drive_folder_url TEXT,
    celery_task_id TEXT
);

CREATE TABLE batch_variations (
    id TEXT PRIMARY KEY,
    batch_link_id TEXT,
    variation_num INTEGER,
    status TEXT,
    celery_task_id TEXT,
    output_path TEXT
);
```

---

## UI Wireframes

### Batch Mode - Link Input
```
┌─────────────────────────────────────────────────────────────┐
│  TikTok Slideshow Generator     [Single ⚪ | 🔵 Batch]  ⚙️  │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  📋 Paste TikTok Links (max 100)                           │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ https://tiktok.com/@user1/photo/123                 │   │
│  │ https://tiktok.com/@user2/photo/456                 │   │
│  │ https://vm.tiktok.com/abc                           │   │
│  │ ...                                                 │   │
│  └─────────────────────────────────────────────────────┘   │
│  ✅ 98 valid  ❌ 2 invalid  [View Invalid]                  │
│                                                             │
│                    [ Next: Map Products → ]                 │
└─────────────────────────────────────────────────────────────┘
```

### Batch Mode - Product Mapping
```
┌─────────────────────────────────────────────────────────────┐
│  Step 2: Map Products to Links                              │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  [📤 Bulk Upload Photos]  [📋 Paste Descriptions]          │
│  [ ] Apply same product to all links                        │
│                                                             │
│  ┌─────┬──────────────────┬─────────────┬─────────────────┐│
│  │  #  │ TikTok Link      │ Photo       │ Description     ││
│  ├─────┼──────────────────┼─────────────┼─────────────────┤│
│  │  1  │ @user1/photo/123 │ [✅ eye.jpg]│ [Lumidew Eye...││
│  │  2  │ @user2/photo/456 │ [❌ Upload] │ [Enter desc...] ││
│  │  3  │ @user3/photo/789 │ [✅ tape.jp]│ [Face Tape...  ]││
│  │ ... │                  │             │                 ││
│  └─────┴──────────────────┴─────────────┴─────────────────┘│
│                                                             │
│  ✅ 96 ready  ❌ 2 incomplete                               │
│                                                             │
│  ─────────────────────────────────────────────────────────  │
│  ⚙️ Variations:  Photo [3▼]  ×  Text [2▼]  =  6 per link   │
│                  Total: 576 variations for 96 links         │
│                                                             │
│         [ ← Back ]              [ 🚀 Start Batch ]          │
└─────────────────────────────────────────────────────────────┘
```

### Processing Progress
```
┌─────────────────────────────────────────────────────────────┐
│  🔄 Processing Batch                          [Cancel]      │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  Overall: 47 of 96 links                                    │
│  ████████████████░░░░░░░░░░░░░░░  49%                      │
│                                                             │
│  ✅ 45 completed  ⏳ 5 processing  ❌ 2 failed  ⏸️ 44 queued│
│                                                             │
│  Estimated: ~18 minutes remaining                           │
│                                                             │
│  ─────────────────────────────────────────────────────────  │
│  Recent:                                                    │
│  ✅ Link 47 @skincare/photo/789 → 6 variations uploaded    │
│  ⏳ Link 48 @beauty/photo/012 → Generating variation 3/6   │
│  ⏳ Link 49 @deals/photo/345 → Analyzing slideshow...      │
│  ❌ Link 46 @user/photo/111 → Rate limit, retry in 30s     │
└─────────────────────────────────────────────────────────────┘
```

---

## Success Metrics

1. Can process 100 links in under 30 minutes
2. Zero rate limit failures (queue manages limits)
3. All results organized correctly in Drive
4. Jenny can use batch mode without asking questions

---

## Open Questions

1. Should we show a preview of one generated slideshow before processing all?
2. Do we need to support resuming interrupted batches?
3. Should failed links auto-retry or wait for manual retry?

---

## Implementation Priority

**Phase 1A: Infrastructure Setup**
- [ ] Install Redis on VPS (if not present)
- [ ] Install Celery + dependencies
- [ ] Create Celery app configuration
- [ ] Create worker startup script
- [ ] Test basic task execution

**Phase 1B: Backend - Queue & Tasks**
- [ ] Create SQLite database schema
- [ ] Create Celery tasks: `process_link`, `generate_variation`
- [ ] Implement rate limiting (10/min)
- [ ] Add retry logic with exponential backoff
- [ ] Create batch status API endpoint
- [ ] Integrate with existing `run_pipeline()`

**Phase 1C: Backend - API Endpoints**
- [ ] `POST /api/batch` - Create new batch
- [ ] `GET /api/batch/{id}/status` - Get progress
- [ ] `POST /api/batch/{id}/cancel` - Cancel batch
- [ ] `POST /api/batch/{id}/retry-failed` - Retry failed links
- [ ] Update Drive upload to use folder structure

**Phase 1D: Frontend - Batch UI**
- [ ] Add Single/Batch mode toggle
- [ ] Batch link input with validation
- [ ] Product mapping table
- [ ] Variation settings
- [ ] Progress tracking screen
- [ ] Completion screen with results

**Phase 2 (If time permits)**
- [ ] Bulk photo upload
- [ ] Paste descriptions from spreadsheet
- [ ] Flower monitoring UI
- [ ] Settings panel with API key
- [ ] Time estimates
- [ ] JPEG output option
