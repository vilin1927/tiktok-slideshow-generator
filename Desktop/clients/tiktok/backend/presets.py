"""
Text Preset Definitions

9 presets: 3 fonts × 3 effects
Used for consistent text rendering on slideshow images.
"""

import os
from typing import Dict, List, Optional
from dataclasses import dataclass


# Font directory (relative to this file)
FONTS_DIR = os.path.join(os.path.dirname(__file__), 'test_fonts')


@dataclass
class FontConfig:
    """Font configuration."""
    name: str
    file: str
    style: str


@dataclass
class EffectConfig:
    """Text effect configuration."""
    name: str
    type: str  # 'shadow', 'outline', 'box'
    # Shadow settings
    shadow_color: str = '#000000'
    shadow_opacity: float = 0.5
    shadow_offset: tuple = (4, 4)
    shadow_blur: int = 8
    # Outline settings
    outline_color: str = '#000000'
    outline_width: int = 3
    # Box settings (from Figma CSS: padding: 20px 40px)
    box_color: str = '#FFFFFF'
    box_padding: int = 40  # Horizontal padding (left/right)
    box_padding_v: int = 20  # Vertical padding (top/bottom)
    box_radius: int = 20
    # Text color
    text_color: str = '#FFFFFF'  # Default white, can be black for box


@dataclass
class TextPreset:
    """Complete text preset (font + effect)."""
    id: str
    display_name: str
    font: FontConfig
    effect: EffectConfig


# Font definitions
FONTS = {
    'classic': FontConfig(
        name='Classic',
        file='Montserrat_600.ttf',
        style='Clean, bold, modern'
    ),
    'elegance': FontConfig(
        name='Elegance',
        file='Crimson_Text_600.ttf',
        style='Refined, feminine'
    ),
    'vintage': FontConfig(
        name='Vintage',
        file='EB_Garamond_500.ttf',
        style='Classic, timeless'
    )
}

# Effect definitions
EFFECTS = {
    'shadow': EffectConfig(
        name='Shadow',
        type='shadow',
        shadow_color='#000000',
        shadow_opacity=0.5,
        shadow_offset=(4, 4),
        shadow_blur=8,
        text_color='#FFFFFF'
    ),
    'outline': EffectConfig(
        name='Outline',
        type='outline',
        outline_color='#000000',
        outline_width=3,
        text_color='#FFFFFF'
    ),
    'box': EffectConfig(
        name='Box',
        type='box',
        box_color='#FFFFFF',
        box_padding=40,      # Horizontal padding (increased)
        box_padding_v=25,    # Vertical padding
        box_radius=20,       # Corner radius from Remotion
        text_color='#000000'  # Black text on white box
    )
}

# All 9 presets
PRESETS: Dict[str, TextPreset] = {}

for font_id, font in FONTS.items():
    for effect_id, effect in EFFECTS.items():
        preset_id = f"{font_id}_{effect_id}"
        PRESETS[preset_id] = TextPreset(
            id=preset_id,
            display_name=f"{font.name} + {effect.name}",
            font=font,
            effect=effect
        )


def get_preset(preset_id: str) -> Optional[TextPreset]:
    """
    Get a preset by ID.

    Args:
        preset_id: Preset ID (e.g., 'classic_shadow')

    Returns:
        TextPreset or None if not found
    """
    return PRESETS.get(preset_id)


def get_font_path(font_file: str) -> str:
    """
    Get full path to a font file.

    Args:
        font_file: Font filename (e.g., 'Montserrat_600.ttf')

    Returns:
        Full path to font file
    """
    return os.path.join(FONTS_DIR, font_file)


def get_font_size(text_length: int, image_height: int) -> int:
    """
    Calculate font size based on text length and image height.

    Args:
        text_length: Number of characters in text
        image_height: Image height in pixels

    Returns:
        Font size in pixels
    """
    # TikTok-style large text - doubled from previous values
    # Short text (<20 chars): 12% of height (~130px on 1080p)
    # Medium text (20-50 chars): 9% of height (~97px on 1080p)
    # Long text (>50 chars): 6% of height (~65px on 1080p)

    if text_length < 20:
        percent = 0.12
    elif text_length <= 50:
        percent = 0.09
    else:
        percent = 0.06

    return int(image_height * percent)


def list_all_presets() -> List[Dict]:
    """
    Get list of all presets for API response.

    Returns:
        List of preset dictionaries
    """
    result = []

    for preset_id, preset in PRESETS.items():
        result.append({
            'id': preset_id,
            'display_name': preset.display_name,
            'font_name': preset.font.name,
            'font_style': preset.font.style,
            'effect_name': preset.effect.name,
            'effect_type': preset.effect.type
        })

    return result


def get_gemini_option() -> Dict:
    """
    Get the 'Gemini Text' option for the dropdown.

    Returns:
        Dictionary representing the Gemini option
    """
    return {
        'id': 'gemini',
        'display_name': 'Gemini Text (Auto)',
        'font_name': 'Auto',
        'font_style': 'Gemini generates text in image',
        'effect_name': 'Auto',
        'effect_type': 'gemini'
    }
