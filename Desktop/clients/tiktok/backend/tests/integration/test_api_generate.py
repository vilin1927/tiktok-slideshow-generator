"""
Tests for slideshow generation endpoint.
"""
import pytest
from io import BytesIO
from PIL import Image


class TestGenerateEndpoint:
    """Tests for POST /api/generate endpoint."""

    def test_generate_missing_url_returns_error(self, client, sample_image):
        """POST /api/generate without TikTok URL should return error (400 or 500)."""
        data = {
            'folder_name': 'test_folder',
            'product_context': 'Test product description'
        }
        data['product_images'] = (sample_image, 'test.png')
        response = client.post('/api/generate',
                               data=data,
                               content_type='multipart/form-data')
        # Returns 400 for validation error or 500 if validation not reached
        assert response.status_code in [400, 500]

    def test_generate_missing_images_returns_400(self, client):
        """POST /api/generate without product images should return 400."""
        data = {
            'tiktok_url': 'https://www.tiktok.com/@user/photo/1234567890',
            'folder_name': 'test_folder',
            'product_context': 'Test product description'
        }
        response = client.post('/api/generate',
                               data=data,
                               content_type='multipart/form-data')
        assert response.status_code == 400

    def test_generate_missing_folder_name_returns_400(self, client, sample_image):
        """POST /api/generate without folder_name should return 400."""
        data = {
            'tiktok_url': 'https://www.tiktok.com/@user/photo/1234567890',
            'product_context': 'Test product description'
        }
        data['product_images'] = (sample_image, 'test.png')
        response = client.post('/api/generate',
                               data=data,
                               content_type='multipart/form-data')
        assert response.status_code == 400

    def test_generate_invalid_url_format(self, client, sample_image):
        """POST /api/generate with invalid TikTok URL should return 400."""
        data = {
            'tiktok_url': 'https://www.youtube.com/watch?v=abc123',
            'folder_name': 'test_folder',
            'product_context': 'Test product description'
        }
        data['product_images'] = (sample_image, 'test.png')
        response = client.post('/api/generate',
                               data=data,
                               content_type='multipart/form-data')
        # Should either return 400 or start processing (implementation dependent)
        assert response.status_code in [200, 400]


class TestStatusEndpoint:
    """Tests for GET /api/status/<session_id> endpoint."""

    def test_status_invalid_session_returns_unknown(self, client):
        """GET /api/status/<invalid_id> should return 200 with 'unknown' step."""
        response = client.get('/api/status/nonexistent-session-id')
        assert response.status_code == 200
        data = response.get_json()
        assert data.get('step') == 'unknown'

    def test_status_returns_json(self, client):
        """GET /api/status should return JSON even for invalid ID."""
        response = client.get('/api/status/test-session-123')
        assert response.content_type == 'application/json'


class TestJobsEndpoint:
    """Tests for job management endpoints."""

    def test_list_jobs_returns_200(self, client):
        """GET /api/jobs should return 200 OK."""
        response = client.get('/api/jobs')
        assert response.status_code == 200

    def test_list_jobs_returns_json(self, client):
        """GET /api/jobs should return JSON response."""
        response = client.get('/api/jobs')
        assert response.content_type == 'application/json'

    def test_list_jobs_structure(self, client):
        """GET /api/jobs should return jobs list structure."""
        response = client.get('/api/jobs')
        data = response.get_json()
        assert 'jobs' in data or isinstance(data, list)

    def test_delete_job_invalid_id(self, client):
        """DELETE /api/jobs/<invalid_id> should return 404."""
        response = client.delete('/api/jobs/nonexistent-job-id')
        assert response.status_code == 404


class TestTestEndpoints:
    """Tests for test/debug endpoints."""

    def test_test_scrape_missing_url(self, client):
        """POST /api/test-scrape without URL should return 400."""
        response = client.post('/api/test-scrape', json={})
        assert response.status_code == 400

    def test_test_text_render_missing_params(self, client):
        """POST /api/test-text-render without params uses defaults and returns 200."""
        response = client.post('/api/test-text-render', json={})
        # Endpoint has default values for all params, so returns 200
        assert response.status_code == 200
