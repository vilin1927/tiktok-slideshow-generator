"""
Physical Consistency Validation Module

Ensures generated scenes don't contain impossible combinations like:
- Wearing a coat while ice bathing
- Swimming in formal wear
- etc.

This module provides validation and auto-correction for scene descriptions.
"""

import re
import logging
from typing import Tuple, List, Optional

logger = logging.getLogger(__name__)

# Activity-to-attire rules
# For each activity, define required/recommended attire and forbidden items
ACTIVITY_ATTIRE_RULES = {
    'ice_bathing': {
        'keywords': ['ice bath', 'ice bathing', 'cold plunge', 'winter swimming', 'polar plunge', 'in frozen lake', 'in icy water', 'in cold water', 'luonto'],
        'required_attire': ['swimwear', 'bikini', 'swimsuit', 'bathing suit', 'swimming attire'],
        'forbidden_attire': ['coat', 'jacket', 'puffer', 'sweater', 'jeans', 'dress', 'hat', 'beanie',
                            'winter coat', 'heavy clothing', 'winter wear', 'hoodie', 'cardigan',
                            'pants', 'trousers', 'skirt', 'blouse', 'shirt'],
        'fix_instruction': 'Person should be wearing swimwear appropriate for cold water swimming'
    },
    'swimming': {
        'keywords': ['swimming', 'in pool', 'in water', 'in ocean', 'in sea', 'in lake', 'bathing in'],
        'required_attire': ['swimwear', 'bikini', 'swimsuit', 'bathing suit'],
        'forbidden_attire': ['coat', 'jacket', 'jeans', 'dress', 'formal wear', 'suit', 'sweater'],
        'fix_instruction': 'Person should be wearing appropriate swimwear'
    },
    'sauna': {
        'keywords': ['sauna', 'steam room', 'in sauna'],
        'required_attire': ['towel', 'swimwear', 'minimal clothing', 'wrapped in towel'],
        'forbidden_attire': ['coat', 'jacket', 'heavy clothes', 'shoes', 'boots', 'jeans', 'dress'],
        'fix_instruction': 'Person should be wrapped in towel or wearing minimal appropriate attire'
    },
    'shower': {
        'keywords': ['in shower', 'showering', 'taking shower'],
        'required_attire': ['towel nearby', 'appropriate for bathing'],
        'forbidden_attire': ['fully clothed', 'coat', 'shoes', 'dress', 'formal wear'],
        'fix_instruction': 'Scene should show appropriate shower/bathing context'
    },
    'beach': {
        'keywords': ['beach', 'seaside', 'oceanside', 'sunbathing'],
        'recommended_attire': ['swimwear', 'bikini', 'coverup', 'sundress', 'shorts', 'tank top'],
        'forbidden_attire': ['winter coat', 'puffer jacket', 'heavy sweater', 'boots'],
        'fix_instruction': 'Person should be wearing beach-appropriate attire'
    },
    'yoga_outdoor': {
        'keywords': ['yoga', 'stretching', 'meditation pose'],
        'recommended_attire': ['athletic wear', 'yoga clothes', 'leggings', 'sports bra', 'comfortable clothes'],
        'forbidden_attire': ['formal wear', 'dress', 'suit', 'heels', 'jeans'],
        'fix_instruction': 'Person should be wearing comfortable athletic or yoga attire'
    },
    'hiking': {
        'keywords': ['hiking', 'trail walking', 'mountain climbing', 'trekking'],
        'recommended_attire': ['hiking boots', 'athletic wear', 'outdoor clothing', 'comfortable shoes'],
        'forbidden_attire': ['heels', 'formal shoes', 'dress', 'suit', 'swimwear'],
        'fix_instruction': 'Person should be wearing appropriate hiking/outdoor attire'
    },
    'sleeping': {
        'keywords': ['sleeping', 'in bed asleep', 'napping'],
        'recommended_attire': ['pajamas', 'sleepwear', 'comfortable clothes', 'loungewear'],
        'forbidden_attire': ['formal wear', 'suit', 'dress', 'outdoor clothes', 'coat', 'shoes'],
        'fix_instruction': 'Person should be in sleepwear or comfortable clothes'
    }
}

