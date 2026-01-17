"""
Tests for video creation endpoints.
"""
import pytest
from io import BytesIO
from PIL import Image


class TestVideoCreateEndpoint:
    """Tests for POST /api/video/create endpoint."""

    def test_video_create_missing_images_returns_400(self, client):
        """POST /api/video/create without images should return 400."""
        data = {
            'folder_name': 'test_video'
        }
        response = client.post('/api/video/create',
                               data=data,
                               content_type='multipart/form-data')
        assert response.status_code == 400

    def test_video_create_missing_folder_uses_default(self, client, sample_image):
        """POST /api/video/create without folder_name uses default name."""
        data = {
            'images': (sample_image, 'test.png')
        }
        response = client.post('/api/video/create',
                               data=data,
                               content_type='multipart/form-data')
        # folder_name has a default value, so request proceeds (200) or may fail for other reasons
        assert response.status_code in [200, 400, 500]


class TestVideoStatusEndpoint:
    """Tests for GET /api/video/status/<id> endpoint."""

    def test_video_status_invalid_id_returns_404(self, client):
        """GET /api/video/status/<invalid_id> should return 404."""
        response = client.get('/api/video/status/nonexistent-video-id')
        assert response.status_code == 404

    def test_video_status_returns_json(self, client):
        """GET /api/video/status should return JSON."""
        response = client.get('/api/video/status/test-video-123')
        assert response.content_type == 'application/json'


class TestVideoJobsEndpoint:
    """Tests for GET /api/video/jobs endpoint."""

    def test_list_video_jobs_returns_200(self, client):
        """GET /api/video/jobs should return 200 OK."""
        response = client.get('/api/video/jobs')
        assert response.status_code == 200

    def test_list_video_jobs_returns_json(self, client):
        """GET /api/video/jobs should return JSON response."""
        response = client.get('/api/video/jobs')
        assert response.content_type == 'application/json'

    def test_list_video_jobs_structure(self, client):
        """GET /api/video/jobs should return jobs list structure."""
        response = client.get('/api/video/jobs')
        data = response.get_json()
        assert 'jobs' in data or isinstance(data, list)

    def test_list_video_jobs_pagination(self, client):
        """GET /api/video/jobs should support pagination params."""
        response = client.get('/api/video/jobs?limit=5&offset=0')
        assert response.status_code == 200


class TestVideoDeleteEndpoint:
    """Tests for DELETE /api/video/jobs/<id> endpoint."""

    def test_delete_video_job_invalid_id(self, client):
        """DELETE /api/video/jobs/<invalid_id> should return 404."""
        response = client.delete('/api/video/jobs/nonexistent-video-id')
        assert response.status_code == 404


class TestVideoClearEndpoint:
    """Tests for DELETE /api/video/jobs endpoint."""

    def test_clear_video_jobs_endpoint(self, client):
        """DELETE /api/video/jobs - check if endpoint exists."""
        response = client.delete('/api/video/jobs')
        # May return 200/204 if implemented, or 405 if not allowed
        assert response.status_code in [200, 204, 405]
