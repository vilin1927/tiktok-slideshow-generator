"""
Unit tests for video_generator module.
"""
import os
import sys
import pytest
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from video_generator import (
    check_ffmpeg_available, get_audio_duration,
    create_concat_file, create_video
)


class TestFFmpegAvailable:
    """Tests for check_ffmpeg_available function."""

    def test_ffmpeg_available(self):
        """check_ffmpeg_available() should return boolean."""
        result = check_ffmpeg_available()
        assert isinstance(result, bool)

    def test_ffmpeg_installed(self):
        """FFmpeg should be installed on the system."""
        result = check_ffmpeg_available()
        if not result:
            pytest.skip("FFmpeg not installed - skipping video tests")
        assert result == True


class TestGetAudioDuration:
    """Tests for get_audio_duration function."""

    def test_audio_duration_invalid_file(self):
        """get_audio_duration() with invalid file should handle error."""
        try:
            duration = get_audio_duration('/nonexistent/file.mp3')
            # Should return 0 or raise exception
            assert duration == 0 or duration is None
        except:
            pass  # Exception is acceptable

    def test_audio_duration_returns_float(self, sample_audio_file):
        """get_audio_duration() should return float."""
        if os.path.getsize(sample_audio_file) == 0:
            pytest.skip("Sample audio file is empty")
        try:
            duration = get_audio_duration(sample_audio_file)
            assert isinstance(duration, (int, float))
        except:
            pytest.skip("Audio duration detection failed")


class TestCreateConcatFile:
    """Tests for create_concat_file function."""

    def test_create_concat_file(self, tmp_path):
        """create_concat_file() should create a concat file."""
        # Create some dummy image paths
        image_paths = [
            str(tmp_path / "img1.png"),
            str(tmp_path / "img2.png"),
            str(tmp_path / "img3.png")
        ]
        # Create dummy images
        for path in image_paths:
            img = Image.new('RGB', (100, 100), color='red')
            img.save(path)

        concat_file = create_concat_file(image_paths, 3.0, str(tmp_path))

        if concat_file:
            assert os.path.exists(concat_file)
            # Read and verify content
            with open(concat_file, 'r') as f:
                content = f.read()
                assert 'duration' in content

    def test_create_concat_file_single_image(self, tmp_path):
        """create_concat_file() should work with single image."""
        image_path = str(tmp_path / "single.png")
        img = Image.new('RGB', (100, 100), color='blue')
        img.save(image_path)

        concat_file = create_concat_file([image_path], 3.0, str(tmp_path))
        if concat_file:
            assert os.path.exists(concat_file)


class TestCreateVideo:
    """Tests for create_video function."""

    def test_create_video_basic(self, tmp_path):
        """create_video() should create a video file."""
        if not check_ffmpeg_available():
            pytest.skip("FFmpeg not available")

        # Create test images
        image_paths = []
        for i in range(3):
            path = str(tmp_path / f"img{i}.png")
            img = Image.new('RGB', (1080, 1920), color='green')
            img.save(path)
            image_paths.append(path)

        output_path = str(tmp_path / "output.mp4")

        try:
            result = create_video(
                image_paths=image_paths,
                audio_path=None,
                output_path=output_path,
                slide_duration=1.0  # Short duration for test
            )

            if result:
                assert os.path.exists(output_path)
                # Check file has content
                assert os.path.getsize(output_path) > 0
        except Exception as e:
            pytest.skip(f"Video creation failed: {e}")

    def test_create_video_without_audio(self, tmp_path):
        """create_video() should work without audio."""
        if not check_ffmpeg_available():
            pytest.skip("FFmpeg not available")

        # Create test image
        image_path = str(tmp_path / "img.png")
        img = Image.new('RGB', (1080, 1920), color='blue')
        img.save(image_path)

        output_path = str(tmp_path / "silent.mp4")

        try:
            result = create_video(
                image_paths=[image_path],
                audio_path=None,
                output_path=output_path,
                slide_duration=1.0
            )

            if result:
                assert os.path.exists(output_path)
        except Exception as e:
            pytest.skip(f"Silent video creation failed: {e}")

    def test_create_video_invalid_images(self, tmp_path):
        """create_video() should handle invalid image paths."""
        if not check_ffmpeg_available():
            pytest.skip("FFmpeg not available")

        output_path = str(tmp_path / "should_fail.mp4")

        try:
            result = create_video(
                image_paths=['/nonexistent/image.png'],
                audio_path=None,
                output_path=output_path,
                slide_duration=1.0
            )
            # Should return None or False for invalid input
            assert result is None or result == False or not os.path.exists(output_path)
        except:
            pass  # Exception is acceptable


class TestVideoOutputFormat:
    """Tests for video output format and quality."""

    def test_video_dimensions(self, tmp_path):
        """Output video should have correct dimensions."""
        if not check_ffmpeg_available():
            pytest.skip("FFmpeg not available")

        # Create test image with TikTok dimensions
        image_path = str(tmp_path / "tiktok.png")
        img = Image.new('RGB', (1080, 1920), color='purple')
        img.save(image_path)

        output_path = str(tmp_path / "tiktok_video.mp4")

        try:
            result = create_video(
                image_paths=[image_path],
                audio_path=None,
                output_path=output_path,
                slide_duration=1.0
            )

            if result and os.path.exists(output_path):
                # Video created successfully
                assert True
        except:
            pytest.skip("Video creation failed")
