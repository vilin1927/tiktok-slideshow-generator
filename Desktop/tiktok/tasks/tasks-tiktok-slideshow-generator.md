## Relevant Files

- `backend/app.py` - Main Flask application with API endpoints
- `backend/tiktok_scraper.py` - TikTok scraping module (RapidAPI integration)
- `backend/gemini_service.py` - Gemini API integration (analysis + image generation)
- `backend/google_drive.py` - Google Drive upload and folder management
- `backend/requirements.txt` - Python dependencies
- `backend/.env.example` - Environment variables template
- `frontend/index.html` - Main frontend (Alpine.js + Tailwind CSS)
- `credentials/` - Google service account credentials (gitignored)

### Notes

- Use `pip install -r backend/requirements.txt` to install dependencies
- Use `python backend/app.py` to run the Flask server
- Environment variables needed: `RAPIDAPI_KEY`, `GEMINI_API_KEY`, `GOOGLE_APPLICATION_CREDENTIALS`
- Google Drive service account credentials JSON should be placed in `credentials/` folder

## Instructions for Completing Tasks

**IMPORTANT:** As you complete each task, you must check it off in this markdown file by changing `- [ ]` to `- [x]`. This helps track progress and ensures you don't skip any steps.

Example:
- `- [ ] 1.1 Read file` → `- [x] 1.1 Read file` (after completing)

Update the file after completing each sub-task, not just after completing an entire parent task.

## Tasks

- [x] 0.0 Create feature branch
  - [x] 0.1 Create and checkout a new branch: `git checkout -b feature/tiktok-slideshow-generator`

- [x] 1.0 Set up project structure and dependencies
  - [x] 1.1 Create `backend/` directory
  - [x] 1.2 Create `frontend/` directory
  - [x] 1.3 Create `credentials/` directory and add to `.gitignore`
  - [x] 1.4 Create `backend/requirements.txt` with dependencies (flask, google-generativeai, google-api-python-client, google-auth, requests, python-dotenv, flask-cors)
  - [x] 1.5 Create `backend/.env.example` with required environment variables
  - [x] 1.6 Create `backend/app.py` with Flask boilerplate and CORS setup
  - [x] 1.7 Install dependencies and verify setup works

- [x] 2.0 Implement TikTok scraper integration (RapidAPI)
  - [x] 2.1 Research RapidAPI TikTok scraper endpoints for slideshow extraction
  - [x] 2.2 Create `backend/tiktok_scraper.py` module
  - [x] 2.3 Implement `extract_slideshow_images(tiktok_url)` - returns list of image URLs
  - [x] 2.4 Implement `extract_audio(tiktok_url)` - returns audio URL or file
  - [x] 2.5 Implement `download_media(url, save_path)` - downloads images/audio locally
  - [x] 2.6 Add error handling for invalid URLs, private videos, API failures
  - [ ] 2.7 Test scraper with sample TikTok slideshow URLs

- [x] 3.0 Implement Gemini API integration (analysis + image generation)
  - [x] 3.1 Create `backend/gemini_service.py` module
  - [x] 3.2 Implement `analyze_slides(images)` using `gemini-3-pro-preview` - categorizes slides into hook/body/product
  - [x] 3.3 Implement `generate_hook_slide(reference_image, product_context, variation_count)` using `gemini-3-pro-image-preview`
  - [x] 3.4 Implement `generate_body_slides(reference_images, product_context, variation_count)` - generates variations for each body slide
  - [x] 3.5 Implement `generate_product_slide(product_image, reference_style_image, variation_count)` - applies text overlay only (no image generation)
  - [x] 3.6 Ensure generated images match viral style (visuals + text font style)
  - [x] 3.7 Add retry logic and error handling for API rate limits
  - [ ] 3.8 Test each generation function with sample inputs

- [x] 4.0 Implement Google Drive integration
  - [ ] 4.1 Set up Google Cloud project and enable Drive API (user task)
  - [ ] 4.2 Create service account and download credentials JSON to `credentials/` (user task)
  - [x] 4.3 Create `backend/google_drive.py` module
  - [x] 4.4 Implement `create_folder(folder_name)` - creates folder, returns folder ID
  - [x] 4.5 Implement `upload_file(file_path, folder_id)` - uploads file to folder
  - [x] 4.6 Implement `set_folder_public(folder_id)` - makes folder publicly viewable
  - [x] 4.7 Implement `get_folder_link(folder_id)` - returns shareable link
  - [ ] 4.8 Test full upload flow: create folder → upload files → get link

- [x] 5.0 Build backend API endpoints
  - [x] 5.1 Create `POST /api/generate` endpoint in `app.py`
  - [x] 5.2 Handle multipart form data: TikTok URL, product images, folder name, variation counts
  - [x] 5.3 Validate inputs (required fields, max 5 variations per type, max 10 product images)
  - [x] 5.4 Implement processing pipeline:
    - Scrape TikTok → get slides + audio
    - Analyze slides → categorize hook/body/product
    - Generate new slides with variations
    - Upload all to Google Drive
  - [x] 5.5 Return JSON response with Google Drive folder link
  - [x] 5.6 Add appropriate error responses (400 for validation, 500 for processing errors)
  - [ ] 5.7 Test endpoint with Postman or curl

- [x] 6.0 Build frontend with Alpine.js + Tailwind CSS
  - [x] 6.1 Create `frontend/index.html` with Tailwind CDN and Alpine.js CDN
  - [x] 6.2 Build form structure:
    - TikTok URL input
    - Product images upload (multiple files)
    - Folder name input
    - Hook variations count (1-5)
    - Body variations count (1-5)
    - Product variations count (1-5)
    - Generate button
  - [x] 6.3 Implement product image preview grid using Alpine.js
  - [x] 6.4 Add client-side validation (required fields, max values)
  - [x] 6.5 Implement form submission via fetch to backend API
  - [x] 6.6 Add loading state with status messages ("Analyzing TikTok...", "Generating slides...", "Uploading to Drive...")
  - [x] 6.7 Display Google Drive folder link on success (clickable, opens in new tab)
  - [x] 6.8 Display error messages on failure
  - [x] 6.9 Style with Tailwind for clean, modern appearance

- [ ] 7.0 Integration and end-to-end testing
  - [ ] 7.1 Test complete flow: form submission → processing → Drive link
  - [ ] 7.2 Verify extracted slides are correctly categorized
  - [ ] 7.3 Verify generated images match viral style aesthetics
  - [ ] 7.4 Verify text overlays match viral font style
  - [ ] 7.5 Verify Google Drive folder contains all outputs (images + audio)
  - [ ] 7.6 Test with multiple different TikTok slideshow URLs
  - [ ] 7.7 Test error handling (invalid URL, API failures, etc.)
  - [ ] 7.8 Fix any bugs discovered during testing
