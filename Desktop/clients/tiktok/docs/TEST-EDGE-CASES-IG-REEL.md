# Instagram Reel Generator — Edge Cases Test Plan

**Date:** 2026-02-15
**Branch:** `feature/instagram-reel-generator`
**URL:** http://31.97.123.84/instagram-reel
**Prerequisite:** Tests 1-3 from `TEST-PLAN-IG-REEL.md` passed (9/9 videos)

---

## Overview

Tests 1-3 covered the happy path. This plan covers every edge case, failure mode, and boundary condition organized by component.

---

## CATEGORY A: Format Scraping & Management

### TEST A1: Invalid Instagram URL
**Steps:** POST `/api/instagram-reel/formats/scrape` with `url: "not-a-url"`, `format_name: "test-invalid"`
**Expected:** 400 or 500 error with clear message, no format created
**Validates:** URL input handling

### TEST A2: Deleted/Private Reel URL
**Steps:** POST scrape with URL `https://www.instagram.com/reels/AAAAAAAAAA/` (non-existent reel)
**Expected:** 500 with RapidAPI error message, no format created
**Validates:** External API error propagation

### TEST A3: RapidAPI Rate Limit / 500 Error
**Steps:** Scrape 5+ reels rapidly in succession
**Expected:** Graceful failure with error message, no crash, previously created formats intact
**Validates:** External API resilience

### TEST A4: Format with Empty Name
**Steps:** POST scrape with `format_name: ""` or `format_name: "   "`
**Expected:** 400 error
**Validates:** Input validation

### TEST A5: Format Name with Special Characters
**Steps:** POST scrape with `format_name: "test/format@#$%"`
**Expected:** Format created with sanitized name, no filesystem errors
**Validates:** `secure_filename()` handling

### TEST A6: Duplicate Format Name
**Steps:** Scrape same reel twice with same format_name
**Expected:** Second format created (unique ID), no DB conflict
**Validates:** Format uniqueness by ID not name

### TEST A7: Edit Format — Zero Duration Clips
**Steps:** PUT `/api/instagram-reel/formats/<id>` with `clips: [{"type": "before", "duration": 0}]`
**Expected:** Format updated, but generation should handle 0-duration gracefully
**Validates:** Duration boundary handling

### TEST A8: Edit Format — Negative Duration
**Steps:** PUT format with `clips: [{"type": "before", "duration": -5}]`
**Expected:** 400 error or FFmpeg failure during generation
**Validates:** Duration validation

### TEST A9: Edit Format — Empty Clips Array
**Steps:** PUT format with `clips: []`
**Expected:** Format updated, generation should fail with clear error
**Validates:** Empty format handling

### TEST A10: Delete Format While Job Processing
**Steps:** Start generation, immediately DELETE the format
**Expected:** Running job may fail gracefully; format deletion succeeds
**Validates:** Concurrent access safety

---

## CATEGORY B: Character & Asset Management

### TEST B1: Character with Empty Name
**Steps:** POST `/api/instagram-reel/characters` with `name: ""`
**Expected:** 400 error
**Validates:** Input validation

### TEST B2: Character Name — All Special Characters
**Steps:** POST characters with `name: "!@#$%^&*()"` (sanitizes to empty)
**Expected:** 400 error (secure_filename returns empty)
**Validates:** Sanitization edge case

### TEST B3: Duplicate Character Name
**Steps:** Create character "testchar", then create "testchar" again
**Expected:** 409 conflict error
**Validates:** UNIQUE constraint handling

### TEST B4: Upload Invalid File Type
**Steps:** Upload `.txt`, `.pdf`, `.exe` file as `before_photo`
**Expected:** File silently skipped, response shows 0 uploaded
**Validates:** Extension filtering

### TEST B5: Upload File with Uppercase Extension
**Steps:** Upload `photo.JPG` and `video.MP4`
**Expected:** Should be accepted (case-insensitive extension check)
**Validates:** Extension case handling (potential bug if case-sensitive)

### TEST B6: Upload Corrupt Image File
**Steps:** Rename a `.txt` file to `.jpg` and upload as `before_photo`
**Expected:** File accepted at upload but may fail during video generation (PIL/FFmpeg error)
**Validates:** Content validation gap

