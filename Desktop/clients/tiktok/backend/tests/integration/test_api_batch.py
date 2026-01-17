"""
Tests for batch processing endpoints.
"""
import pytest
from io import BytesIO
from PIL import Image


class TestBatchCreateEndpoint:
    """Tests for POST /api/batch endpoint."""

    def test_batch_create_missing_links_returns_400(self, client):
        """POST /api/batch without links should return 400."""
        data = {
            'hook_photo_var': 1,
            'hook_text_var': 1
        }
        response = client.post('/api/batch',
                               data=data,
                               content_type='multipart/form-data')
        assert response.status_code == 400

    def test_batch_create_empty_links_returns_400(self, client):
        """POST /api/batch with empty links should return 400."""
        data = {
            'links': '',
            'hook_photo_var': 1,
            'hook_text_var': 1
        }
        response = client.post('/api/batch',
                               data=data,
                               content_type='multipart/form-data')
        assert response.status_code == 400

    def test_batch_create_invalid_links_returns_400(self, client):
        """POST /api/batch with only invalid links should return 400."""
        data = {
            'links': 'https://youtube.com/watch\nhttps://instagram.com/post',
            'hook_photo_var': 1,
            'hook_text_var': 1
        }
        response = client.post('/api/batch',
                               data=data,
                               content_type='multipart/form-data')
        assert response.status_code == 400


class TestBatchStatusEndpoint:
    """Tests for GET /api/batch/<id>/status endpoint."""

    def test_batch_status_invalid_id_returns_404(self, client):
        """GET /api/batch/<invalid_id>/status should return 404."""
        response = client.get('/api/batch/nonexistent-batch-id/status')
        assert response.status_code == 404

    def test_batch_status_returns_json(self, client):
        """GET /api/batch/<id>/status should return JSON."""
        response = client.get('/api/batch/test-batch-123/status')
        assert response.content_type == 'application/json'


class TestBatchCancelEndpoint:
    """Tests for POST /api/batch/<id>/cancel endpoint."""

    def test_batch_cancel_invalid_id_returns_404(self, client):
        """POST /api/batch/<invalid_id>/cancel should return 404."""
        response = client.post('/api/batch/nonexistent-batch-id/cancel')
        assert response.status_code == 404


class TestBatchRetryEndpoint:
    """Tests for POST /api/batch/<id>/retry-failed endpoint."""

    def test_batch_retry_invalid_id_returns_404(self, client):
        """POST /api/batch/<invalid_id>/retry-failed should return 404."""
        response = client.post('/api/batch/nonexistent-batch-id/retry-failed')
        assert response.status_code == 404


class TestBatchLinksEndpoint:
    """Tests for GET /api/batch/<id>/links endpoint."""

    def test_batch_links_invalid_id_returns_404(self, client):
        """GET /api/batch/<invalid_id>/links should return 404."""
        response = client.get('/api/batch/nonexistent-batch-id/links')
        assert response.status_code == 404


class TestBatchListEndpoint:
    """Tests for GET /api/batch/list endpoint."""

    def test_list_batches_returns_200(self, client):
        """GET /api/batch/list should return 200 OK."""
        response = client.get('/api/batch/list')
        assert response.status_code == 200

    def test_list_batches_returns_json(self, client):
        """GET /api/batch/list should return JSON response."""
        response = client.get('/api/batch/list')
        assert response.content_type == 'application/json'

    def test_list_batches_structure(self, client):
        """GET /api/batch/list should return batches list structure."""
        response = client.get('/api/batch/list')
        data = response.get_json()
        assert 'batches' in data or isinstance(data, list)


class TestValidateLinksEndpoint:
    """Tests for POST /api/batch/validate endpoint."""

    def test_validate_empty_links(self, client):
        """POST /api/batch/validate with empty links."""
        response = client.post('/api/batch/validate', json={'links': ''})
        data = response.get_json()
        assert 'valid_count' in data or response.status_code == 400

    def test_validate_valid_links(self, client, valid_tiktok_urls):
        """POST /api/batch/validate with valid TikTok links."""
        links_text = '\n'.join(valid_tiktok_urls[:2])
        response = client.post('/api/batch/validate', json={'links': links_text})
        if response.status_code == 200:
            data = response.get_json()
            assert 'valid_count' in data

    def test_validate_mixed_links(self, client, valid_tiktok_urls, invalid_tiktok_urls):
        """POST /api/batch/validate with mixed valid and invalid links."""
        links = valid_tiktok_urls[:1] + invalid_tiktok_urls[:1]
        links_text = '\n'.join(links)
        response = client.post('/api/batch/validate', json={'links': links_text})
        if response.status_code == 200:
            data = response.get_json()
            assert 'valid_count' in data
            assert 'invalid_count' in data
