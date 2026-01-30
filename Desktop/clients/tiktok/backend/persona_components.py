"""
Expanded Persona Components Module

Provides 100+ unique persona combinations within target demographic.
Replaces the limited 5-variation cycling system.

The target audience (gender, age range, style) is extracted from slideshow analysis
and remains constant. This module varies the VISUAL APPEARANCE within that demographic.
"""

import random
import time
import logging
from typing import Dict, Optional, List

logger = logging.getLogger(__name__)

# Expanded persona component options
# These create visual diversity while staying within target demographic

PERSONA_COMPONENTS = {
    # Ethnic diversity within target audience
    'ethnicities': [
        'South Asian',
        'East Asian',
        'Southeast Asian',
        'Middle Eastern',
        'Black/African',
        'Hispanic/Latina',
        'White/European',
        'Mixed heritage',
        'Pacific Islander',
        'Mediterranean',
    ],  # 10 options

    # Face shape variations
    'face_shapes': [
        'oval with soft features',
        'heart-shaped with delicate chin',
        'round and youthful',
        'square with soft angles',
        'diamond-shaped',
        'oblong and elegant',
        'rectangle with strong features',
    ],  # 7 options

    # Eye feature variations
    'eye_features': [
        'almond-shaped eyes',
        'large round eyes',
        'hooded eyes with depth',
        'monolid eyes',
        'upturned cat eyes',
        'downturned gentle eyes',
        'wide-set expressive eyes',
        'deep-set intense eyes',
        'bright doe eyes',
    ],  # 9 options

    # Nose variations
    'nose_types': [
        'straight nose with soft bridge',
        'small button nose',
        'Roman nose with character',
        'wide nose with soft features',
        'narrow elegant nose',
        'slightly upturned nose',
        'flat bridge nose',
        'prominent nose with presence',
    ],  # 8 options

    # Lip variations
    'lip_shapes': [
        'full lips',
        'naturally thin lips',
        'heart-shaped cupid bow',
        'wide expressive lips',
        'bow-shaped defined lips',
    ],  # 5 options

    # Hair texture variations
    'hair_types': [
        'straight silky hair',
        'soft wavy hair',
        'bouncy curly hair',
        'tight coily hair',
        'natural afro texture',
        'loose waves',
        'thick voluminous hair',
    ],  # 7 options

    # Hair color variations
    'hair_colors': [
        'jet black',
        'dark brown',
        'warm medium brown',
        'light chestnut brown',
        'auburn with red tones',
        'honey blonde',
        'platinum blonde',
        'highlighted balayage',
        'dark with caramel highlights',
        'natural black with shine',
    ],  # 10 options

    # Hair length/style variations
    'hair_styles': [
        'pixie cut short',
        'chin-length bob',
        'shoulder-length layers',
        'mid-back length flowing',
        'long waist-length',
        'asymmetrical cut',
        'braided style',
        'natural loc style',
        'sleek ponytail',
        'messy bun updo',
    ],  # 10 options

    # Body type variations
    'body_types': [
        'slim and lean',
        'athletic toned',
        'naturally curvy',
        'plus-size beautiful',
        'petite and compact',
        'tall and statuesque',
        'average build natural',
    ],  # 7 options

    # Distinctive features that make personas memorable
    'distinctive_features': [
        'light freckles across nose',
        'beauty mark on cheek',
        'dimples when smiling',
        'strong defined jawline',
        'high prominent cheekbones',
        'defined collarbones',
        'natural gap between front teeth',
        'thick expressive eyebrows',
        'delicate arched eyebrows',
        'subtle laugh lines',
        'clear glowing skin',
        'sun-kissed complexion',
    ],  # 12 options

    # Skin tone variations (should align with ethnicity)
    'skin_tones': [
        'porcelain fair',
        'light with pink undertones',
        'light with warm undertones',
        'medium beige',
        'warm olive',
        'golden tan',
        'caramel brown',
        'rich brown',
        'dark brown',
        'deep ebony',
    ],  # 10 options
}

# Ethnicity to skin tone mapping for consistency
ETHNICITY_SKIN_TONES = {
    'South Asian': ['medium beige', 'warm olive', 'golden tan', 'caramel brown', 'rich brown'],
    'East Asian': ['porcelain fair', 'light with warm undertones', 'medium beige', 'golden tan'],
    'Southeast Asian': ['light with warm undertones', 'medium beige', 'warm olive', 'golden tan', 'caramel brown'],
    'Middle Eastern': ['light with warm undertones', 'medium beige', 'warm olive', 'golden tan', 'caramel brown'],
    'Black/African': ['caramel brown', 'rich brown', 'dark brown', 'deep ebony'],
    'Hispanic/Latina': ['light with warm undertones', 'medium beige', 'warm olive', 'golden tan', 'caramel brown', 'rich brown'],
    'White/European': ['porcelain fair', 'light with pink undertones', 'light with warm undertones', 'medium beige', 'warm olive'],
    'Mixed heritage': ['light with warm undertones', 'medium beige', 'warm olive', 'golden tan', 'caramel brown'],
    'Pacific Islander': ['warm olive', 'golden tan', 'caramel brown', 'rich brown'],
    'Mediterranean': ['light with warm undertones', 'medium beige', 'warm olive', 'golden tan'],
}


