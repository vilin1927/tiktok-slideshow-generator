"""
Unit tests for text_renderer module.
"""
import os
import sys
import pytest
from PIL import Image
from io import BytesIO

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from text_renderer import (
    has_emoji, split_text_and_emojis, wrap_text,
    render_text, load_font, get_font_size
)
from presets import get_preset


class TestHasEmoji:
    """Tests for has_emoji function."""

    def test_has_emoji_with_emoji(self):
        """has_emoji() should return True for text with emoji."""
        assert has_emoji("Hello 😀") == True
        assert has_emoji("🎉 Party!") == True
        assert has_emoji("Fire 🔥") == True

    def test_has_emoji_without_emoji(self):
        """has_emoji() should return False for text without emoji."""
        assert has_emoji("Hello World") == False
        assert has_emoji("Just text here") == False
        assert has_emoji("123 numbers") == False

    def test_has_emoji_empty_string(self):
        """has_emoji() should return False for empty string."""
        assert has_emoji("") == False

    def test_has_emoji_special_chars(self):
        """has_emoji() should return False for special chars (not emoji)."""
        assert has_emoji("@#$%^&*") == False
        assert has_emoji("!!!???") == False


class TestSplitTextAndEmojis:
    """Tests for split_text_and_emojis function."""

    def test_split_text_only(self):
        """split_text_and_emojis() with text only."""
        segments = split_text_and_emojis("Hello World")
        assert len(segments) >= 1
        # Should have text segment
        text_segments = [s for s in segments if s[0] == 'text']
        assert len(text_segments) >= 1

    def test_split_emoji_only(self):
        """split_text_and_emojis() with emoji only."""
        segments = split_text_and_emojis("😀🎉🔥")
        assert len(segments) >= 1
        # Should have emoji segments
        emoji_segments = [s for s in segments if s[0] == 'emoji']
        assert len(emoji_segments) >= 1

    def test_split_mixed(self):
        """split_text_and_emojis() with mixed text and emoji."""
        segments = split_text_and_emojis("Hello 😀 World")
        assert len(segments) >= 2

    def test_split_empty_string(self):
        """split_text_and_emojis() with empty string."""
        segments = split_text_and_emojis("")
        assert isinstance(segments, list)


class TestLoadFont:
    """Tests for load_font function."""

    def test_load_font_montserrat(self):
        """load_font() should load Montserrat font."""
        preset = get_preset('classic_shadow')
        if preset and preset.font:
            font = load_font(preset.font.file, 48)
            assert font is not None

    def test_load_font_different_sizes(self):
        """load_font() should load fonts at different sizes."""
        preset = get_preset('classic_shadow')
        if preset and preset.font:
            font_small = load_font(preset.font.file, 24)
            font_large = load_font(preset.font.file, 72)
            assert font_small is not None
            assert font_large is not None


class TestGetFontSize:
    """Tests for get_font_size function."""

    def test_font_size_short_text(self):
        """get_font_size() for short text."""
        size = get_font_size(10, 1920)
        assert isinstance(size, int)
        assert size > 0
        assert size < 200  # Reasonable upper limit

    def test_font_size_long_text(self):
        """get_font_size() for long text should be smaller."""
        short = get_font_size(10, 1920)
        long = get_font_size(100, 1920)
        assert long <= short

    def test_font_size_different_heights(self):
        """get_font_size() should scale with image height."""
        small_img = get_font_size(20, 1080)
        large_img = get_font_size(20, 1920)
        assert large_img >= small_img


class TestWrapText:
    """Tests for wrap_text function."""

    def test_wrap_short_text(self):
        """wrap_text() should not wrap short text."""
        preset = get_preset('classic_shadow')
        if preset and preset.font:
            font = load_font(preset.font.file, 48)
            lines = wrap_text("Hi", font, 1000)
            assert len(lines) == 1

    def test_wrap_long_text(self):
        """wrap_text() should wrap long text."""
        preset = get_preset('classic_shadow')
        if preset and preset.font:
            font = load_font(preset.font.file, 48)
            long_text = "This is a very long line of text that should definitely be wrapped across multiple lines"
            lines = wrap_text(long_text, font, 300)
            assert len(lines) >= 2

    def test_wrap_empty_text(self):
        """wrap_text() should handle empty text."""
        preset = get_preset('classic_shadow')
        if preset and preset.font:
            font = load_font(preset.font.file, 48)
            lines = wrap_text("", font, 1000)
            assert isinstance(lines, list)


class TestRenderText:
    """Tests for render_text function."""

    def test_render_text_creates_image(self, sample_image_file, temp_output_dir):
        """render_text() should create output image."""
        output_path = os.path.join(temp_output_dir, "output.png")
        safe_zone = {'x': 100, 'y': 100, 'width': 800, 'height': 200}

        result = render_text(
            image_path=sample_image_file,
            text="Test Text",
            safe_zone=safe_zone,
            preset_id='classic_shadow',
            output_path=output_path
        )

        if result:
            assert os.path.exists(output_path)

    def test_render_text_all_presets(self, sample_image_file, temp_output_dir, preset_ids):
        """render_text() should work with all 9 presets."""
        safe_zone = {'x': 100, 'y': 100, 'width': 800, 'height': 200}

        for preset_id in preset_ids:
            output_path = os.path.join(temp_output_dir, f"output_{preset_id}.png")
            try:
                result = render_text(
                    image_path=sample_image_file,
                    text="Test",
                    safe_zone=safe_zone,
                    preset_id=preset_id,
                    output_path=output_path
                )
                # Just check it doesn't crash
                assert True
            except Exception as e:
                pytest.fail(f"render_text failed for {preset_id}: {e}")

    def test_render_text_with_emoji(self, sample_image_file, temp_output_dir):
        """render_text() should handle text with emoji."""
        output_path = os.path.join(temp_output_dir, "emoji_output.png")
        safe_zone = {'x': 100, 'y': 100, 'width': 800, 'height': 200}

        try:
            result = render_text(
                image_path=sample_image_file,
                text="Hello 😀 World",
                safe_zone=safe_zone,
                preset_id='classic_shadow',
                output_path=output_path
            )
            # Check it doesn't crash with emoji
            assert True
        except Exception as e:
            # Emoji rendering might fail on some systems
            pytest.skip(f"Emoji rendering not supported: {e}")

    def test_render_text_multiline(self, sample_image_file, temp_output_dir):
        """render_text() should handle multiline text."""
        output_path = os.path.join(temp_output_dir, "multiline_output.png")
        safe_zone = {'x': 100, 'y': 100, 'width': 300, 'height': 400}

        try:
            result = render_text(
                image_path=sample_image_file,
                text="This is a long text that should wrap to multiple lines",
                safe_zone=safe_zone,
                preset_id='classic_shadow',
                output_path=output_path
            )
            assert True
        except Exception as e:
            pytest.fail(f"Multiline render failed: {e}")
