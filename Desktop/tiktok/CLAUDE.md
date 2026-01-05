# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

TikTok Slideshow Generator - A web application that analyzes viral TikTok slideshows and generates new slides matching the visual/text style for user products.

## Tech Stack

- **Backend**: Python Flask
- **Frontend**: Alpine.js + Tailwind CSS (CDN)
- **APIs**:
  - Gemini 2.0 Flash (analysis) + Gemini 2.0 Flash Exp (image generation)
  - RapidAPI TikTok Scraper
  - Google Drive API (service account)

## Development Commands

```bash
# Setup
python3 -m venv venv
source venv/bin/activate
pip install -r backend/requirements.txt

# Run backend (port 5001)
cd backend && python app.py

# Frontend - open in browser
open frontend/index.html
```

## Environment Variables

Copy `backend/.env.example` to `backend/.env` and set:
- `RAPIDAPI_KEY` - RapidAPI key for TikTok scraper
- `GEMINI_API_KEY` - Google Gemini API key
- `GOOGLE_APPLICATION_CREDENTIALS` - Path to service account JSON

## Directory Structure

```
backend/
  app.py              - Flask API endpoints
  tiktok_scraper.py   - TikTok scraping module
  gemini_service.py   - Gemini API integration
  google_drive.py     - Google Drive uploads
frontend/
  index.html          - Alpine.js + Tailwind UI
credentials/          - Google service account (gitignored)
tasks/                - PRDs and task lists
docs/                 - Workflow documentation
```

## API Endpoints

- `GET /api/health` - Health check
- `POST /api/generate` - Main generation endpoint
- `POST /api/test-scrape` - Test TikTok scraping

## Documentation Workflow

The `/docs/` directory contains workflow rules:
- **create-prd.md**: PRD generation guidelines
- **generate-tasks.md**: Task list generation guidelines
