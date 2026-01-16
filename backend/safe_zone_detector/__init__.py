"""
Safe Zone Detector Package

Analyzes images to find safe areas for text overlay placement.
Detects faces, busy areas, and main subjects to avoid.
"""

from .detector import analyze_image, SafeZoneResult

__all__ = ['analyze_image', 'SafeZoneResult']
