"""
Tests for health check endpoint.
"""
import pytest


class TestHealthEndpoint:
    """Tests for /api/health endpoint."""

    def test_health_check_returns_200(self, client):
        """GET /api/health should return 200 OK."""
        response = client.get('/api/health')
        assert response.status_code == 200

    def test_health_check_returns_json(self, client):
        """GET /api/health should return JSON response."""
        response = client.get('/api/health')
        assert response.content_type == 'application/json'

    def test_health_check_returns_status_ok(self, client):
        """GET /api/health should return status: ok."""
        response = client.get('/api/health')
        data = response.get_json()
        assert data['status'] == 'ok'

    def test_health_check_returns_message(self, client):
        """GET /api/health should return a message."""
        response = client.get('/api/health')
        data = response.get_json()
        assert 'message' in data
        assert 'TikTok Slideshow Generator' in data['message']

    def test_health_check_post_not_allowed(self, client):
        """POST /api/health should return 405 Method Not Allowed."""
        response = client.post('/api/health')
        assert response.status_code == 405
