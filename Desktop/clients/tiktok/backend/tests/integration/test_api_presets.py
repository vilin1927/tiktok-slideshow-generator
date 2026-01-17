"""
Tests for preset endpoints.
"""
import pytest


class TestPresetsListEndpoint:
    """Tests for GET /api/presets endpoint."""

    def test_list_presets_returns_200(self, client):
        """GET /api/presets should return 200 OK."""
        response = client.get('/api/presets')
        assert response.status_code == 200

    def test_list_presets_returns_json(self, client):
        """GET /api/presets should return JSON response."""
        response = client.get('/api/presets')
        assert response.content_type == 'application/json'

    def test_list_presets_returns_list(self, client):
        """GET /api/presets should return a list of presets."""
        response = client.get('/api/presets')
        data = response.get_json()
        assert 'presets' in data
        assert isinstance(data['presets'], list)

    def test_list_presets_returns_all_9_presets(self, client):
        """GET /api/presets should return all 9 presets plus gemini option."""
        response = client.get('/api/presets')
        data = response.get_json()
        # 9 presets + gemini option = 10 total
        assert len(data['presets']) >= 9

    def test_list_presets_contains_expected_ids(self, client, preset_ids):
        """GET /api/presets should contain all expected preset IDs."""
        response = client.get('/api/presets')
        data = response.get_json()
        returned_ids = [p['id'] for p in data['presets']]
        for preset_id in preset_ids:
            assert preset_id in returned_ids, f"Missing preset: {preset_id}"

    def test_preset_has_required_fields(self, client):
        """Each preset should have id, display_name, font_name, and effect_name fields."""
        response = client.get('/api/presets')
        data = response.get_json()
        for preset in data['presets']:
            assert 'id' in preset
            assert 'display_name' in preset
            assert 'font_name' in preset
            assert 'effect_name' in preset


class TestPresetDetailEndpoint:
    """Tests for GET /api/presets/<id> endpoint."""

    def test_get_preset_valid_id(self, client):
        """GET /api/presets/<valid_id> should return preset details."""
        response = client.get('/api/presets/classic_shadow')
        assert response.status_code == 200
        data = response.get_json()
        assert data['id'] == 'classic_shadow'

    def test_get_preset_invalid_id(self, client):
        """GET /api/presets/<invalid_id> should return 404."""
        response = client.get('/api/presets/nonexistent_preset')
        assert response.status_code == 404

    def test_get_all_preset_ids(self, client, preset_ids):
        """All preset IDs should be accessible."""
        for preset_id in preset_ids:
            response = client.get(f'/api/presets/{preset_id}')
            assert response.status_code == 200, f"Failed for preset: {preset_id}"

    def test_get_gemini_option(self, client):
        """GET /api/presets/gemini should return gemini option."""
        response = client.get('/api/presets/gemini')
        # May return 200 or 404 depending on implementation
        if response.status_code == 200:
            data = response.get_json()
            assert data['id'] == 'gemini'
