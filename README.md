# TikTok Slideshow Generator

Generate viral-style slideshows for your products using AI-powered story adaptation.

## Project Structure

```
tiktok-slideshow-generator/
├── backend/           # Flask API + Celery workers
│   ├── app.py         # Main Flask application
│   ├── tasks.py       # Celery background tasks
│   ├── gemini_service_v2.py  # AI content generation
│   └── ...
├── frontend/          # Static HTML/JS frontend
│   └── index.html
├── deploy.sh          # Deployment script
└── README.md
```

## Setup

### Prerequisites
- Python 3.10+
- Redis (for Celery)
- Google Cloud credentials (for Drive API)
- Gemini API key
- RapidAPI key (for TikTok scraping)

### Local Development

1. Create virtual environment:
```bash
python3 -m venv venv
source venv/bin/activate
```

2. Install dependencies:
```bash
pip install -r backend/requirements.txt
```

3. Create `.env` file in `backend/`:
```bash
cp backend/.env.example backend/.env
# Edit .env with your API keys
```

4. Run the app:
```bash
cd backend
python app.py
```

5. Run Celery worker (separate terminal):
```bash
cd backend
celery -A celery_app worker --loglevel=info
```

### VPS Deployment

```bash
./deploy.sh
```

## Features

- Single link slideshow generation
- Batch processing (multiple links)
- Text preset system with safe zone detection
- Video generation
- Google Drive export
