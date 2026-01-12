"""
Safe Zone Detector - Analyzes images to find safe areas for text overlay placement.

Detects faces, busy areas, and main subjects to identify where text can be placed
without obscuring important visual elements.
"""

from .detector import analyze_image

__all__ = ['analyze_image']
__version__ = '1.0.0'
