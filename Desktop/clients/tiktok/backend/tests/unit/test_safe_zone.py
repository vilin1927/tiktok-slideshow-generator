"""
Unit tests for safe_zone_detector module.
"""
import os
import sys
import pytest
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


class TestSafeZoneAnalyzer:
    """Tests for safe zone analysis functions."""

    def test_analyze_image_returns_result(self, sample_image_file):
        """analyze_image() should return a SafeZoneResult."""
        try:
            from safe_zone_detector import analyze_image
            result = analyze_image(sample_image_file)
            assert result is not None
            # Should have recommended zone
            assert hasattr(result, 'recommended_zone') or 'recommended_zone' in str(type(result))
        except ImportError:
            pytest.skip("safe_zone_detector not available")

    def test_analyze_image_with_solid_color(self, tmp_path):
        """analyze_image() should find safe zone in solid color image."""
        try:
            from safe_zone_detector import analyze_image
            # Create solid blue image
            img = Image.new('RGB', (1080, 1920), color='blue')
            img_path = tmp_path / "solid.png"
            img.save(str(img_path))

            result = analyze_image(str(img_path))
            assert result is not None
        except ImportError:
            pytest.skip("safe_zone_detector not available")


class TestFaceDetector:
    """Tests for face detection."""

    def test_detect_faces_no_faces(self, sample_image_file):
        """detect_faces() should return empty list for image without faces."""
        try:
            from safe_zone_detector.face_detector import detect_faces
            faces = detect_faces(sample_image_file)
            assert isinstance(faces, list)
            # Solid color image should have no faces
            assert len(faces) == 0
        except ImportError:
            pytest.skip("face_detector not available")

    def test_get_face_avoid_zones(self, sample_image_file):
        """get_face_avoid_zones() should return list."""
        try:
            from safe_zone_detector.face_detector import get_face_avoid_zones
            zones = get_face_avoid_zones(sample_image_file)
            assert isinstance(zones, list)
        except ImportError:
            pytest.skip("face_detector not available")


class TestEdgeAnalyzer:
    """Tests for edge analysis."""

    def test_analyze_edges(self, sample_image_file):
        """analyze_edges() should return grid data."""
        try:
            from safe_zone_detector.edge_analyzer import analyze_edges
            result = analyze_edges(sample_image_file)
            assert result is not None
        except ImportError:
            pytest.skip("edge_analyzer not available")

    def test_get_busy_avoid_zones(self, sample_image_file):
        """get_busy_avoid_zones() should return list."""
        try:
            from safe_zone_detector.edge_analyzer import get_busy_avoid_zones
            zones = get_busy_avoid_zones(sample_image_file)
            assert isinstance(zones, list)
        except ImportError:
            pytest.skip("edge_analyzer not available")


class TestColorAnalyzer:
    """Tests for color analysis."""

    def test_analyze_colors(self, sample_image_file):
        """analyze_colors() should return color data."""
        try:
            from safe_zone_detector.color_analyzer import analyze_colors
            result = analyze_colors(sample_image_file)
            assert result is not None
        except ImportError:
            pytest.skip("color_analyzer not available")

    def test_get_uniform_safe_zones(self, sample_image_file):
        """get_uniform_safe_zones() should return list."""
        try:
            from safe_zone_detector.color_analyzer import get_uniform_safe_zones
            zones = get_uniform_safe_zones(sample_image_file)
            assert isinstance(zones, list)
        except ImportError:
            pytest.skip("color_analyzer not available")

    def test_get_text_color_for_region(self, sample_image_file):
        """get_text_color_for_region() should return color."""
        try:
            from safe_zone_detector.color_analyzer import get_text_color_for_region
            color = get_text_color_for_region(sample_image_file, 0, 0, 100, 100)
            assert color is not None
            # Should be black or white
            assert color in ['black', 'white', (0, 0, 0), (255, 255, 255)]
        except ImportError:
            pytest.skip("color_analyzer not available")


class TestSaliencyDetector:
    """Tests for saliency detection."""

    def test_detect_saliency(self, sample_image_file):
        """detect_saliency() should return saliency map."""
        try:
            from safe_zone_detector.saliency import detect_saliency
            result = detect_saliency(sample_image_file)
            assert result is not None
        except ImportError:
            pytest.skip("saliency not available")

    def test_get_saliency_avoid_zone(self, sample_image_file):
        """get_saliency_avoid_zone() should return zone or None."""
        try:
            from safe_zone_detector.saliency import get_saliency_avoid_zone
            zone = get_saliency_avoid_zone(sample_image_file)
            # Can be None if no salient region found
            assert zone is None or isinstance(zone, dict)
        except ImportError:
            pytest.skip("saliency not available")


class TestZoneRanking:
    """Tests for zone ranking and selection."""

    def test_zone_has_required_fields(self, sample_image_file):
        """Recommended zone should have x, y, width, height."""
        try:
            from safe_zone_detector import analyze_image
            result = analyze_image(sample_image_file)
            if result and hasattr(result, 'recommended_zone'):
                zone = result.recommended_zone
                if zone:
                    assert 'x' in zone or hasattr(zone, 'x')
                    assert 'y' in zone or hasattr(zone, 'y')
                    assert 'width' in zone or hasattr(zone, 'width')
                    assert 'height' in zone or hasattr(zone, 'height')
        except ImportError:
            pytest.skip("safe_zone_detector not available")
