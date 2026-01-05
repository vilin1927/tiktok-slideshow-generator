# PRD: TikTok Slideshow Generator

## Introduction/Overview

The TikTok Slideshow Generator is a web application that allows users to create viral-style TikTok slideshows for their products by analyzing existing viral TikTok slideshows and replicating their visual style, text fonts, and structure. Users provide a viral TikTok link and their product photos, and the system generates new slideshow images that match the viral content's aesthetic while featuring the user's product.

**Problem Solved:** Creating viral TikTok slideshow content requires design skills and understanding of trending styles. This tool automates the process by cloning the style of proven viral content and applying it to new products.

## Goals

1. Enable users to generate TikTok slideshow images that match the style of viral content
2. Reduce content creation time from hours to minutes
3. Allow batch generation of multiple variations for A/B testing
4. Provide seamless output delivery via Google Drive

## User Stories

1. **As a product seller**, I want to paste a viral TikTok slideshow link and have the system analyze its style, so I can replicate what's already working.

2. **As a content creator**, I want to upload my product photos and have AI generate hook/body slides in the viral style, so I don't need design skills.

3. **As a marketer**, I want to generate multiple variations of each slide type, so I can test different versions and find what converts best.

4. **As a user**, I want to receive my generated images and audio in a Google Drive folder, so I can easily access and upload them to TikTok.

## Functional Requirements

### Input Collection (Web Form)

1. The system must accept a viral TikTok slideshow URL as input
2. The system must accept product photos upload (up to 10 images)
3. The system must accept a folder name for organizing output
4. The system must allow users to specify the number of variations for:
   - Hook slide (always 1 in source, first slide)
   - Body slides (variable count, all middle slides)
   - Product slide (always 1 in source, last slide)
5. The system must display uploaded product photos in the interface for user preview

### TikTok Analysis & Extraction

6. The system must extract individual slide images from the viral TikTok using RapidAPI (or similar scraper)
7. The system must extract the audio track from the viral TikTok
8. The system must identify and categorize slides into: hook (first slide), body (middle slides), product (last slide) - dynamic count
9. The system must extract all slides as individual images for use as Gemini reference inputs

### AI Image Generation (Gemini 2.0 Flash)

10. **Single-Step Style Transfer**: For each viral slide, the system must send the original image to Gemini and request a new scene that matches:
    - The exact visual style/aesthetic of the reference image
    - The exact text font style of the reference image
    - But with new, contextually appropriate text for the user's product
11. **Hook Slide Generation**: Generate new hook slides matching viral hook's complete style (visuals + text style) adapted for user's product
12. **Body Slides Generation**: Generate new body slides matching each viral body slide's complete style adapted for user's product
13. **Product Slide Generation**: Take user's product photo and apply text overlay matching the viral product slide's text style (no image generation, just text overlay)
14. The system must generate the specified number of variations for each slide type (max 5 variations per slide type)

### Output & Storage

15. The system must create a Google Drive folder with the user-specified name
16. The system must upload all generated images to the Google Drive folder
17. The system must upload the extracted audio file to the Google Drive folder
18. The system must provide a direct link to the Google Drive folder in the web interface
19. The system must display a processing status message (e.g., "Processing... please wait a few minutes")
20. The system must use a service account for Google Drive (no per-user OAuth required)

### Web Interface

21. The system must provide a web form for all inputs
22. The system must show a preview of uploaded product images
23. The system must display the Google Drive folder link upon completion
24. The system must show processing status while generating content

## Non-Goals (Out of Scope)

1. **Video rendering**: This tool generates slideshow images, not video files
2. **Direct TikTok posting**: Users manually upload to TikTok from the generated assets
3. **Audio editing/modification**: Audio is extracted as-is from viral TikTok
4. **User authentication system**: Single-user/simple access (no login required)
5. **Scheduling or automation**: One-off generation only
6. **Analytics or tracking**: No performance tracking of generated content

## Technical Considerations

### APIs & Services
- **Gemini 3 Pro** (`gemini-3-pro-preview`): Analysis, slide categorization, text generation
- **Gemini 3 Pro Image** (`gemini-3-pro-image-preview`): Image generation and style transfer
- **RapidAPI TikTok Scraper**: Extract slides and audio from TikTok URLs
- **Google Drive API**: File storage and folder management (service account auth)

### Architecture
- **Backend**: Python (Flask or FastAPI)
- **Frontend**: Alpine.js + Tailwind CSS (lightweight, perfect for form-based MVP)
- **Storage**: Google Drive via service account (credentials stored server-side)
- **Database**: None for MVP (can add SQLite later for run history)

### Gemini API Reference
- Image generation docs: https://ai.google.dev/gemini-api/docs/image-generation
- **Analysis model**: `gemini-3-pro-preview` - for categorizing slides, generating text content
- **Image model**: `gemini-3-pro-image-preview` - for generating/editing images with style transfer

### Key Technical Challenges
1. **Style transfer via Gemini**: Ensuring Gemini accurately replicates both visual style and text font style from reference images in a single generation step
2. **Slide categorization**: Automatically identifying hook vs body vs product slides from extracted TikTok content
3. **Contextual text generation**: Generating new text that matches the vibe/aesthetic but is logically appropriate for the user's product

## Design Considerations

### Web Form Layout
- Viral TikTok URL input field
- Product photos upload area (drag & drop, shows previews)
- Folder name input
- Three numeric inputs for variation counts (hook, body, product) - max 5 each
- Generate button
- Status display area
- Result link display area

### Processing Flow (User Experience)
1. User fills form and clicks Generate
2. Interface shows "Analyzing viral TikTok..."
3. Interface shows "Generating slides..."
4. Interface shows "Uploading to Google Drive..."
5. Interface displays clickable link to Google Drive folder

## Success Metrics

1. Successfully extract slides and audio from 95%+ of valid TikTok slideshow URLs
2. Generated images visually match the style of viral source content
3. Text fonts/styles are accurately replicated on generated images
4. Complete generation process in under 5 minutes for standard requests
5. All outputs successfully uploaded to Google Drive with working links

## Open Questions

1. How should the system handle rate limits from TikTok scraper or Gemini API?
2. Should slide categorization (hook/body/product) be automatic or user-defined?
3. What product context should the user provide for AI text generation (product name, description, key features)?
