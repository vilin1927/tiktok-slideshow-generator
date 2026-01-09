# Batch Links Feature - Product Spec

**Contract**: $180 | **Revenue**: $162 after fees

---

## What We're Building

Jenny wants to process up to 100 TikTok slideshow links at once instead of one at a time. She also wants control over output settings like image variations and file format.

---

## Feature 1: Batch Link Input

### The Problem
Currently user enters one TikTok link at a time. Jenny has spreadsheets with 100+ links she wants to process in bulk.

### The Solution
A large text box where user can paste all their links at once in any format.

### How It Works
1. User copies a column of links from Google Sheets
2. User pastes into the text box
3. System automatically detects and separates the links
4. System validates each link and shows count: "✅ 98 valid | ❌ 2 invalid"
5. User can click to see which specific links are invalid and why

### Accepted Input Formats
- Links on separate lines (from Google Sheets copy)
- Links separated by commas
- Links separated by tabs
- Any mix of the above

### Validation Rules
- Must be a TikTok URL
- Must be a photo/slideshow link (not just any TikTok)
- Duplicate links should be flagged
- Maximum 100 links per batch

### User Feedback
- Real-time count of valid vs invalid links as user types/pastes
- "View Invalid" button shows list of problem links with reasons
- Warning if user tries to paste more than 100 links

---

## Feature 2: Parallel Processing

### The Problem
Processing links one after another is slow. 100 links × 2 minutes each = 3+ hours of waiting.

### The Solution
Process multiple links at the same time (in parallel).

### How It Works
1. User submits batch of 100 links
2. System processes 5-10 links simultaneously
3. As each finishes, next one starts automatically
4. Total time reduced from hours to ~20-30 minutes

### Technical Constraints
- Maximum 5-10 concurrent processes (to avoid API rate limits)
- If one link fails, others continue processing
- Failed links are logged for retry

---

## Feature 3: Progress Tracking

### The Problem
User has no idea how long 100 links will take or which ones are done.

### The Solution
Real-time progress display showing exactly what's happening.

### What User Sees
```
Processing Batch: 47/100 links complete

████████████░░░░░░░░░ 47%

✅ 45 completed successfully
⏳ 2 currently processing
❌ 0 failed

Estimated time remaining: ~12 minutes
```

### Progress Updates
- Progress bar fills as links complete
- Counter shows X of Y completed
- Status shows how many succeeded/failed/in-progress
- Time estimate updates as processing continues

### When Complete
- Summary shows total success/fail counts
- Download button for all completed slideshows
- List of any failed links with error reasons
- Option to retry failed links

---

## Feature 4: Photo Variations Control

### The Problem
User wants to choose how many different image variations to generate per slideshow.

### The Solution
Simple number input where user sets desired photo variations.

### How It Works
- Input field labeled "Photo Variations per Link"
- Minimum: 1, Maximum: 10
- Default: 3
- Applies to ALL links in the batch

### Example
User sets Photo Variations = 3
Each of the 100 TikTok links generates 3 different image sets
Total output: 300 slideshow variations

---

## Feature 5: Text Variations Control

### The Problem
User wants different text/copy variations for each slideshow.

### The Solution
Number input for text variations, with auto-calculated totals.

### How It Works
- Input field labeled "Text Variations per Link"
- Minimum: 1, Maximum: 10
- Default: 2
- Shows calculated total: "Total per link: Photo × Text = 6 variations"

### Example
Photo Variations = 3, Text Variations = 2
Each TikTok link produces: 3 × 2 = 6 total slideshow variations
100 links × 6 variations = 600 total outputs

---

## Feature 6: JPEG Output Format

### The Problem
Current output is PNG which creates large files. Jenny wants smaller JPEG files.

### The Solution
Change default output format from PNG to JPEG.

### Settings
- Format: JPEG
- Quality: 85-90% (good balance of quality vs file size)
- This applies to all generated slideshow images

### Expected Result
- File sizes reduced by ~60-70%
- Faster downloads and uploads
- Minimal visible quality loss

---

## Feature 7: Gemini API Key Management

### The Problem
Jenny needs to use her own Gemini API key and be able to change it anytime.

### The Solution
Settings panel where user can enter and update their API key.

### How It Works
1. Settings icon/link in the interface
2. Opens settings panel
3. API Key field (shows dots for security, like a password)
4. "Update" button saves new key
5. System validates key is working
6. Shows status: "✅ Key is valid" or "❌ Key is invalid"

### Security
- Key is masked when displayed (••••••••••••)
- Key is stored securely (not visible in browser)
- Key is validated before saving

---

## UI Layout Overview

### Main Form (Updated)

