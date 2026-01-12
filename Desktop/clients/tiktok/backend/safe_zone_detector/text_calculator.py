"""
Text Metrics Calculator - Calculates character limits based on font metrics.
"""

from PIL import ImageFont
from pathlib import Path
from typing import Dict, Optional
import os


# Default fonts directory (relative to this file)
FONTS_DIR = Path(__file__).parent.parent / "test_fonts"


def find_font(font_name: str, weight: str = "500") -> Optional[Path]:
    """
    Find a font file in the test_fonts directory.

    Args:
        font_name: Font name (e.g., "Inter", "Crimson Text")
        weight: Font weight (e.g., "400", "500", "600", "700")

    Returns:
        Path to font file, or None if not found
    """
    # Clean font name for filename matching
    clean_name = font_name.replace(" ", "_")

    # Try different extensions
    for ext in [".ttf", ".otf"]:
        font_path = FONTS_DIR / f"{clean_name}_{weight}{ext}"
        if font_path.exists():
            return font_path

    # Try without underscore
    for ext in [".ttf", ".otf"]:
        font_path = FONTS_DIR / f"{clean_name}{weight}{ext}"
        if font_path.exists():
            return font_path

    # Fallback: search for any matching font
    if FONTS_DIR.exists():
        for file in FONTS_DIR.iterdir():
            if clean_name.lower() in file.name.lower():
                return file

    return None


def calculate_text_capacity(
    width: int,
    height: int,
    font_path: str,
    font_size: int = 56,
    line_spacing: float = 1.2,
    padding_ratio: float = 0.1
) -> Dict:
    """
    Calculate how much text can fit in a given area.

    Args:
        width: Available width in pixels
        height: Available height in pixels
        font_path: Path to the font file
        font_size: Font size in pixels
        line_spacing: Line height multiplier (1.2 = 120% of font size)
        padding_ratio: Padding on each side (0.1 = 10%)

    Returns:
        Dict with:
        - max_chars_per_line: Maximum characters per line
        - max_lines: Maximum number of lines
        - total_chars: Total character capacity
        - usable_width: Width after padding
        - usable_height: Height after padding
        - line_height: Pixel height per line
        - avg_char_width: Average character width
    """
    # Load font
    try:
        font = ImageFont.truetype(str(font_path), font_size)
    except Exception as e:
        # Fallback to default font
        font = ImageFont.load_default()

    # Calculate usable area (after padding)
    padding_x = int(width * padding_ratio)
    padding_y = int(height * padding_ratio)
    usable_width = width - (2 * padding_x)
    usable_height = height - (2 * padding_y)

    # Calculate average character width using a sample string
    sample = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    try:
        bbox = font.getbbox(sample)
        sample_width = bbox[2] - bbox[0]
        avg_char_width = sample_width / len(sample)
    except:
        # Fallback estimation
        avg_char_width = font_size * 0.5

    # Calculate line height
    try:
        ascent, descent = font.getmetrics()
        line_height = int((ascent + descent) * line_spacing)
    except:
        line_height = int(font_size * line_spacing)

    # Calculate capacity
    max_chars_per_line = max(1, int(usable_width / avg_char_width))
    max_lines = max(1, int(usable_height / line_height))
    total_chars = max_chars_per_line * max_lines

    return {
        "max_chars_per_line": max_chars_per_line,
        "max_lines": max_lines,
        "total_chars": total_chars,
        "usable_width": usable_width,
        "usable_height": usable_height,
        "line_height": line_height,
        "avg_char_width": avg_char_width,
        "font_size": font_size,
        "padding": {"x": padding_x, "y": padding_y}
    }


def calculate_zone_capacity(
    zone_bounds: Dict,
    font_name: str = "Inter",
    font_weight: str = "500",
    font_size: int = 56
) -> Dict:
    """
    Calculate text capacity for a safe zone.

    Args:
        zone_bounds: Dict with x, y, w, h
        font_name: Font name to use
        font_weight: Font weight
        font_size: Font size in pixels

    Returns:
        Text capacity dict (see calculate_text_capacity)
    """
    font_path = find_font(font_name, font_weight)

    if font_path is None:
        # Try common fallbacks
        for fallback in ["Inter", "Roboto", "Arial"]:
            font_path = find_font(fallback, "500")
            if font_path:
                break

    if font_path is None:
        # Use PIL default
        font_path = ""

    return calculate_text_capacity(
        width=zone_bounds["w"],
        height=zone_bounds["h"],
        font_path=font_path,
        font_size=font_size
    )


def get_available_fonts() -> list:
    """List all available fonts in the test_fonts directory."""
    if not FONTS_DIR.exists():
        return []

    fonts = []
    for file in FONTS_DIR.iterdir():
        if file.suffix.lower() in [".ttf", ".otf"]:
            fonts.append(file.name)

    return sorted(fonts)


if __name__ == "__main__":
    print(f"Fonts directory: {FONTS_DIR}")
    print(f"Available fonts: {get_available_fonts()}")

    # Test with a sample zone
    test_zone = {"x": 0, "y": 0, "w": 500, "h": 300}
    result = calculate_zone_capacity(test_zone, "Inter", "500", 48)
    print(f"\nTest zone {test_zone['w']}x{test_zone['h']}:")
    print(f"  Max chars per line: {result['max_chars_per_line']}")
    print(f"  Max lines: {result['max_lines']}")
    print(f"  Total capacity: {result['total_chars']} chars")