### TEST B7: Upload Corrupt Video File
**Steps:** Rename a `.jpg` to `.mp4` and upload as `before_video`
**Expected:** File accepted at upload but fails during FFmpeg processing
**Validates:** Content vs extension mismatch

### TEST B8: Upload Very Large File (100MB+)
**Steps:** Upload a 100MB+ video as `before_video`
**Expected:** Upload succeeds (no size limit), generation works
**Validates:** Large file handling, timeout risks

### TEST B9: Upload with Invalid asset_type
**Steps:** POST assets with `asset_type: "photo_before"` (wrong format)
**Expected:** 400 error
**Validates:** Strict enum validation

### TEST B10: Upload to Non-Existent Character
**Steps:** POST assets with invalid character_id UUID
**Expected:** 404 error
**Validates:** Foreign key integrity

### TEST B11: Delete Character While Job Processing
**Steps:** Start generation, immediately DELETE a character used in the job
**Expected:** Running job may fail for that character's videos; other character videos proceed
**Validates:** Concurrent deletion safety

### TEST B12: Character with Only Before Assets (No After)
**Steps:** Create character, upload only before_photos, then generate
**Expected:** Character skipped in combination generation, warning logged
**Validates:** Asymmetric asset handling

### TEST B13: Character with Only After Assets (No Before)
**Steps:** Create character, upload only after_photos, then generate
**Expected:** Character skipped, warning logged
**Validates:** Asymmetric asset handling

---

## CATEGORY C: Text & Emoji Rendering

### TEST C1: Text with Apple Emoji
**Steps:** Generate with hook_text: `"POV: you lost face fat 😱🔥"`, cta_text: `"see how ⬇️"`
**Expected:** Emoji renders as Apple Color Emoji in video, not as □ or missing
**Validates:** Emoji font loading and rendering pipeline

### TEST C2: Text with Only Emoji (No Words)
**Steps:** Generate with hook_text: `"🔥🔥🔥😱💪"`, cta_text: `"⬇️⬇️⬇️"`
**Expected:** Emoji-only text renders correctly, properly centered
**Validates:** Pure emoji text handling

### TEST C3: Text with Combined/Compound Emoji
**Steps:** hook_text with skin tone modifiers: `"glow up 👩🏽‍🦰✨"`, flag emojis: `"🇺🇸🏳️‍🌈"`
**Expected:** Compound emoji rendered as single glyph, not split
**Validates:** ZWJ and variation selector handling

### TEST C4: Very Long Text (200+ chars)
**Steps:** Generate with hook_text = 200-character sentence
**Expected:** Text wraps properly within box, box doesn't overflow image, font size may adjust
**Validates:** Word wrap + overflow protection

### TEST C5: Text with Special Characters
**Steps:** hook_text: `"it's a 50% glow-up! (seriously) — trust me"`
**Expected:** Apostrophes, percent, parentheses, em-dash render correctly
**Validates:** Character escaping (was breaking FFmpeg drawtext before)

### TEST C6: Text with Newlines
**Steps:** hook_text containing `\n` characters
**Expected:** Line breaks respected in rendering
**Validates:** Explicit newline handling

### TEST C7: Empty Hook Text
**Steps:** POST generate with `hook_text: ""`
**Expected:** 400 error (hook_text required)
**Validates:** Required field validation

### TEST C8: Empty CTA Text
**Steps:** POST generate with `hook_text: "valid"`, `cta_text: ""`
**Expected:** Videos generate without CTA overlay, or with empty box
**Validates:** Optional CTA handling

### TEST C9: Text Position Verification (+40px)
**Steps:** Generate video with hook text, screenshot the frame
**Expected:** Text box is positioned 40px lower than the 75% mark (~77% of frame height)
**Validates:** Recent +40px position fix

### TEST C10: Text on Video Clips (Not Just Photos)
**Steps:** Generate with asset_type=videos, verify hook text appears on before-video clips
**Expected:** Text rendered as PNG overlay composited via FFmpeg (not drawtext)
**Validates:** Video overlay pipeline (emoji support in video path)

---

## CATEGORY D: Generation & Combination Logic

### TEST D1: num_videos = 1 (Minimum)
**Steps:** Generate with num_videos=1
**Expected:** Exactly 1 video produced
**Validates:** Minimum boundary

