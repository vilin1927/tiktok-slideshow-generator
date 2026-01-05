# TikTok Slideshow Generator - Project Goal

## What It Does
Transforms viral TikTok slideshows into custom branded content for Jenny's products automatically. Takes a viral TikTok URL and recreates the same winning format with new images and text tailored to Jenny's products.

## The Problem
Manually recreating viral TikTok content takes 1+ hours per slideshow - analyzing the aesthetic, writing compelling copy, creating matching images, replicating text styles. This process doesn't scale and requires significant creative effort for each piece of content.

## The Solution
User inputs a viral TikTok URL → AI analyzes everything → Generates new slideshow with:
- **Same vibe** (lighting, colors, composition, mood)
- **Same text style** (font, color, outline, position - copied exactly)
- **NEW images** relevant to Jenny's product
- **NEW text** tailored to product benefits and target audience

---

## Jenny's Products (Hardcoded - 3 Total)

1. **LumiDew Face Tape** - Invisible face lifting tape for instant snatched jawline
2. **LumiDew Steam Eye Mask** - Relaxing heated eye masks for self-care
3. **LumiDew Gua Sha Tool** - Rose quartz facial massage tool for lymphatic drainage

Each product has 10 pre-uploaded photos in Google Drive for the product slide.

---

## How It Works

### Step 1: ANALYZE (Gemini 3  flash Preview - for understanding)
- Extract slideshow structure (how many slides, what type each)
- Extract text style (color, weight, outline, position, background)
- Extract image aesthetic (lighting, colors, composition, mood)
- Understand content pattern (hook → body tips → product → CTA)
- Identify target audience and content angle

### Step 2: GENERATE (Nano Banana Pro - $0.134/image)
- Create NEW images matching viral aesthetic
- Generate NEW text relevant to Jenny's specific product
- Apply SAME text style from viral reference
- Use XML-structured prompts for consistent results

### Step 3: OVERLAY (for product slides only)
- Use Jenny's actual product photo (randomly selected from 10 pre-uploaded)
- Add text overlay ONLY - do NOT modify the photo
- Text positioned to NOT cover the product

---

## Slide Types & Actions

| Slide Type | Image | Text |
|------------|-------|------|
| Hook | Generate NEW similar image | Generate NEW hook for product |
| Bridge/Proof | Generate NEW similar image | Generate NEW transformation text |
| Body/Tips | Generate NEW similar images | Generate NEW tips/benefits |
| Product | Jenny's REAL photo (unchanged) | Text overlay only |
| CTA | Generate NEW graphic | Engagement question or user's CTA |

---

## Manual Testing Results ✅

We conducted extensive manual testing in Google AI Studio to validate the approach:

### Test 1: Body Slide Generation
- **Input:** Viral bathroom skincare slide as reference
- **Output:** Generated new bathroom scene with "LumiDew" branded products
- **Result:** ✅ Excellent - matched aesthetic, correct text style (white + black outline)

### Test 2: Flat Lay Generation
- **Input:** Viral product flat lay reference
- **Output:** Generated face tape strips with beauty tools
- **Result:** ✅ Excellent - created contextually relevant imagery

### Test 3: CTA Slide Generation
- **Input:** Viral "Lmk what glow up tips" graphic with vegetable sketches
- **Output:** Generated same style with skincare sketches (serums, tape, jars)
- **Result:** ✅ Perfect - matched font, highlight box, even changed illustrations to be beauty-relevant

### Test 4: Product Photo Overlay
- **Input:** Jenny's actual Face Tape photo + style reference
- **Output:** Added text overlay at bottom without covering product
- **Result:** ✅ Works - text positioned correctly, product visible

### Prompt Format Tested
XML-structured prompts showed best results:
```xml
<task>Add text overlay to the product photo</task>
<images>
  <image1 role="style_reference">Use ONLY for text style</image1>
  <image2 role="target">ADD TEXT TO THIS IMAGE</image2>
</images>
<text_to_add>my secret for a snatched jawline 🔥</text_to_add>
<text_style>
  - Color/Weight/Outline: Exactly the same as reference
  - Position: Choose best to not cover product
</text_style>
<rules>
  - Do NOT cover or overlap the product
  - Product must remain fully visible
  - Do NOT modify the original photo
</rules>
```

---

## Key Rules (from Jenny)

1. **Structure is DYNAMIC** - matches viral TikTok (6, 9, 12 slides - whatever it has)
2. **Text STYLE copied exactly** - font, color, outline, position from reference
3. **Text CONTENT is NEW** - relevant to Jenny's specific product
4. **Product photos UNMODIFIED** - only text overlay added
5. **Text NEVER covers the product** - positioned around it
6. **User can provide CTA** - "link in bio", "shop on Amazon", etc.

---

## Technical Stack

- **Analysis**: Gemini 3 Pro Preview (`gemini-3-pro-preview`)
- **Image Generation**: Gemini 3 Pro Image (`gemini-3-pro-image-preview`)
- **TikTok Scraping**: RapidAPI TikTok Scraper
- **Storage**: Google Drive (product photos + outputs)
- **Backend**: Python or Node.js (proper backend solution)

> **Note:** Previous solution used n8n workflow. Will switch to proper backend (Python/Node.js) for better control, error handling, and scalability.

---

## User Inputs

1. Viral TikTok slideshow URL
2. Select product (Face Tape / Steam Eye Mask / Gua Sha Tool)
3. Optional: Custom CTA text ("link in bio", "shop on Amazon", etc.)

## Outputs

- Complete slideshow images (matching viral slide count)
- Saved to Google Drive folder
- Ready to upload to TikTok

---

## Cost Per Slideshow

| Item | Cost |
|------|------|
| Analysis (Gemini flash) | ~$0.02-0.05 |
| Image generation (9 slides avg) | ~$1.20 |
| **Total per slideshow** | **~$1.25** |

*Cost varies based on number of slides in viral TikTok*

---

## What's NOT Included

- Music extraction (images only)
- Video assembly (slideshow images only)
- Multiple user accounts/authentication
- Custom product uploads (3 hardcoded products only)
- More than 3 products without additional development