"""
Unit tests for presets module.
"""
import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from presets import (
    get_preset, list_all_presets, get_font_path, get_font_size, get_gemini_option
)


class TestGetPreset:
    """Tests for get_preset function."""

    def test_get_preset_classic_shadow(self):
        """get_preset('classic_shadow') should return correct preset."""
        preset = get_preset('classic_shadow')
        assert preset is not None
        assert preset.id == 'classic_shadow'

    def test_get_preset_classic_outline(self):
        """get_preset('classic_outline') should return correct preset."""
        preset = get_preset('classic_outline')
        assert preset is not None
        assert preset.id == 'classic_outline'

    def test_get_preset_classic_box(self):
        """get_preset('classic_box') should return correct preset."""
        preset = get_preset('classic_box')
        assert preset is not None
        assert preset.id == 'classic_box'

    def test_get_preset_elegance_shadow(self):
        """get_preset('elegance_shadow') should return correct preset."""
        preset = get_preset('elegance_shadow')
        assert preset is not None
        assert preset.id == 'elegance_shadow'

    def test_get_preset_elegance_outline(self):
        """get_preset('elegance_outline') should return correct preset."""
        preset = get_preset('elegance_outline')
        assert preset is not None
        assert preset.id == 'elegance_outline'

    def test_get_preset_elegance_box(self):
        """get_preset('elegance_box') should return correct preset."""
        preset = get_preset('elegance_box')
        assert preset is not None
        assert preset.id == 'elegance_box'

    def test_get_preset_vintage_shadow(self):
        """get_preset('vintage_shadow') should return correct preset."""
        preset = get_preset('vintage_shadow')
        assert preset is not None
        assert preset.id == 'vintage_shadow'

    def test_get_preset_vintage_outline(self):
        """get_preset('vintage_outline') should return correct preset."""
        preset = get_preset('vintage_outline')
        assert preset is not None
        assert preset.id == 'vintage_outline'

    def test_get_preset_vintage_box(self):
        """get_preset('vintage_box') should return correct preset."""
        preset = get_preset('vintage_box')
        assert preset is not None
        assert preset.id == 'vintage_box'

    def test_get_preset_invalid_id(self):
        """get_preset('invalid') should return None."""
        preset = get_preset('nonexistent_preset')
        assert preset is None

    def test_get_preset_empty_id(self):
        """get_preset('') should return None."""
        preset = get_preset('')
        assert preset is None

    def test_all_presets_have_required_fields(self, preset_ids):
        """All presets should have required fields."""
        for preset_id in preset_ids:
            preset = get_preset(preset_id)
            assert preset is not None
            assert hasattr(preset, 'id')
            assert hasattr(preset, 'font')
            assert hasattr(preset, 'effect')


class TestListAllPresets:
    """Tests for list_all_presets function."""

    def test_list_all_presets_returns_list(self):
        """list_all_presets() should return a list."""
        presets = list_all_presets()
        assert isinstance(presets, list)

    def test_list_all_presets_returns_9(self):
        """list_all_presets() should return 9 presets."""
        presets = list_all_presets()
        assert len(presets) == 9

    def test_list_all_presets_contains_all_ids(self, preset_ids):
        """list_all_presets() should contain all preset IDs."""
        presets = list_all_presets()
        # list_all_presets returns dicts with 'id' key, not objects with .id attribute
        returned_ids = [p['id'] for p in presets]
        for preset_id in preset_ids:
            assert preset_id in returned_ids


class TestGetFontPath:
    """Tests for get_font_path function."""

    def test_get_font_path_montserrat(self):
        """get_font_path for Montserrat should return existing path."""
        # Get a classic preset to find font file name
        preset = get_preset('classic_shadow')
        if preset and preset.font:
            path = get_font_path(preset.font.file)
            assert path is not None
            assert os.path.exists(path)

    def test_get_font_path_invalid(self):
        """get_font_path for invalid font should return None or raise."""
        try:
            path = get_font_path('nonexistent_font.ttf')
            # If it returns, it should be None or not exist
            if path is not None:
                assert not os.path.exists(path)
        except:
            pass  # Exception is acceptable


class TestGetFontSize:
    """Tests for get_font_size function."""

    def test_get_font_size_short_text(self):
        """get_font_size for short text should return appropriate size."""
        size = get_font_size(10, 1920)
        assert isinstance(size, int)
        assert size > 0

    def test_get_font_size_long_text(self):
        """get_font_size for long text should return smaller size."""
        short_size = get_font_size(10, 1920)
        long_size = get_font_size(100, 1920)
        assert long_size <= short_size

    def test_get_font_size_tall_image(self):
        """get_font_size for taller image should return larger size."""
        small_size = get_font_size(20, 1080)
        large_size = get_font_size(20, 1920)
        assert large_size >= small_size


class TestGetGeminiOption:
    """Tests for get_gemini_option function."""

    def test_get_gemini_option_returns_dict(self):
        """get_gemini_option() should return a dict."""
        option = get_gemini_option()
        assert isinstance(option, dict)

    def test_get_gemini_option_has_id(self):
        """get_gemini_option() should have id field."""
        option = get_gemini_option()
        assert 'id' in option
        assert option['id'] == 'gemini'

    def test_get_gemini_option_has_display_name(self):
        """get_gemini_option() should have display_name field."""
        option = get_gemini_option()
        # API uses 'display_name' not 'name'
        assert 'display_name' in option