# Blacklist of impossible combinations (activity, attire) tuples
IMPOSSIBLE_COMBINATIONS = [
    # Water + Heavy clothing
    ('in water', 'wearing coat'),
    ('in water', 'wearing jacket'),
    ('in water', 'wearing puffer'),
    ('in water', 'wearing sweater'),
    ('in water', 'wearing jeans'),
    ('in water', 'wearing dress'),
    ('in water', 'wearing suit'),
    ('in water', 'wearing hoodie'),
    ('swimming', 'winter coat'),
    ('swimming', 'heavy clothing'),
    ('ice bath', 'coat'),
    ('ice bath', 'jacket'),
    ('ice bath', 'sweater'),
    ('cold plunge', 'winter wear'),
    ('frozen lake', 'wearing coat'),
    ('frozen lake', 'wearing jacket'),

    # Sauna conflicts
    ('sauna', 'wearing coat'),
    ('sauna', 'wearing shoes'),
    ('sauna', 'fully dressed'),

    # Physical impossibilities
    ('underwater', 'dry hair'),
    ('running', 'high heels on grass'),
    ('skiing', 'barefoot'),
    ('skiing', 'swimwear'),

    # Context conflicts
    ('sleeping', 'standing'),
    ('sleeping', 'outdoor clothes'),
    ('formal event', 'pajamas'),
    ('office meeting', 'swimwear'),
    ('beach sunbathing', 'winter coat'),
]


def detect_activity(scene_description: str) -> Optional[str]:
    """
    Detect the activity type from a scene description.

    Args:
        scene_description: The scene description text

    Returns:
        Activity key if detected, None otherwise
    """
    scene_lower = scene_description.lower()

    for activity, rules in ACTIVITY_ATTIRE_RULES.items():
        for keyword in rules['keywords']:
            if keyword.lower() in scene_lower:
                return activity

    return None


def check_impossible_combinations(scene_description: str) -> List[Tuple[str, str]]:
    """
    Check if scene contains any impossible combinations.

    Args:
        scene_description: The scene description text

    Returns:
        List of (activity, attire) tuples that are impossible
    """
    scene_lower = scene_description.lower()
    violations = []

    for activity_keyword, attire_keyword in IMPOSSIBLE_COMBINATIONS:
        if activity_keyword.lower() in scene_lower and attire_keyword.lower() in scene_lower:
            violations.append((activity_keyword, attire_keyword))

    return violations


def check_forbidden_attire(scene_description: str, activity: str) -> List[str]:
    """
    Check if scene contains forbidden attire for the detected activity.

    Args:
        scene_description: The scene description text
        activity: The detected activity type

    Returns:
        List of forbidden attire items found in the scene
    """
    if activity not in ACTIVITY_ATTIRE_RULES:
        return []

    scene_lower = scene_description.lower()
    rules = ACTIVITY_ATTIRE_RULES[activity]
    forbidden = rules.get('forbidden_attire', [])

    found_forbidden = []
    for item in forbidden:
        if item.lower() in scene_lower:
            found_forbidden.append(item)

    return found_forbidden


def validate_scene(scene_description: str) -> Tuple[bool, List[str], Optional[str]]:
    """
    Validate a scene description for physical consistency.

    Args:
        scene_description: The scene description to validate

    Returns:
        Tuple of (is_valid, list_of_issues, fix_instruction)
    """
    issues = []
    fix_instruction = None

    # Check impossible combinations first
    violations = check_impossible_combinations(scene_description)
    if violations:
        for activity, attire in violations:
            issues.append(f"Impossible combination: '{activity}' with '{attire}'")

    # Detect activity and check forbidden attire
    activity = detect_activity(scene_description)
    if activity:
        forbidden_found = check_forbidden_attire(scene_description, activity)
        if forbidden_found:
            rules = ACTIVITY_ATTIRE_RULES[activity]
            fix_instruction = rules.get('fix_instruction', 'Use appropriate attire for the activity')
            for item in forbidden_found:
                issues.append(f"Activity '{activity}' should not have '{item}'")

    is_valid = len(issues) == 0

    if not is_valid:
        logger.warning(f"Scene validation failed: {issues}")
        logger.warning(f"Original scene: {scene_description[:200]}...")

    return is_valid, issues, fix_instruction


