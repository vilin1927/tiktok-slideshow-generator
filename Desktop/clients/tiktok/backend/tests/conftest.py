"""
Shared pytest fixtures for TikTok Slideshow Generator tests.
"""
import os
import sys
import pytest
import tempfile
import shutil
from io import BytesIO
from PIL import Image

# Add backend directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(scope='session')
def app():
    """Create Flask application for testing."""
    # Set test environment variables before importing app
    os.environ['TESTING'] = 'true'
    os.environ['ADMIN_PASSWORD'] = 'test_password_123'

    from app import app as flask_app
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False

    yield flask_app


@pytest.fixture
def client(app):
    """Create Flask test client."""
    return app.test_client()


@pytest.fixture
def runner(app):
    """Create Flask CLI test runner."""
    return app.test_cli_runner()


@pytest.fixture(scope='function')
def test_db():
    """Create isolated test database."""
    # Create a temporary database file
    temp_db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
    temp_db.close()

    # Set the database path
    original_db = os.environ.get('DATABASE_PATH')
    os.environ['DATABASE_PATH'] = temp_db.name

    # Initialize database
    from database import init_db
    init_db()

    yield temp_db.name

    # Cleanup
    if original_db:
        os.environ['DATABASE_PATH'] = original_db
    else:
        os.environ.pop('DATABASE_PATH', None)

    try:
        os.unlink(temp_db.name)
    except:
        pass


@pytest.fixture
def sample_image():
    """Create a sample test image."""
    img = Image.new('RGB', (1080, 1920), color='blue')
    img_bytes = BytesIO()
    img.save(img_bytes, format='PNG')
    img_bytes.seek(0)
    return img_bytes


@pytest.fixture
def sample_image_file(sample_image, tmp_path):
    """Create a sample test image file on disk."""
    img_path = tmp_path / "test_image.png"
    img = Image.new('RGB', (1080, 1920), color='red')
    img.save(str(img_path))
    return str(img_path)


@pytest.fixture
def sample_audio_file(tmp_path):
    """Create a minimal sample audio file path."""
    # We'll create a placeholder - real audio tests would need actual audio
    audio_path = tmp_path / "test_audio.mp3"
    # Create empty file as placeholder
    audio_path.touch()
    return str(audio_path)


@pytest.fixture
def temp_output_dir(tmp_path):
    """Create temporary output directory."""
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    return str(output_dir)


@pytest.fixture
def mock_gemini_response():
    """Mock Gemini API response for testing."""
    return {
        "analysis": {
            "product_name": "Test Product",
            "brand": "TestBrand",
            "hook_text": "Amazing Product!",
            "body_texts": ["Feature 1", "Feature 2", "Feature 3"],
            "product_text": "Get yours today!"
        }
    }


@pytest.fixture
def valid_tiktok_urls():
    """Sample valid TikTok URLs for testing."""
    return [
        "https://www.tiktok.com/@user/photo/1234567890123456789",
        "https://www.tiktok.com/@testuser/video/9876543210987654321",
        "https://vm.tiktok.com/ABC123xyz/",
        "https://www.tiktok.com/t/ZT8example/"
    ]


@pytest.fixture
def invalid_tiktok_urls():
    """Sample invalid TikTok URLs for testing."""
    return [
        "https://www.youtube.com/watch?v=abc123",
        "https://www.instagram.com/p/abc123",
        "not-a-url",
        "",
        "https://tiktok.com/invalid"
    ]


@pytest.fixture
def preset_ids():
    """All valid preset IDs."""
    return [
        'classic_shadow', 'classic_outline', 'classic_box',
        'elegance_shadow', 'elegance_outline', 'elegance_box',
        'vintage_shadow', 'vintage_outline', 'vintage_box'
    ]


@pytest.fixture
def admin_token(client):
    """Get admin authentication token."""
    response = client.post('/api/admin/login', json={
        'password': 'test_password_123'
    })
    if response.status_code == 200:
        return response.get_json().get('token')
    return None


# Mock fixtures for external services
@pytest.fixture
def mock_rapidapi(monkeypatch):
    """Mock RapidAPI TikTok scraper."""
    def mock_scrape(*args, **kwargs):
        return {
            'images': ['/tmp/test1.jpg', '/tmp/test2.jpg'],
            'audio': '/tmp/test_audio.mp3'
        }

    monkeypatch.setattr('tiktok_scraper.scrape_tiktok_slideshow', mock_scrape)


@pytest.fixture
def mock_gemini(monkeypatch):
    """Mock Gemini API."""
    def mock_pipeline(*args, **kwargs):
        return {
            'generated_images': {
                'p1_t1': ['/tmp/gen1.png', '/tmp/gen2.png']
            },
            'analysis': {
                'product_name': 'Test Product',
                'brand': 'TestBrand'
            }
        }

    monkeypatch.setattr('gemini_service_v2.run_pipeline', mock_pipeline)


@pytest.fixture
def mock_google_drive(monkeypatch):
    """Mock Google Drive upload."""
    def mock_upload(*args, **kwargs):
        return {
            'folder_id': 'test_folder_123',
            'folder_link': 'https://drive.google.com/test'
        }

    monkeypatch.setattr('google_drive.upload_slideshow_output', mock_upload)