def generate_diverse_persona(
    target_audience: Optional[Dict] = None,
    version: int = 1,
    seed: Optional[int] = None
) -> Dict:
    """
    Generate a unique, diverse persona within target demographic.

    Args:
        target_audience: Dict with gender, age_range, style from analysis
        version: Version number for seeding (1-based)
        seed: Optional explicit seed for reproducibility

    Returns:
        Dict with all persona attributes
    """
    # Create seed from version + timestamp for variety across runs
    if seed is None:
        # Use version + current time milliseconds for variety
        seed = version + int(time.time() * 1000) % 100000

    random.seed(seed)

    # Select ethnicity first (affects skin tone options)
    ethnicity = random.choice(PERSONA_COMPONENTS['ethnicities'])

    # Get compatible skin tones for selected ethnicity
    compatible_skin_tones = ETHNICITY_SKIN_TONES.get(
        ethnicity,
        PERSONA_COMPONENTS['skin_tones']  # Fallback to all
    )
    skin_tone = random.choice(compatible_skin_tones)

    # Randomly select other features
    persona = {
        'ethnicity': ethnicity,
        'skin_tone': skin_tone,
        'face_shape': random.choice(PERSONA_COMPONENTS['face_shapes']),
        'eye_features': random.choice(PERSONA_COMPONENTS['eye_features']),
        'nose_type': random.choice(PERSONA_COMPONENTS['nose_types']),
        'lip_shape': random.choice(PERSONA_COMPONENTS['lip_shapes']),
        'hair_type': random.choice(PERSONA_COMPONENTS['hair_types']),
        'hair_color': random.choice(PERSONA_COMPONENTS['hair_colors']),
        'hair_style': random.choice(PERSONA_COMPONENTS['hair_styles']),
        'body_type': random.choice(PERSONA_COMPONENTS['body_types']),
        'distinctive_feature': random.choice(PERSONA_COMPONENTS['distinctive_features']),
    }

    # Add target audience constraints (these don't change)
    if target_audience:
        persona['gender'] = target_audience.get('gender', 'female')
        persona['age_range'] = target_audience.get('age_range', '20s-30s')
        persona['style'] = target_audience.get('style', 'casual modern')
    else:
        # Defaults if no target audience provided
        persona['gender'] = 'female'
        persona['age_range'] = '20s-30s'
        persona['style'] = 'casual modern'

    logger.info(f"Generated diverse persona v{version}: {ethnicity}, {skin_tone}, {persona['hair_type']}")

    return persona


def format_persona_prompt(persona: Dict) -> str:
    """
    Format persona attributes into prompt instruction text.

    Args:
        persona: Dict from generate_diverse_persona()

    Returns:
        Formatted string for image generation prompt
    """
    prompt = f"""
GENERATE THIS SPECIFIC PERSON:

Demographics (from target audience - do not change):
- Gender: {persona.get('gender', 'female')}
- Age: {persona.get('age_range', '20s-30s')}
- Style vibe: {persona.get('style', 'casual modern')}

Physical Appearance (create THIS specific look):
- Ethnicity/Background: {persona.get('ethnicity', 'Mixed heritage')}
- Skin tone: {persona.get('skin_tone', 'medium beige')}
- Face shape: {persona.get('face_shape', 'oval with soft features')}
- Eyes: {persona.get('eye_features', 'almond-shaped eyes')}
- Nose: {persona.get('nose_type', 'straight nose with soft bridge')}
- Lips: {persona.get('lip_shape', 'full lips')}
- Body type: {persona.get('body_type', 'average build natural')}

Hair:
- Texture: {persona.get('hair_type', 'soft wavy hair')}
- Color: {persona.get('hair_color', 'dark brown')}
- Style: {persona.get('hair_style', 'shoulder-length layers')}

Distinctive feature: {persona.get('distinctive_feature', 'natural clear skin')}

This creates ONE specific individual. Generate this EXACT person consistently.
"""
    return prompt.strip()


def get_persona_summary(persona: Dict) -> str:
    """
    Get a short summary of persona for logging.

    Args:
        persona: Persona dict

    Returns:
        Short summary string
    """
    return (
        f"{persona.get('ethnicity', '?')} / "
        f"{persona.get('skin_tone', '?')} / "
        f"{persona.get('hair_color', '?')} {persona.get('hair_type', '?')}"
    )


# Calculate total possible combinations
def get_total_combinations() -> int:
    """Calculate total possible unique persona combinations."""
    total = 1
    for key, options in PERSONA_COMPONENTS.items():
        total *= len(options)
    return total


if __name__ == "__main__":
    print(f"Total possible persona combinations: {get_total_combinations():,}")
    print("\nSample generated personas:\n")

    # Generate 5 sample personas to show diversity
    for i in range(1, 6):
        persona = generate_diverse_persona(
            target_audience={'gender': 'female', 'age_range': '20s', 'style': 'wellness lifestyle'},
            version=i,
            seed=i * 12345  # Fixed seeds for demo
        )
        print(f"Persona {i}: {get_persona_summary(persona)}")
        print(f"  Face: {persona['face_shape']}, {persona['eye_features']}")
        print(f"  Hair: {persona['hair_style']}, {persona['hair_color']}")
        print(f"  Feature: {persona['distinctive_feature']}")
        print()

    print("\n--- Sample Prompt Output ---\n")
    sample_persona = generate_diverse_persona(
        target_audience={'gender': 'female', 'age_range': '20s', 'style': 'wellness'},
        version=1,
        seed=99999
    )
    print(format_persona_prompt(sample_persona))