def get_attire_instruction(scene_description: str) -> Optional[str]:
    """
    Get attire instruction to add to scene if activity requires specific clothing.

    Args:
        scene_description: The scene description

    Returns:
        Attire instruction string if activity detected, None otherwise
    """
    activity = detect_activity(scene_description)

    if not activity:
        return None

    rules = ACTIVITY_ATTIRE_RULES[activity]
    required = rules.get('required_attire', [])

    if required:
        return f"IMPORTANT: Person must be wearing {required[0]} (appropriate for {activity.replace('_', ' ')})"

    recommended = rules.get('recommended_attire', [])
    if recommended:
        return f"Recommended attire: {', '.join(recommended[:3])}"

    return None


def enhance_scene_with_attire_rules(scene_description: str) -> str:
    """
    Enhance a scene description with appropriate attire instructions.

    This doesn't modify the scene but adds attire guidance for the image generator.

    Args:
        scene_description: Original scene description

    Returns:
        Enhanced scene description with attire instructions
    """
    attire_instruction = get_attire_instruction(scene_description)

    if attire_instruction:
        # Add attire instruction at the end
        enhanced = f"{scene_description}\n\n{attire_instruction}"
        logger.info(f"Enhanced scene with attire instruction: {attire_instruction}")
        return enhanced

    return scene_description


def validate_and_log(scene_description: str, slide_info: str = "") -> Tuple[bool, str]:
    """
    Validate scene and log results. Main entry point for integration.

    Args:
        scene_description: The scene description to validate
        slide_info: Optional slide context for logging (e.g., "hook slide", "body_2")

    Returns:
        Tuple of (is_valid, enhanced_scene_or_warning)
    """
    is_valid, issues, fix_instruction = validate_scene(scene_description)

    if is_valid:
        # Scene is valid, enhance with attire instructions if applicable
        enhanced = enhance_scene_with_attire_rules(scene_description)
        return True, enhanced
    else:
        # Scene has issues, return warning with fix instruction
        warning = f"[PHYSICAL CONSISTENCY WARNING for {slide_info}]\n"
        warning += f"Issues: {'; '.join(issues)}\n"
        if fix_instruction:
            warning += f"Fix: {fix_instruction}\n"
        warning += f"Enhanced scene: {enhance_scene_with_attire_rules(scene_description)}"

        logger.warning(warning)

        # Return enhanced scene anyway (with attire instruction added)
        # The image generator will get the corrective instruction
        return False, enhance_scene_with_attire_rules(scene_description)


# Convenience function for quick validation
def is_scene_valid(scene_description: str) -> bool:
    """Quick check if scene is physically consistent."""
    is_valid, _, _ = validate_scene(scene_description)
    return is_valid


if __name__ == "__main__":
    # Test cases
    test_scenes = [
        "Woman in coat and beanie standing by frozen lake",  # Valid - not IN water
        "Woman in coat and beanie swimming in frozen lake",  # Invalid - coat in water
        "Woman in puffer jacket doing ice bath in frozen lake",  # Invalid
        "Woman in swimsuit doing cold plunge in icy water",  # Valid
        "Woman in bikini ice bathing at sunrise",  # Valid
        "Person wearing winter coat in sauna",  # Invalid
        "Woman wrapped in towel in sauna",  # Valid
        "Man in suit swimming in pool",  # Invalid
        "Woman in yoga clothes doing stretching on deck",  # Valid
    ]

    print("Physical Consistency Validation Tests:\n")
    for scene in test_scenes:
        is_valid, issues, fix = validate_scene(scene)
        status = "VALID" if is_valid else "INVALID"
        print(f"[{status}] {scene[:60]}...")
        if not is_valid:
            print(f"  Issues: {issues}")
            print(f"  Fix: {fix}")
        print()