```
┌─────────────────────────────────────────────────────────────┐
│  TikTok Slideshow Generator                    [⚙️ Settings]│
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  📋 TikTok Links                                            │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ Paste links here (from Sheets, CSV, or any list)    │   │
│  │                                                     │   │
│  │ https://tiktok.com/@user/photo/123                  │   │
│  │ https://vm.tiktok.com/abc                           │   │
│  │ ...                                                 │   │
│  └─────────────────────────────────────────────────────┘   │
│  ✅ 98 valid  ❌ 2 invalid  [View Invalid]                  │
│                                                             │
│  ─────────────────────────────────────────────────────────  │
│                                                             │
│  📸 Product Photos                                          │
│  [Upload Zone - drag & drop or click]                       │
│                                                             │
│  📝 Product Name                                            │
│  [Text input field]                                         │
│                                                             │
│  📄 Product Description                                     │
│  [Text area]                                                │
│                                                             │
│  🔗 Product Page URL (optional - for auto-fill)             │
│  [URL input field]                                          │
│                                                             │
│  ─────────────────────────────────────────────────────────  │
│                                                             │
│  ⚙️ Generation Settings                                     │
│                                                             │
│  Photo Variations: [3 ▼]     Text Variations: [2 ▼]        │
│                                                             │
│  Total per link: 6 variations                               │
│  Total for batch: 588 variations (98 links × 6)             │
│                                                             │
│  ─────────────────────────────────────────────────────────  │
│                                                             │
│           [ 🚀 Generate All Slideshows ]                    │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### Settings Panel

```
┌─────────────────────────────────────────────────────────────┐
│  ⚙️ Settings                                         [✕]    │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  Gemini API Key                                             │
│  ┌─────────────────────────────────────┐                   │
│  │ ••••••••••••••••••••••••           │  [Update]          │
│  └─────────────────────────────────────┘                   │
│  ✅ Key is valid                                            │
│                                                             │
│  Output Format                                              │
│  ○ PNG (larger, lossless)                                  │
│  ● JPEG (smaller, recommended)                             │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### Processing Progress Screen

```
┌─────────────────────────────────────────────────────────────┐
│  🔄 Processing Batch                                        │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  Progress: 47 of 98 links                                   │
│                                                             │
│  ████████████████░░░░░░░░░░░░░░░  48%                      │
│                                                             │
│  ✅ 45 completed                                            │
│  ⏳ 2 in progress                                           │
│  ❌ 0 failed                                                │
│                                                             │
│  Estimated time remaining: ~12 minutes                      │
│                                                             │
│  ─────────────────────────────────────────────────────────  │
│                                                             │
│  Recent Activity:                                           │
│  • ✅ Link 47: @skincare_tips/photo/789 - Done             │
│  • ⏳ Link 48: @beauty_finds/photo/012 - Processing...     │
│  • ⏳ Link 49: @deals_daily/photo/345 - Processing...      │
│                                                             │
│           [ Cancel Batch ]                                  │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### Completion Screen

```
┌─────────────────────────────────────────────────────────────┐
│  ✅ Batch Complete!                                         │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  98 links processed                                         │
│                                                             │
│  ✅ 95 successful (570 slideshows generated)               │
│  ❌ 3 failed                                                │
│                                                             │
│  [ 📥 Download All (2.3 GB) ]    [ 🔄 Retry Failed ]       │
│                                                             │
│  ─────────────────────────────────────────────────────────  │
│                                                             │
│  Failed Links:                                              │
│  • https://tiktok.com/... - Video unavailable              │
│  • https://tiktok.com/... - Private account                │
│  • https://tiktok.com/... - Rate limit (retry later)       │
│                                                             │
│           [ Start New Batch ]                               │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

---

## Acceptance Criteria

### Must Have (for $180 payment)
- [ ] Can paste 100 links at once in any format
- [ ] Links are validated with clear feedback
- [ ] All valid links process in parallel (not one-by-one)
- [ ] Progress is visible in real-time
- [ ] User can set photo variation count
- [ ] User can set text variation count
- [ ] Output files are JPEG format
- [ ] User can change Gemini API key in settings

### Nice to Have (if time permits)
- [ ] Retry failed links button
- [ ] Download all as ZIP
- [ ] Save settings between sessions
- [ ] Processing time estimates

---

## Delivery Checklist

Before marking complete:
- [ ] Test with actual 100 links from Jenny
- [ ] Verify parallel processing works (check timestamps)
- [ ] Confirm JPEG output and file sizes
- [ ] Test API key validation
- [ ] Record demo video for Jenny
- [ ] Get Jenny's sign-off