### TEST D2: num_videos = 50 (Maximum)
**Steps:** Generate with num_videos=50 (needs enough assets for combos)
**Expected:** Up to 50 videos or as many unique combos as possible
**Validates:** Maximum boundary + dedup behavior when requested > available

### TEST D3: num_videos = 0 or Negative
**Steps:** POST generate with num_videos=0, then num_videos=-5
**Expected:** Clamped to 1 (min boundary)
**Validates:** Input clamping

### TEST D4: num_videos = 100 (Over Maximum)
**Steps:** POST generate with num_videos=100
**Expected:** Clamped to 50
**Validates:** Input clamping

### TEST D5: num_text_variations = 1 (No Variations)
**Steps:** Generate with num_text_variations=1
**Expected:** All videos use original hook/CTA text
**Validates:** Variation bypass

### TEST D6: num_text_variations = 10 (Maximum)
**Steps:** Generate with num_text_variations=10
**Expected:** 10 text variations generated (or clamped), used across videos
**Validates:** Maximum variation boundary

### TEST D7: More Videos Than Unique Combos
**Steps:** 1 character, 1 before-photo, 1 after-photo, 1 text variation, request 10 videos
**Expected:** Only 1 unique combo generated, warning logged, 1 video produced
**Validates:** Deduplication + graceful underproduction

### TEST D8: asset_type Mismatch — Request Photos, Only Videos Available
**Steps:** Character has only before_videos + after_videos, request asset_type=photos
**Expected:** Character skipped (no photo assets), job fails if no other characters
**Validates:** Asset type filtering

### TEST D9: asset_type = "both"
**Steps:** Character has both photos and videos, request asset_type=both
**Expected:** All assets used in combination pool, some videos use photos, some use video clips
**Validates:** Mixed asset type handling

### TEST D10: asset_type = Invalid Value
**Steps:** POST generate with asset_type="all" or asset_type="images"
**Expected:** 400 error
**Validates:** Enum validation

### TEST D11: Multiple Characters — Uneven Asset Counts
**Steps:** Character A: 10 before/10 after photos. Character B: 1 before/1 after photo. Request 20 videos.
**Expected:** More combos from Character A, fewer from B, all valid
**Validates:** Balanced distribution across characters

### TEST D12: Single Clip Format (e.g., only "before" clip)
**Steps:** Create format with only 1 clip of type "before"
**Expected:** Videos generated with just 1 clip each, no crash from missing after/cta clips
**Validates:** Minimal format handling

### TEST D13: Format with Unknown Clip Type
**Steps:** Edit format to have clip type "unknown_type"
**Expected:** Treated as transition (no text, uses before asset)
**Validates:** Unknown clip type fallback

---

## CATEGORY E: External Service Failures

### TEST E1: Claude API Unavailable for Text Variations
**Steps:** Set invalid ANTHROPIC_API_KEY, then generate with num_text_variations=3
**Expected:** Falls back to Gemini for text variations, generation proceeds
**Validates:** Claude → Gemini fallback

### TEST E2: Both Claude and Gemini Fail for Text Variations
**Steps:** Set invalid keys for both APIs, generate with num_text_variations=3
**Expected:** Job fails with clear error about text generation failure
**Validates:** Total text generation failure handling

### TEST E3: Google Drive API Failure
**Steps:** Temporarily invalidate Drive credentials, generate
**Expected:** Job fails at Drive folder creation step, error message includes "Drive"
**Validates:** Drive error handling

### TEST E4: Drive Upload Fails for Single Video (Not All)
**Steps:** Generate 3 videos where Drive upload intermittently fails
**Expected:** Failed-upload video marked completed (with error note), other videos uploaded normally
**Validates:** Partial upload failure handling

### TEST E5: RapidAPI Key Exhausted Mid-Scrape
**Steps:** Exhaust RapidAPI quota, then attempt format scrape
**Expected:** 500 error with quota/auth message
**Validates:** API quota handling

### TEST E6: Redis Down (Celery Queue Fails)
**Steps:** Stop Redis, then POST generate
**Expected:** 500 error about task queuing failure, job created but not processed
**Validates:** Message broker failure

---

## CATEGORY F: Resource & Infrastructure

### TEST F1: Concurrent Job Submission
**Steps:** Submit 3 generation jobs simultaneously (different formats/characters)
**Expected:** All 3 queued and processed (Celery concurrency=2, so 1 waits)
**Validates:** Concurrent processing

