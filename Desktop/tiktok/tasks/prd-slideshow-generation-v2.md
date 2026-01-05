# PRD: TikTok Slideshow Generation v2 - Redesigned Pipeline

## Introduction/Overview

Redesign the slideshow generation pipeline to use a **two-step approach**:

1. **Single Analysis Call**: Send ALL scraped TikTok images to Gemini at once. Analyze structure, style, and story. Generate a NEW adapted story for the user's product while preserving the exact same structure, mood, and vibe.

2. **Parallel Image Generation**: Execute image generation calls in parallel (respecting 60 RPM limit) where each call receives:
   - The reference image (for style matching)
   - The new scene description (mood, composition, subject)
   - Instruction to match font/text style exactly from reference

**Key Insight**: Never specify font names or exact text styling - always say "match exactly the font style, size, position from the reference image."

## Goals

1. Eliminate wasted API calls by analyzing all images in one request
2. Preserve exact structure from viral TikTok (1 hook → N body → 1 product/CTA)
3. Generate images that match the original aesthetic but with new product content
4. Maximize throughput with parallel image generation (up to 60 RPM)
5. Save analysis results for debugging and user visibility

## User Stories

1. As a user, I upload a TikTok URL and my product image, and the system generates a new slideshow with the SAME structure and vibe but adapted for my product.

2. As a user, I can see what the AI detected (structure, slides, mood) before generation starts.

3. As a user, my product image is used as the base for the product/CTA slide with styled text overlay matching the original.

## Functional Requirements

### Step 1: Scraping (unchanged)
1. Scrape TikTok URL via RapidAPI
2. Download all images in parallel via proxy
3. Download audio file
4. Output: `slide_1.jpg`, `slide_2.jpg`, ... `slide_N.jpg`, `audio.mp3`

### Step 2: Analysis & Story Generation (Single API Call)

**Input to Gemini (`gemini-3-pro-preview`):**
- ALL scraped images uploaded at once
- User's product description
- User's product image (for context)

**Prompt Structure:**
```
[ATTACH: all_viral_slideshow_images]
[ATTACH: user_product_image]

You are analyzing a viral TikTok slideshow to recreate it for a new product.

USER'S PRODUCT: {product_description}

TASK: Analyze this slideshow and create a NEW story for the user's product.

PART A - DETECT STRUCTURE:
- How many slides total?
- Which slide is the HOOK? (attention-grabbing first slide)
- Which slides are BODY? (tips, benefits, features)
- Which slide is the PRODUCT/CTA? (buy link, "link in bio", endorsement)

PART B - FOR EACH SLIDE, EXTRACT:
1. Slide type (hook/body/product)
2. Text style description (for reference only - generation will copy from image)
3. Image style (subject, composition, lighting, colors, background, mood)
4. Original text content

PART C - CREATE NEW STORY FOR USER'S PRODUCT:
For each slide, generate:
1. slide_index: (0, 1, 2, ...)
2. slide_type: (hook/body/product)
3. reference_image: which original slide to use as style reference
4. new_scene_description: describe the NEW image to generate
   - Same mood/vibe as original
   - Same composition style
   - Adapted for user's product
   - DO NOT include exact text - that will be generated matching reference style
5. text_purpose: what the text should convey (e.g., "attention-grabbing question about skincare")

IMPORTANT:
- Output slide count MUST match input slide count
- Preserve exact structure (hook→body→product order)
- For product slide: note that user's product image will be used as base

Return JSON:
{
  "structure": {
    "total_slides": N,
    "hook_index": 0,
    "body_indices": [1, 2, 3, 4, 5],
    "product_index": 6
  },
  "original_context": {
    "product_topic": "...",
    "target_audience": "...",
    "overall_mood": "...",
    "hook_angle": "..."
  },
  "new_slides": [
    {
      "slide_index": 0,
      "slide_type": "hook",
      "reference_image_index": 0,
      "new_scene_description": "Close-up lifestyle shot of person looking concerned at skin in mirror, warm soft lighting, aesthetic clean background, worried/curious expression",
      "text_purpose": "Attention-grabbing question about the skincare problem this product solves",
      "original_text_style": {
        "background": "white rounded rectangle 90% opacity",
        "position": "center",
        "font_description": "bold sans-serif, black text"
      }
    },
    ...
  ]
}
```

