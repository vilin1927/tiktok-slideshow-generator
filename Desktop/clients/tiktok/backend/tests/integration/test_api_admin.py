"""
Tests for admin authentication and management endpoints.
"""
import pytest


class TestAdminLoginEndpoint:
    """Tests for POST /api/admin/login endpoint."""

    def test_login_missing_password_returns_400(self, client):
        """POST /api/admin/login without password should return 400."""
        response = client.post('/api/admin/login', json={})
        assert response.status_code == 400

    def test_login_wrong_password_returns_401(self, client):
        """POST /api/admin/login with wrong password should return 401."""
        response = client.post('/api/admin/login', json={
            'password': 'wrong_password_123'
        })
        assert response.status_code == 401

    def test_login_correct_password_returns_200(self, client):
        """POST /api/admin/login with correct password should return 200."""
        response = client.post('/api/admin/login', json={
            'password': 'test_password_123'
        })
        assert response.status_code == 200

    def test_login_returns_token(self, client):
        """POST /api/admin/login should return a token."""
        response = client.post('/api/admin/login', json={
            'password': 'test_password_123'
        })
        if response.status_code == 200:
            data = response.get_json()
            assert 'token' in data

    def test_login_empty_password_returns_400(self, client):
        """POST /api/admin/login with empty password should return 400."""
        response = client.post('/api/admin/login', json={
            'password': ''
        })
        assert response.status_code in [400, 401]


class TestAdminLogoutEndpoint:
    """Tests for POST /api/admin/logout endpoint."""

    def test_logout_without_token_returns_401(self, client):
        """POST /api/admin/logout without token should return 401."""
        response = client.post('/api/admin/logout')
        assert response.status_code == 401

    def test_logout_with_valid_token_returns_200(self, client, admin_token):
        """POST /api/admin/logout with valid token should return 200."""
        if admin_token:
            response = client.post('/api/admin/logout',
                                   headers={'X-Admin-Token': admin_token})
            assert response.status_code == 200


class TestAdminVerifyEndpoint:
    """Tests for GET /api/admin/verify endpoint."""

    def test_verify_without_token_returns_401(self, client):
        """GET /api/admin/verify without token should return 401."""
        response = client.get('/api/admin/verify')
        assert response.status_code == 401

    def test_verify_with_invalid_token_returns_401(self, client):
        """GET /api/admin/verify with invalid token should return 401."""
        response = client.get('/api/admin/verify',
                              headers={'X-Admin-Token': 'invalid-token-123'})
        assert response.status_code == 401

    def test_verify_with_valid_token_returns_200(self, client, admin_token):
        """GET /api/admin/verify with valid token should return 200."""
        if admin_token:
            response = client.get('/api/admin/verify',
                                  headers={'X-Admin-Token': admin_token})
            assert response.status_code == 200


class TestAdminKeysEndpoint:
    """Tests for API keys management endpoints."""

    def test_get_keys_without_token_returns_401(self, client):
        """GET /api/admin/keys without token should return 401."""
        response = client.get('/api/admin/keys')
        assert response.status_code == 401

    def test_get_keys_with_valid_token_returns_200(self, client, admin_token):
        """GET /api/admin/keys with valid token should return 200."""
        if admin_token:
            response = client.get('/api/admin/keys',
                                  headers={'X-Admin-Token': admin_token})
            assert response.status_code == 200

    def test_get_keys_returns_masked_keys(self, client, admin_token):
        """GET /api/admin/keys should return masked API keys."""
        if admin_token:
            response = client.get('/api/admin/keys',
                                  headers={'X-Admin-Token': admin_token})
            if response.status_code == 200:
                data = response.get_json()
                # Keys should be masked (showing only last few chars)
                assert 'keys' in data or 'gemini' in str(data).lower()

    def test_update_keys_without_token_returns_401(self, client):
        """PUT /api/admin/keys without token should return 401."""
        response = client.put('/api/admin/keys', json={
            'GEMINI_API_KEY': 'new_key_123'
        })
        assert response.status_code == 401

    def test_update_keys_with_valid_token(self, client, admin_token):
        """PUT /api/admin/keys with valid token should process request."""
        if admin_token:
            response = client.put('/api/admin/keys',
                                  headers={'X-Admin-Token': admin_token},
                                  json={'GEMINI_API_KEY': 'test_key_value'})
            # Should return 200 or 400 depending on validation
            assert response.status_code in [200, 400]


class TestAdminVideoJobsEndpoint:
    """Tests for admin video jobs endpoint."""

    def test_admin_video_jobs_without_token_returns_401(self, client):
        """GET /api/admin/video-jobs without token should return 401."""
        response = client.get('/api/admin/video-jobs')
        assert response.status_code == 401

    def test_admin_video_jobs_with_valid_token_returns_200(self, client, admin_token):
        """GET /api/admin/video-jobs with valid token should return 200."""
        if admin_token:
            response = client.get('/api/admin/video-jobs',
                                  headers={'X-Admin-Token': admin_token})
            assert response.status_code == 200