### TEST F2: Large Batch — 50 Videos
**Steps:** Generate 50 videos in single job (max allowed)
**Expected:** All 50 processed sequentially, no timeout, proper progress tracking
**Validates:** Large batch handling, long-running task stability

### TEST F3: FFmpeg Timeout
**Steps:** Generate with very large video asset (high-res, long duration)
**Expected:** FFmpeg respects 120s timeout, video marked failed, batch continues
**Validates:** Subprocess timeout handling

### TEST F4: Temporary Directory Cleanup
**Steps:** Generate videos, then check `/tmp` for leftover `photo_clip_*`, `text_overlay_*` files
**Expected:** All temp files cleaned up after job completes
**Validates:** Resource cleanup

### TEST F5: Job Polling After Completion
**Steps:** Poll job status endpoint repeatedly after job is completed
**Expected:** Always returns completed status with correct counts, no errors
**Validates:** Idempotent status endpoint

### TEST F6: Admin Panel — IG Reels Tab
**Steps:** Open http://31.97.123.84/admin/queue, click "IG Reels" tab
**Expected:** Shows jobs with stats, expandable video details, pagination works
**Validates:** Admin panel integration (just deployed)

---

## CATEGORY G: Emoji Rendering Specific (VPS vs Local)

### TEST G1: Emoji Font Availability on VPS
**Steps:** SSH to VPS, check: `ls /usr/share/fonts/truetype/AppleColorEmoji.ttf` or `Twemoji.ttf`
**Expected:** At least one emoji font exists (installed previously for TikTok slideshows)
**Validates:** Font availability prerequisite

### TEST G2: Emoji in Photo Clip (PIL Path)
**Steps:** Generate with photos + emoji in hook_text, download video, check before-clip frame
**Expected:** Emoji visible as color glyph (not black square or missing)
**Validates:** PIL emoji rendering via text_renderer.py

### TEST G3: Emoji in Video Clip (FFmpeg Overlay Path)
**Steps:** Generate with videos + emoji in hook_text, download video, check before-clip frames
**Expected:** Emoji visible as color glyph, same quality as photo path
**Validates:** FFmpeg overlay PNG compositing with emoji

### TEST G4: Mixed Emoji and Regular Text
**Steps:** hook_text: `"She lost 10 lbs in 2 weeks 🔥 crazy results"`
**Expected:** Text and emoji on same line, proper spacing, emoji at correct vertical position
**Validates:** Mixed text+emoji rendering alignment

### TEST G5: Emoji in CTA Text
**Steps:** cta_text: `"link in bio ⬇️🔗"` with CTA clip in format
**Expected:** CTA text box (white bg) shows emoji properly
**Validates:** CTA style rendering with emoji

---

## Execution Priority

### P0 — Must Test (Blocking for Production)
- C1, C9, C10 (emoji rendering + text position — today's fix)
- G1, G2, G3 (emoji on VPS — deployment verification)
- F6 (admin panel — just deployed)

### P1 — High Priority
- B12, B13 (missing assets)
- D7 (more videos than combos)
- D8 (asset type mismatch)
- E1, E3 (API failures)
- C4, C5 (long text, special chars)

### P2 — Medium Priority
- A1-A3, A10 (format edge cases)
- B4-B7 (bad file uploads)
- D1-D4 (num_videos boundaries)
- E6 (Redis down)
- F1-F3 (concurrency, large batch, timeout)

### P3 — Low Priority (Unlikely but Good Coverage)
- B2, B3 (name edge cases)
- A7-A9 (format edit edge cases)
- D12, D13 (unusual format structures)
- F4 (temp cleanup verification)

---

## How to Execute

**For API-level tests:** Use Chrome DevTools MCP console or `curl` from VPS
**For UI-level tests:** Navigate to http://31.97.123.84/instagram-reel
**For VPS verification:** `ssh root@31.97.123.84`
**Logs:** `journalctl -u celery-worker -f` and `journalctl -u tiktok-slideshow -f`
**DB check:** `sqlite3 /root/tiktok-slideshow-generator/Desktop/clients/tiktok/backend/batch_processing.db "SELECT id, status, error_message FROM ig_jobs ORDER BY created_at DESC LIMIT 10;"`