**Output:**
- Save full JSON to `analysis.json` in session folder
- Parse into generation tasks

### Step 3: Parallel Image Generation

**Rate Limiting:**
- Tier 1 = 60 RPM max
- Use conservative 10 concurrent requests with 1s delay between batches
- Implement retry with exponential backoff

**For Hook/Body Slides - Prompt to `gemini-3-pro-image-preview`:**
```
[ATTACH: reference_slide_image]

Generate a NEW TikTok slideshow image.

SCENE: {new_scene_description}

CRITICAL INSTRUCTIONS:
1. Match EXACTLY the same font style, size, weight, and position as the reference image
2. Match EXACTLY the same text box style (shape, color, opacity, border, shadow)
3. Match the same overall mood, lighting, and aesthetic
4. Generate appropriate text for: {text_purpose}
5. The text content should be new but the STYLE must be identical to reference

DO NOT describe the font - just copy it exactly from the reference.
```

**For Product Slide - Prompt to `gemini-3-pro-image-preview`:**
```
[ATTACH: user_product_image]
[ATTACH: reference_product_slide]

Generate a TikTok product slide.

BASE IMAGE: Use the first image (user's product photo) as the main image.
STYLE REFERENCE: Copy the text overlay style EXACTLY from the second image.

TASK: Add text overlay to the product photo that:
1. Matches EXACTLY the font style, size, weight from reference
2. Matches EXACTLY the text box style (shape, color, opacity, position)
3. Contains compelling CTA text for: {product_description}

DO NOT change the product image - only add styled text overlay.
```

**Parallel Execution:**
```python
from concurrent.futures import ThreadPoolExecutor
import time

def generate_with_rate_limit(tasks, max_concurrent=10, rpm_limit=60):
    results = []
    with ThreadPoolExecutor(max_workers=max_concurrent) as executor:
        for i in range(0, len(tasks), max_concurrent):
            batch = tasks[i:i+max_concurrent]
            futures = [executor.submit(generate_image, task) for task in batch]
            results.extend([f.result() for f in futures])

            # Respect rate limit: wait if needed
            if i + max_concurrent < len(tasks):
                time.sleep(max(1, 60 / rpm_limit * len(batch)))

    return results
```

### Step 4: Upload to Google Drive (unchanged)
- Create folder
- Upload all generated images
- Upload audio
- Return shareable link

## Non-Goals (Out of Scope)

1. Editing/adjusting generated images after creation
2. Custom font selection by user
3. Manual slide reordering
4. A/B testing multiple stories for same input
5. Video generation (images + audio assembly)

## Technical Considerations

1. **Models:**
   - Analysis: `gemini-3-pro-preview`
   - Image Generation: `gemini-3-pro-image-preview`

2. **Rate Limits (Tier 1):**
   - ~60 RPM for image generation
   - Semaphore-based rate limiting with delay BEFORE each request
   - 10 max concurrent requests
   - Retry logic with exponential backoff (max 3 retries)

3. **Timeouts:**
   - Default HTTP timeout: 120 seconds per API call
   - Configurable via `REQUEST_TIMEOUT` constant
   - Applied at HTTP client level for reliable termination

4. **Image Upload:**
   - All images sent as base64 in single analysis call
   - ~7 images × ~200KB = ~1.4MB per analysis request

5. **Session Storage:**
   - Save `analysis.json` with full AI response
   - Save each generated image with metadata
   - Keep for debugging/display to user

## Progress Callback API

The service uses two different callback signatures for different granularity levels:

### PipelineProgressCallback (run_pipeline)
```python
def callback(status: str, message: str, percent: int) -> None:
    """
    High-level pipeline progress.

    Args:
        status: Current phase ('analyzing' | 'generating')
        message: Human-readable progress message
        percent: Overall progress percentage (0-100)
    """
```

### ImageProgressCallback (generate_all_images)
```python
def callback(current: int, total: int, message: str) -> None:
    """
    Image generation progress.

    Args:
        current: Number of images completed
        total: Total number of images
        message: Human-readable progress message (e.g., "Generated 3/7 images")
    """
```

### Internal Wrapper
`run_pipeline` internally converts `ImageProgressCallback` to `PipelineProgressCallback`:
```python
def image_progress(current, total, message):
    percent = 40 + int(50 * current / total)  # Maps to 40-90%
    progress_callback('generating', message, percent)
```

## Rate Limiter Implementation

```python
class RateLimiter:
    """
    Semaphore-based rate limiter that enforces RPM limits.
    Ensures delay BEFORE each request, not after.
    """
    def __init__(self, rpm: int = 60, max_concurrent: int = 10):
        self.semaphore = threading.Semaphore(max_concurrent)
        self.min_interval = 60.0 / rpm  # seconds between requests
        self.last_request_time = 0.0
        self.lock = threading.Lock()

    def acquire(self):
        """Acquire permission to make a request. Blocks if rate limit exceeded."""
        self.semaphore.acquire()
        with self.lock:
            now = time.time()
            elapsed = now - self.last_request_time
            if elapsed < self.min_interval:
                time.sleep(self.min_interval - elapsed)
            self.last_request_time = time.time()

    def release(self):
        """Release the semaphore after request completes."""
        self.semaphore.release()
```

Usage pattern:
```python
rate_limiter.acquire()  # Blocks until safe to proceed
try:
    result = make_api_call()
finally:
    rate_limiter.release()
```

## Success Metrics

1. All N input slides → N output slides (100% structure preservation)
2. Generation time < 2 minutes for 7-slide deck
3. Text style matching accuracy (subjective - user feedback)
4. Zero failed generations due to rate limiting

## Data Flow Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                         USER INPUT                               │
│  TikTok URL + Product Image + Product Description                │
└─────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│                    STEP 1: SCRAPE                                │
│  RapidAPI → Download all slides in parallel → Save locally       │
│  Output: slide_1.jpg ... slide_N.jpg + audio.mp3                 │
└─────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│              STEP 2: ANALYZE & PLAN (Single Call)                │
│                                                                  │
│  Input: ALL slides + product image + description                 │
│  Model: gemini-3-pro-preview                                     │
│                                                                  │
│  Output: analysis.json                                           │
│  {                                                               │
│    structure: {hook: 0, body: [1,2,3,4,5], product: 6}          │
│    new_slides: [                                                 │
│      {index: 0, type: "hook", scene: "...", ref: 0},            │
│      {index: 1, type: "body", scene: "...", ref: 1},            │
│      ...                                                         │
│    ]                                                             │
│  }                                                               │
└─────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│            STEP 3: GENERATE IMAGES (Parallel)                    │
│                                                                  │
│  Model: gemini-3-pro-image-preview                               │
│  Rate: 10 concurrent, respect 60 RPM                             │
│                                                                  │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐            │
│  │ Hook     │ │ Body 1   │ │ Body 2   │ │ Body N   │  ...       │
│  │ Generate │ │ Generate │ │ Generate │ │ Generate │            │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘            │
│                                                                  │
│  Product slide: user_image + reference_style → styled overlay    │
│                                                                  │
│  Output: hook_v1.png, body_1_v1.png, ..., product_v1.png        │
└─────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│                   STEP 4: UPLOAD                                 │
│  Google Drive → Create folder → Upload images + audio            │
│  Output: Shareable folder link                                   │
└─────────────────────────────────────────────────────────────────┘
```

## File Structure

```
temp/
├── scraped/{session_id}/
│   ├── slide_1.jpg
│   ├── slide_2.jpg
│   ├── ...
│   └── audio.mp3
├── generated/{session_id}/
│   ├── analysis.json      # Full AI analysis for debugging
│   ├── hook_v1.png
│   ├── body_1_v1.png
│   ├── body_2_v1.png
│   ├── ...
│   └── product_v1.png
└── uploads/{session_id}/
    └── product_1_userimage.png
```

## Decisions (Resolved)

1. **Show analysis to user?** → Toggle in UI, default OFF. Analysis always saved to `analysis.json` but hidden unless user expands.

2. **Handle failed image generation?** → NEVER skip. Retry failed images with exponential backoff. If still fails after 3 retries, FAIL the entire request. User gets all slides or nothing.

3. **Variation count?** → NOT implemented in v2. Always generate exactly 1 image per slide. Variations will be added in future version.
