"""
Gemini Service V2 - Redesigned Pipeline
Single analysis call + parallel image generation
With persona consistency and smart product insertion
"""
import os
import json
import base64
import time
import re
import threading
from typing import Optional, Callable
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from dotenv import load_dotenv
from PIL import Image

load_dotenv()

from logging_config import get_logger, get_request_logger

logger = get_logger('gemini')

# Import safe zone detector and text renderer for manual preset mode
from safe_zone_detector import analyze_image as detect_safe_zones
from text_renderer import render_text
from presets import get_preset

# Import new diversity and consistency modules
from persona_components import generate_diverse_persona, format_persona_prompt, get_persona_summary
from physical_consistency import validate_and_log as validate_scene_consistency, enhance_scene_with_attire_rules

# Feature flags for new improvements (set to True to enable)
USE_EXPANDED_PERSONAS = True      # Use 100+ persona combinations instead of 5
USE_SCENE_VARIETY = True          # Add scene variety instructions
USE_PHYSICAL_CONSISTENCY = True   # Validate scenes for impossible combinations

from google import genai
from google.genai import types
from google.genai.types import HarmCategory, HarmBlockThreshold, SafetySetting

# Model names - 2 models only for simplicity and rate limit management
# TEXT_MODEL: All text analysis, grounding, scene generation (high capacity: 1000 RPM, 10K RPD)
# IMAGE_MODEL: Image generation (1000 RPM, 1K RPD per key)
TEXT_MODEL = 'gemini-3-flash-preview'
IMAGE_MODEL = 'gemini-3.1-flash-image-preview'

# Backwards compatibility aliases (deprecated, use TEXT_MODEL instead)
ANALYSIS_MODEL = TEXT_MODEL
GROUNDING_MODEL = TEXT_MODEL

# Generation config
MAX_RETRIES = 5       # Retries for direct generation mode
REQUEST_TIMEOUT = 120 # 120 sec timeout per API call

# Safety settings - use BLOCK_ONLY_HIGH to allow benign lifestyle content
# This prevents false positives on scenes like "candlelit dinner", "slip dress", etc.
# while still blocking truly harmful content
SAFETY_SETTINGS = [
    SafetySetting(
        category=HarmCategory.HARM_CATEGORY_HARASSMENT,
        threshold=HarmBlockThreshold.BLOCK_ONLY_HIGH,
    ),
    SafetySetting(
        category=HarmCategory.HARM_CATEGORY_HATE_SPEECH,
        threshold=HarmBlockThreshold.BLOCK_ONLY_HIGH,
    ),
    SafetySetting(
        category=HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
        threshold=HarmBlockThreshold.BLOCK_ONLY_HIGH,
    ),
    SafetySetting(
        category=HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
        threshold=HarmBlockThreshold.BLOCK_ONLY_HIGH,
    ),
]

# Safety fallback: word replacements to sanitize prompts that get blocked
# These replace potentially triggering words with safer alternatives
# IMPORTANT: Phrase patterns (multi-word) must come BEFORE single-word patterns
# to avoid broken grammar like "cozy setting soft linens" instead of "soft blanket"
# ============ JSON Repair Utility ============

def _repair_json(text: str) -> str:
    """
    Attempt to repair common JSON errors from Gemini responses.
    Fixes: trailing commas, missing closing brackets, truncated strings.
    Returns repaired text (may still be invalid - caller should handle).
    """
    # Remove trailing commas before } or ]
    text = re.sub(r',\s*([}\]])', r'\1', text)

    # Fix truncated response: count brackets and close unclosed ones
    open_braces = text.count('{') - text.count('}')
    open_brackets = text.count('[') - text.count(']')

    # If truncated mid-string, close the string first
    in_string = False
    escape_next = False
    for ch in text:
        if escape_next:
            escape_next = False
            continue
        if ch == '\\':
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
    if in_string:
        text += '"'

    # Close unclosed brackets/braces
    text += ']' * max(0, open_brackets)
    text += '}' * max(0, open_braces)

    return text


SAFETY_PHRASE_REPLACEMENTS = [
    # Multi-word phrases - MUST be processed first
    # Bed-related phrases (most common safety triggers)
    (r'\blying on bed sheets\b', 'resting on a soft blanket'),
    (r'\blying on the bed sheets\b', 'resting on a soft blanket'),
    (r'\bon bed sheets\b', 'on a soft blanket'),
    (r'\bon the bed sheets\b', 'on a soft blanket'),
    (r'\bbed sheets\b', 'soft blanket'),
    (r'\bsilk sheets\b', 'soft fabric'),
    (r'\bsatin sheets\b', 'soft fabric'),
    (r'\blying on bed\b', 'resting on a sofa'),
    (r'\blying on the bed\b', 'resting on a sofa'),
    (r'\blying in bed\b', 'relaxing at home'),
    (r'\blaying in bed\b', 'relaxing at home'),
    (r'\blaying on bed\b', 'relaxing on a sofa'),
    (r'\bon the bed\b', 'on the sofa'),
    (r'\bon bed\b', 'on a sofa'),
    (r'\bin bed\b', 'at home'),
    (r'\binto bed\b', 'to rest'),
    (r'\bto bed\b', 'to rest'),
    (r'\bbedroom scene\b', 'cozy living room scene'),
    (r'\bbedroom background\b', 'cozy living room background'),
    (r'\bsoft bedroom lighting\b', 'soft warm lighting'),
    (r'\bbedroom lighting\b', 'warm indoor lighting'),
    (r'\bcozy bedroom\b', 'cozy living room'),
    (r'\bin bedroom\b', 'in living room'),
    (r'\bin the bedroom\b', 'in the living room'),

    # Low light + bed combinations (especially triggering)
    (r'\blow light.*?bed\b', 'soft warm lighting in living room'),
    (r'\bbed.*?low light\b', 'sofa in soft warm lighting'),

    # Pillow/bedding phrases
    (r'\bsilk pillowcases?\b', 'soft cushion covers'),
    (r'\bpillowcases?\b', 'cushion covers'),
    (r'\bpillows? on bed\b', 'cushions on sofa'),

    # Body position phrases
    (r'\blying down\b', 'relaxing'),
    (r'\blaid down\b', 'resting'),
    (r'\blay down\b', 'rest'),
    (r'\blie down\b', 'relax'),
]

SAFETY_WORD_REPLACEMENTS = [
    # Clothing
    (r'\bslip dress\b', 'elegant dress'),
    (r'\blingerie\b', 'loungewear'),
    (r'\bbikini\b', 'swimwear'),
    (r'\bunderwear\b', 'comfortable clothes'),
    (r'\bbra\b', 'top'),
    (r'\bnightgown\b', 'sleepwear'),
    (r'\bbodycon\b', 'fitted'),
    (r'\blow[- ]cut\b', 'stylish'),
    (r'\bskinny\b', 'fitted'),
    (r'\btight[- ]fitting\b', 'well-fitted'),

    # Settings/atmosphere - single words (processed after phrases)
    (r'\bbedroom\b', 'living room'),
    (r'\bbed\b', 'sofa'),
    (r'\bpillows?\b', 'cushions'),
    (r'\bsheets\b', 'blanket'),
    (r'\bduvet\b', 'cozy blanket'),
    (r'\bbedding\b', 'soft furnishings'),
    (r'\bmattress\b', 'comfortable surface'),
    (r'\bcandlelit\b', 'warm ambient lighting'),
    (r'\bcandle[- ]lit\b', 'warm ambient lighting'),
    (r'\bintimate\b', 'cozy'),
    (r'\bromantic\b', 'warm'),
    (r'\bseductive\b', 'confident'),
    (r'\bsensual\b', 'relaxed'),
    (r'\bsultry\b', 'confident'),
    (r'\bsteamy\b', 'relaxing'),

    # Body descriptions
    (r'\bbare\b', 'natural'),
    (r'\bnaked\b', 'natural'),
    (r'\bexposed\b', 'visible'),
    (r'\bcleavage\b', 'neckline'),
    (r'\bcurves\b', 'figure'),
    (r'\bcurvy\b', 'natural'),
    (r'\bskin[- ]tight\b', 'form-fitting'),

    # Actions
    (r'\bundressing\b', 'getting ready'),
    (r'\bshowering\b', 'freshening up'),
    (r'\bbathing\b', 'relaxing'),
]

# Product-in-use reference images (for products worn ON the face)
# These show the actual product being worn, used when product_on_face.show_on_persona = true
PRODUCT_IN_USE_REFERENCES = {
    'face_tape': os.path.join(os.path.dirname(__file__), 'static', 'product_references', 'face_tape_reference.png'),
}

# Facial variation sets for persona diversity
# Each version gets different facial features to ensure personas look distinct
# while still matching the target demographic
FACIAL_VARIATION_SETS = {
    1: {'face_shape': 'oval', 'eye_shape': 'almond-shaped', 'nose_type': 'straight with a soft bridge', 'distinctive_feature': 'subtle dimples when smiling'},
    2: {'face_shape': 'heart-shaped with a delicate chin', 'eye_shape': 'round and larger', 'nose_type': 'slightly upturned', 'distinctive_feature': 'high prominent cheekbones'},
    3: {'face_shape': 'square with soft angles', 'eye_shape': 'hooded with depth', 'nose_type': 'prominent bridge', 'distinctive_feature': 'strong defined jawline'},
    4: {'face_shape': 'round and youthful', 'eye_shape': 'wide-set and expressive', 'nose_type': 'small button nose', 'distinctive_feature': 'apple cheeks'},
    5: {'face_shape': 'long and elegant', 'eye_shape': 'cat-eye shaped', 'nose_type': 'aquiline with character', 'distinctive_feature': 'defined brow bone'},
}

def _get_facial_variation(version: int) -> dict:
    """
    Get facial variation features based on version number.
    Cycles through 5 distinct feature sets.

    Args:
        version: The version/photo variation number (1-based)

    Returns:
        Dict with face_shape, eye_shape, nose_type, distinctive_feature
    """
    # Cycle through 5 variations (version 1->1, 2->2, ..., 6->1, 7->2, etc.)
    variation_key = ((version - 1) % 5) + 1
    return FACIAL_VARIATION_SETS[variation_key]

def _get_product_in_use_reference(product_on_face_config: dict) -> Optional[str]:
    """
    Get the product-in-use reference image path based on analysis config.

    Args:
        product_on_face_config: The product_on_face dict from analysis output

    Returns:
        Path to reference image if product should be shown on face, None otherwise
    """
    if not product_on_face_config or not product_on_face_config.get('show_on_persona', False):
        return None

    # Currently only face tape is supported
    ref_path = PRODUCT_IN_USE_REFERENCES.get('face_tape')
    if ref_path and os.path.exists(ref_path):
        return ref_path

    return None

def _sanitize_scene_description(scene: str, aggressive: bool = False) -> tuple[str, bool]:
    """
    Sanitize a scene description by replacing potentially triggering words.

    Processes phrase-level replacements FIRST to avoid broken grammar,
    then applies single-word replacements for any remaining triggers.

    Args:
        scene: The scene description to sanitize
        aggressive: If True, applies more aggressive sanitization that removes
                   all bed/bedroom-related content entirely

    Returns:
        tuple: (sanitized_scene, was_modified)
    """
    sanitized = scene
    was_modified = False

    # First pass: phrase-level replacements (multi-word patterns)
    # This prevents "bed sheets" from becoming "sofa blanket" (broken grammar)
    # Instead it becomes "soft blanket" (coherent phrase)
    for pattern, replacement in SAFETY_PHRASE_REPLACEMENTS:
        new_text = re.sub(pattern, replacement, sanitized, flags=re.IGNORECASE)
        if new_text != sanitized:
            was_modified = True
            sanitized = new_text

    # Second pass: single-word replacements for any remaining triggers
    for pattern, replacement in SAFETY_WORD_REPLACEMENTS:
        new_text = re.sub(pattern, replacement, sanitized, flags=re.IGNORECASE)
        if new_text != sanitized:
            was_modified = True
            sanitized = new_text

    # Aggressive mode: if first pass still has issues, do nuclear replacements
    if aggressive:
        # Replace any remaining problematic patterns with completely neutral alternatives
        aggressive_replacements = [
            # Remove any remaining lying/laying references
            (r'\b(lying|laying)\s+(on|in|down)\b', 'sitting comfortably in'),
            # Replace "low light" which can be triggering in certain contexts
            (r'\blow light\b', 'soft natural lighting'),
            (r'\bdim light\b', 'soft natural lighting'),
            (r'\bdimly lit\b', 'softly lit'),
            # Replace any screen/phone in dark/low light (common trigger)
            (r'phone.*?(low|dim|dark)\s*light', 'phone on a desk with natural lighting'),
            (r'screen.*?(low|dim|dark)\s*light', 'screen on a desk with natural lighting'),
            # Remove "at night" which can be triggering
            (r'\bat night\b', 'in the evening'),
            (r'\bnighttime\b', 'evening time'),
        ]
        for pattern, replacement in aggressive_replacements:
            new_text = re.sub(pattern, replacement, sanitized, flags=re.IGNORECASE)
            if new_text != sanitized:
                was_modified = True
                sanitized = new_text

    return sanitized, was_modified

# Queue mode flag - set via environment variable
# When True, submits to global queue instead of direct generation
USE_QUEUE_MODE = os.getenv('USE_IMAGE_QUEUE', 'true').lower() == 'true'

logger.info(f"Gemini service: USE_QUEUE_MODE={USE_QUEUE_MODE}")


class GeminiServiceError(Exception):
    """Custom exception for Gemini API errors"""
    pass


def _get_client(timeout: int = REQUEST_TIMEOUT):
    """
    Initialize and return Gemini client with timeout configuration.
    Uses API key rotation if multiple keys are configured.

    Args:
        timeout: HTTP request timeout in seconds (default: REQUEST_TIMEOUT)

    Returns:
        Tuple of (client, api_key) - api_key is needed for recording usage
    """
    try:
        # Try to use API key manager for rotation
        from api_key_manager import get_api_key_manager, ApiKeyExhaustedError
        manager = get_api_key_manager()
        api_key = manager.get_available_key()
        logger.debug(f"Using rotated API key: {api_key[:8]}...")
    except ImportError:
        # Fallback to single key if manager not available
        api_key = os.getenv('GEMINI_API_KEY')
        if not api_key:
            raise GeminiServiceError('GEMINI_API_KEY environment variable not set')
    except ApiKeyExhaustedError:
        raise  # Let it propagate - handled by queue_processor

    client = genai.Client(
        api_key=api_key,
        http_options={'timeout': timeout * 1000}  # Convert to milliseconds
    )
    return client, api_key


def _record_api_usage(api_key: str, success: bool = True, is_rate_limit: bool = False, is_invalid_key: bool = False, model_type: str = 'text'):
    """
    Record API usage for the given key and model type.

    Args:
        api_key: The API key that was used
        success: Whether the request succeeded
        is_rate_limit: Whether this was a 429 rate limit error
        is_invalid_key: Whether this was a 400 API_KEY_INVALID error
        model_type: 'text' or 'image' (default: 'text' for analysis calls)
    """
    try:
        from api_key_manager import get_api_key_manager
        manager = get_api_key_manager()
        if success:
            manager.record_usage(api_key, model_type=model_type)
        else:
            manager.record_failure(api_key, model_type=model_type, is_rate_limit=is_rate_limit, is_invalid_key=is_invalid_key)
    except ImportError:
        pass  # Manager not available, skip tracking


# Cache for grounded product searches (avoid repeated API calls)
_grounding_cache = {}
_grounding_cache_lock = threading.Lock()


def _get_real_products_for_scene(category: str, scene_type: str = "lifestyle", variation_id: str = "") -> str:
    """
    Use Google Search grounding to find real product names for a scene.

    Args:
        category: Product category (e.g., "skincare", "sleep", "wellness")
        scene_type: Type of scene (e.g., "bathroom", "bedroom", "morning routine")
        variation_id: Optional unique ID to get different results for different slides (e.g., "slide_0", "slide_1")

    Returns:
        String with real product names to include in scene generation
    """
    # Include variation_id in cache key to allow unique results per slide when needed
    cache_key = f"{category}_{scene_type}_{variation_id}" if variation_id else f"{category}_{scene_type}"

    with _grounding_cache_lock:
        if cache_key in _grounding_cache:
            logger.debug(f"Using cached products for {cache_key}")
            return _grounding_cache[cache_key]

    try:
        client, api_key = _get_client(timeout=30)  # Quick timeout for grounding

        # Add variety instruction when variation_id is provided
        variety_instruction = f"\n- Pick DIFFERENT products than typical suggestions (variation #{variation_id})" if variation_id else ""

        query = f"""For viral TikTok content about {category}, list 5-8 SPECIFIC real product names
that would naturally appear in a {scene_type} scene.

Return ONLY a comma-separated list of real brand + product names like:
"CeraVe Hydrating Cleanser, The Ordinary Niacinamide, Glow Recipe Watermelon Toner"

Focus on products that are:
- Actually popular on TikTok in 2024
- Recognizable brands (not generic)
- Would naturally be in this type of scene{variety_instruction}

Just the product names, nothing else."""

        response = client.models.generate_content(
            model=TEXT_MODEL,
            contents=query,
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())],
                temperature=0.3  # Lower temp for factual responses
            )
        )

        products = response.text.strip()

        # Record successful API usage
        _record_api_usage(api_key, success=True)

        # Cache the result
        with _grounding_cache_lock:
            _grounding_cache[cache_key] = products

        logger.info(f"Grounded products for {cache_key}: {products[:100]}...")
        return products

    except Exception as e:
        # Check for rate limit and record failure
        if '429' in str(e) or 'RESOURCE_EXHAUSTED' in str(e):
            _record_api_usage(api_key, success=False, is_rate_limit=True)
            logger.warning(f"Grounding search rate limited for {cache_key}, key {api_key[:8]} marked exhausted")
        else:
            logger.warning(f"Grounding search failed for {cache_key}: {e}")
        # Return empty - scene will generate without specific products
        return ""


def _get_specific_brand_for_product(generic_product: str, variation_id: str = "") -> str:
    """
    Get a SINGLE specific real brand name for a generic product.

    Args:
        generic_product: Generic product like "tart cherry juice", "weighted blanket"
        variation_id: Optional unique ID to get different brand for different slides

    Returns:
        Specific brand name like "Cheribundi tart cherry juice" or empty if failed
    """
    # Include variation_id in cache key to allow unique brands per slide when needed
    cache_key = f"brand_{generic_product.lower().strip()}_{variation_id}" if variation_id else f"brand_{generic_product.lower().strip()}"

    with _grounding_cache_lock:
        if cache_key in _grounding_cache:
            logger.debug(f"Using cached brand for {generic_product}")
            return _grounding_cache[cache_key]

    try:
        client, api_key = _get_client(timeout=20)

        query = f"""What is ONE popular, recognizable brand of {generic_product} that's trending on TikTok?

Return ONLY the brand name + product, like:
- "Cheribundi Tart Cherry Juice"
- "Bearaby Napper Weighted Blanket"
- "Hatch Restore Sunrise Alarm"

Just the single brand + product name, nothing else. Pick something visually recognizable."""

        response = client.models.generate_content(
            model=TEXT_MODEL,
            contents=query,
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())],
                temperature=0.5  # Some variety in brand selection
            )
        )

        brand = response.text.strip().strip('"').strip("'")

        # Record successful API usage
        _record_api_usage(api_key, success=True)

        # Cache the result
        with _grounding_cache_lock:
            _grounding_cache[cache_key] = brand

        logger.info(f"Grounded brand for '{generic_product}': {brand}")
        return brand

    except Exception as e:
        # Check for rate limit and record failure
        if '429' in str(e) or 'RESOURCE_EXHAUSTED' in str(e):
            _record_api_usage(api_key, success=False, is_rate_limit=True)
            logger.warning(f"Brand grounding rate limited for {generic_product}, key {api_key[:8]} marked exhausted")
        else:
            logger.warning(f"Brand grounding failed for {generic_product}: {e}")
        return ""


def _smart_detect_brandable_product(scene_description: str) -> str:
    """
    Use Gemini to intelligently detect if scene contains a product that would
    benefit from a real brand name. No hardcoded list - AI decides.

    Args:
        scene_description: Scene description to analyze

    Returns:
        The generic product name if found (e.g., "tart cherry juice"), or empty string
    """
    cache_key = f"detect_{hash(scene_description)}"

    with _grounding_cache_lock:
        if cache_key in _grounding_cache:
            return _grounding_cache[cache_key]

    try:
        client, api_key = _get_client(timeout=15)

        prompt = f"""Analyze this TikTok scene description and determine if it contains a PRODUCT that would look more realistic with a specific brand name.

Scene: "{scene_description}"

LOOK FOR products like:
- Drinks (juice, tea, coffee, smoothie, water bottle)
- Skincare/beauty (serum, moisturizer, face mask, roller)
- Wellness (supplements, vitamins, essential oils, diffuser)
- Home goods (blanket, pillow, candle, journal)
- Electronics (phone, headphones, alarm clock)
- Fitness (yoga mat, weights, foam roller)

DO NOT flag:
- Generic furniture (bed, couch, table)
- Room elements (wall, window, mirror)
- Body parts or clothing
- The USER'S PRODUCT (that's provided separately)

If you find a brandable product, return ONLY the generic product name.
If no brandable product found, return exactly: NONE

Examples:
- "woman holding glass of green juice" → "green juice"
- "hand holding serum bottle over bedding" → "serum"
- "cozy bedroom with soft lighting" → NONE
- "person drinking iced coffee at desk" → "iced coffee"

Your response (product name or NONE):"""

        response = client.models.generate_content(
            model=TEXT_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(temperature=0.1)
        )

        result = response.text.strip().lower()

        # Record successful API usage
        _record_api_usage(api_key, success=True)

        # Cache result
        with _grounding_cache_lock:
            _grounding_cache[cache_key] = result if result != "none" else ""

        if result and result != "none":
            logger.info(f"Smart detection found brandable product: '{result}' in scene")
            return result

        return ""

    except Exception as e:
        # Check for rate limit and record failure
        if '429' in str(e) or 'RESOURCE_EXHAUSTED' in str(e):
            _record_api_usage(api_key, success=False, is_rate_limit=True)
            logger.warning(f"Smart product detection rate limited, key {api_key[:8]} marked exhausted")
        else:
            logger.warning(f"Smart product detection failed: {e}")
        return ""


def _enhance_scene_with_real_brand(scene_description: str) -> str:
    """
    Smart detection + grounding: Detect if scene has a product that would benefit
    from a real brand, then find that brand via Google Search.

    Uses AI to detect products (no hardcoded list), then grounds with real brands.

    Args:
        scene_description: Original scene description

    Returns:
        Enhanced scene with real brand name, or original if no product detected
    """
    # Step 1: Smart detection - does scene have a brandable product?
    generic_product = _smart_detect_brandable_product(scene_description)

    if not generic_product:
        return scene_description

    # Step 2: Ground with real brand name
    real_brand = _get_specific_brand_for_product(generic_product)

    if not real_brand:
        logger.debug(f"Grounding failed for '{generic_product}', using original scene")
        return scene_description

    # Step 3: Enhance scene with specific brand
    enhanced = f"""{scene_description}

PRODUCT REALISM: The scene mentions {generic_product}. Show it as {real_brand} - make the product look authentic and recognizable (correct packaging shape, colors, label style).
Keep this as the ONE featured product - no random clutter."""

    logger.info(f"Enhanced scene: '{generic_product}' → '{real_brand}'")
    return enhanced


def _load_image_bytes(image_path: str) -> bytes:
    """Load image file as bytes"""
    with open(image_path, 'rb') as f:
        return f.read()


def _get_image_mime_type(image_path: str) -> str:
    """Get MIME type from image path"""
    ext = Path(image_path).suffix.lower()
    mime_types = {
        '.jpg': 'image/jpeg',
        '.jpeg': 'image/jpeg',
        '.png': 'image/png',
        '.gif': 'image/gif',
        '.webp': 'image/webp'
    }
    return mime_types.get(ext, 'image/jpeg')


def detect_product_slide(slide_paths: list) -> dict:
    """
    Send all slideshow images to Gemini to detect which slide is a product/ad slide.
    Used by TikTok Copy tool for auto-detection mode.

    Args:
        slide_paths: List of file paths to slideshow slide images

    Returns:
        dict with keys:
            - slide_number: int (1-indexed) or None if no product slide found
            - confidence: 'high', 'medium', or 'low'
            - reason: brief description of why this slide was identified
    """
    client, api_key = _get_client(timeout=30)

    contents = [
        "Analyze these TikTok slideshow images. Identify which slide (if any) "
        "contains a product photo, Amazon listing, 'link in bio' text, promotional product, "
        "or branded product packaging.\n\n"
        "Return ONLY valid JSON (no markdown, no code blocks):\n"
        '{"slide_number": N, "confidence": "high"|"medium"|"low", "reason": "brief description"}\n\n'
        "If NO product/promotional slide is found, return:\n"
        '{"slide_number": null, "confidence": null, "reason": "no product slide found"}\n\n'
        "Rules:\n"
        "- Slide numbers are 1-indexed (first slide = 1)\n"
        "- Look for: product packaging, Amazon logos, 'link in bio', branded products, promotional overlays\n"
        "- Do NOT count lifestyle/selfie slides with products naturally in the scene\n"
        "- Only flag slides that are PRIMARILY about promoting/showing a product\n"
        "- If multiple product slides exist, return the FIRST one\n"
    ]

    for i, path in enumerate(slide_paths):
        contents.append(f"[SLIDE {i + 1}]")
        contents.append(types.Part.from_bytes(
            data=_load_image_bytes(path),
            mime_type=_get_image_mime_type(path)
        ))

    try:
        response = client.models.generate_content(
            model=TEXT_MODEL,
            contents=contents,
            config=types.GenerateContentConfig(
                safety_settings=SAFETY_SETTINGS,
                temperature=0.1  # Low temp for consistent classification
            )
        )

        response_text = response.text.strip()
        # Strip markdown code blocks if present
        if response_text.startswith('```'):
            response_text = response_text.split('\n', 1)[1] if '\n' in response_text else response_text[3:]
            if response_text.endswith('```'):
                response_text = response_text[:-3].strip()

        result = json.loads(response_text)

        # Record successful API usage
        _record_api_usage(api_key, success=True)

        logger.info(f"Product slide detection: slide={result.get('slide_number')}, "
                     f"confidence={result.get('confidence')}, reason={result.get('reason')}")
        return result

    except json.JSONDecodeError as e:
        # JSON parse error is not a rate limit - still record success for the API call
        _record_api_usage(api_key, success=True)
        logger.warning(f"Product slide detection: failed to parse response: {response.text[:200]}")
        return {'slide_number': None, 'confidence': None, 'reason': f'JSON parse error: {str(e)}'}
    except Exception as e:
        # Check for rate limit and record failure
        if '429' in str(e) or 'RESOURCE_EXHAUSTED' in str(e):
            _record_api_usage(api_key, success=False, is_rate_limit=True)
            logger.warning(f"Product slide detection rate limited, key {api_key[:8]} marked exhausted")
        else:
            logger.warning(f"Product slide detection failed: {str(e)}")
        return {'slide_number': None, 'confidence': None, 'reason': f'Detection error: {str(e)}'}


def _get_style_description(style: str) -> str:
    """Get visual description for font style category."""
    descriptions = {
        'modern-clean': 'geometric letterforms, consistent stroke width, rounded corners, airy spacing',
        'classic-elegant': 'high contrast between thick/thin strokes, refined serifs, formal appearance',
        'handwritten-casual': 'organic letterforms, varying baseline, personal feel',
        'bold-impact': 'extra thick strokes, condensed width, attention-grabbing',
        'minimal-thin': 'delicate strokes, generous spacing, airy feel'
    }
    return descriptions.get(style, 'clean, readable letterforms')


def _validate_image_structure(image_path: str, expected_ratio: str = "3:4") -> tuple[bool, list[str]]:
    """
    Validate image meets basic structural requirements.
    Returns (is_valid, list_of_issues)
    """
    issues = []

    try:
        with Image.open(image_path) as img:
            width, height = img.size

            # Check dimensions exist
            if width == 0 or height == 0:
                issues.append("Image has zero dimensions")
                return False, issues

            # Check aspect ratio (5% tolerance)
            expected_ratios = {"3:4": 0.75, "4:3": 1.33, "9:16": 0.5625}
            expected = expected_ratios.get(expected_ratio, 0.75)
            actual = width / height
            if abs(actual - expected) > 0.05:
                issues.append(f"Wrong aspect ratio: {actual:.2f} (expected ~{expected})")

            # Check minimum resolution
            if width < 800 or height < 800:
                issues.append(f"Resolution too low: {width}x{height}")

            # Check not blank (few colors = failed generation)
            colors = img.getcolors(maxcolors=1000)
            if colors and len(colors) < 50:
                issues.append("Image may be blank or single-color")

            # File size sanity check
            file_size = os.path.getsize(image_path)
            if file_size < 50000:  # Less than 50KB is suspicious
                issues.append(f"File size suspiciously small: {file_size} bytes")

    except Exception as e:
        issues.append(f"Failed to load image: {str(e)}")
        return False, issues

    return len(issues) == 0, issues


def _validate_required_keywords(analysis: dict) -> tuple[bool, list[str]]:
    """
    Validate that product slide contains required keywords (brand name, purchase location).
    Returns (is_valid, list_of_issues)
    """
    issues = []

    # Get required keywords from analysis
    keywords = analysis.get('required_keywords', {})
    brand_name = keywords.get('brand_name', '') or 'Lumidew'  # Default to Lumidew
    purchase_location = keywords.get('purchase_location', '') or 'amazon'  # Default to amazon

    # Find product slide
    product_slides = [s for s in analysis.get('new_slides', [])
                      if s.get('slide_type') == 'product']

    if not product_slides:
        issues.append("No product slide found in analysis")
        return False, issues

    # Collect all text from product slide (both old and new format)
    product_slide = product_slides[0]
    all_texts = []

    # Old format: text_content
    if product_slide.get('text_content'):
        all_texts.append(product_slide.get('text_content'))

    # New format: scene_variations[].text_variations[]
    for scene_var in product_slide.get('scene_variations', []):
        for text in scene_var.get('text_variations', []):
            all_texts.append(text)

    product_text = ' '.join(all_texts).lower()

    # Check brand name
    if brand_name and brand_name.lower() not in product_text:
        issues.append(f"Product slide missing brand name: '{brand_name}'")

    # Check purchase location
    purchase_terms = ['amazon', 'website', 'their site', 'shop', 'store']
    if purchase_location:
        purchase_terms.append(purchase_location.lower())
    if not any(term in product_text for term in purchase_terms):
        issues.append("Product slide missing purchase location")

    return len(issues) == 0, issues


def _inject_missing_keywords(analysis: dict) -> dict:
    """
    Manually inject missing keywords into product slide text and remove redundancy.
    Also processes text_variations array.
    """
    keywords = analysis.get('required_keywords', {})
    brand = keywords.get('brand_name', '') or 'Lumidew'  # Default to Lumidew
    location = keywords.get('purchase_location', '') or 'amazon'  # Default to amazon

    def process_text(text: str) -> str:
        """Process a single text: inject keywords, clean up, and remove redundancy."""
        if not text:
            return text
        original_text = text

        # Clean up common Gemini output issues
        # Remove "Header:" prefix if present (Gemini sometimes adds this)
        if text.lower().startswith('header:'):
            text = text[7:].lstrip()  # Remove "Header:" and any leading whitespace

        # Remove "grab" and replace with natural alternatives
        text = re.sub(r'\bgrab\s+(them|yours|it)\s+(on|from)\s+', 'get yours on ', text, flags=re.IGNORECASE)
        text = re.sub(r'\bgrab\s+(on|from)\s+', 'find them on ', text, flags=re.IGNORECASE)

        # Fix "(amazon)" at end to be more natural
        if text.rstrip().endswith('(amazon)'):
            text = text.rstrip()[:-8].rstrip() + ' i got mine from amazon!'
        elif text.rstrip().endswith('(amazon find)'):
            text = text.rstrip()[:-13].rstrip() + ' i got mine from amazon!'

        # Check and inject brand
        if brand and brand.lower() not in text.lower():
            # Add brand mention naturally
            if '!' in text:
                # Insert after first exclamation
                text = text.replace('!', f'! love my {brand}', 1)
            else:
                text = f"obsessed with {brand} ✨ " + text

        # Check and inject purchase location
        if location and location.lower() not in text.lower() and 'amazon' not in text.lower():
            text = text.rstrip() + f" ✨ got mine on {location}"

        # Remove redundant brand mentions (e.g., "LumiDew X from LumiDew")
        text = _remove_brand_redundancy(text, brand)

        return text

    for slide in analysis.get('new_slides', []):
        if slide.get('slide_type') == 'product':
            # Process text_variations array if present
            text_variations = slide.get('text_variations', [])
            if text_variations:
                processed_variations = []
                for text in text_variations:
                    processed_text = process_text(text)
                    processed_variations.append(processed_text)
                slide['text_variations'] = processed_variations
                logger.info(f"Processed {len(processed_variations)} text variations for product slide")

            # Also process text_content for backward compatibility
            text = slide.get('text_content', '')
            if text:
                processed_text = process_text(text)
                if processed_text != text:
                    slide['text_content'] = processed_text
                    logger.info(f"Injected keywords into product slide text_content")

    return analysis


def _extract_brand_from_description(product_description: str) -> dict:
    """
    Pre-extract brand from product description using pattern matching.
    Provides ground truth to validate against AI extraction.
    """
    result = {'brand_candidates': [], 'likely_brand': None}

    # Pattern 1: First capitalized word(s) - usually the brand
    first_word_match = re.match(r'^([A-Z][a-zA-Z]+)', product_description.strip())
    if first_word_match:
        result['brand_candidates'].append(first_word_match.group(1))

    # Pattern 2: CamelCase words (like CeraVe, LumiDew)
    camel_matches = re.findall(r'\b([A-Z][a-z]+[A-Z][a-zA-Z]*)\b', product_description)
    result['brand_candidates'].extend(camel_matches)

    # Pattern 3: "by [Brand]" pattern
    by_matches = re.findall(r'\bby\s+([A-Z][a-zA-Z]+)', product_description, re.IGNORECASE)
    result['brand_candidates'].extend(by_matches)

    # Filter common non-brand words
    exclude = {'The', 'This', 'For', 'With', 'And', 'New', 'Best', 'Premium', 'Quality', 'Pack', 'Set'}
    candidates = [c for c in result['brand_candidates'] if c not in exclude]

    if candidates:
        result['likely_brand'] = candidates[0]

    return result


def _validate_brand_not_hallucinated(
    analysis: dict,
    product_description: str,
    pre_extracted_brand: str = None
) -> tuple[bool, str]:
    """
    Verify AI-extracted brand actually exists in product description.
    Returns (is_valid, corrected_brand)
    """
    keywords = analysis.get('required_keywords', {})
    ai_brand = keywords.get('brand_name', '')
    source_quote = keywords.get('brand_source_quote', '')

    # Check 1: AI brand appears in product description (case-insensitive)
    if ai_brand and ai_brand.lower() in product_description.lower():
        return True, ai_brand

    # Check 2: AI brand appears in its own source quote AND quote is from description
    if ai_brand and source_quote:
        if ai_brand.lower() in source_quote.lower() and source_quote.lower() in product_description.lower():
            return True, ai_brand

    # Hallucination detected! Use fallback
    logger.warning(f"Brand hallucination detected: '{ai_brand}' not found in description")

    # Fallback 1: Use pre-extracted brand
    if pre_extracted_brand:
        logger.info(f"Using pre-extracted brand: '{pre_extracted_brand}'")
        return False, pre_extracted_brand

    # Fallback 2: Extract first capitalized word from description
    match = re.match(r'^([A-Z][a-zA-Z]+)', product_description.strip())
    if match:
        fallback = match.group(1)
        logger.info(f"Using fallback brand: '{fallback}'")
        return False, fallback

    # Give up - return AI's brand anyway
    return False, ai_brand


def _remove_brand_redundancy(text: str, brand_name: str) -> str:
    """
    Remove redundant brand mentions like 'Product from Brand' when
    the product name already contains the brand.

    Examples:
    - "LumiDew Steam Eye Mask from LumiDew" -> "LumiDew Steam Eye Mask"
    - "I love my CeraVe cleanser from CeraVe" -> "I love my CeraVe cleanser"
    """
    if not brand_name or not text:
        return text

    # Count how many times brand appears (case-insensitive)
    brand_lower = brand_name.lower()
    occurrences = text.lower().count(brand_lower)

    # Only remove if brand appears more than once (redundant)
    if occurrences > 1:
        # Pattern: "from [brand]" anywhere in text
        pattern = rf'\s+from\s+{re.escape(brand_name)}(?=\s|$|,|!|\?|\.)'
        text = re.sub(pattern, '', text, flags=re.IGNORECASE)

        # Clean up any double spaces left behind
        text = re.sub(r'\s+', ' ', text).strip()

    return text


def analyze_and_plan(
    slide_paths: list[str],
    product_image_paths: list[str],
    product_description: str,
    output_dir: str,
    hook_photo_var: int = 1,
    hook_text_var: int = 1,
    body_photo_var: int = 1,
    body_text_var: int = 1,
    product_text_var: int = 1,
    request_id: str = None
) -> dict:
    """
    Single API call to analyze ALL slides and create new story plan.

    Detects slideshow type, identifies target audience, finds optimal
    product insertion point, and generates complete slide plan.
    """
    log = get_request_logger('gemini', request_id) if request_id else logger
    log.info(f"Starting analysis: {len(slide_paths)} slides, product: {product_description[:40]}...")
    start_time = time.time()

    client, api_key = _get_client()
    num_slides = len(slide_paths)

    prompt = f"""You are analyzing a viral TikTok slideshow to recreate it with a product insertion.

There are {num_slides} slides in this slideshow. Analyze them ALL.

═══════════════════════════════════════════════════════════════
TASK 0: EXTRACT BRAND NAME (GROUNDING - DO THIS FIRST!)
═══════════════════════════════════════════════════════════════

⚠️ CRITICAL GROUNDING INSTRUCTION:
You MUST extract the brand name EXACTLY as it appears in this product description:

---BEGIN PRODUCT DESCRIPTION---
{product_description}
---END PRODUCT DESCRIPTION---

EXTRACTION RULES:
1. Find the brand/company name EXACTLY as written above
2. The brand name MUST appear verbatim in the product description
3. DO NOT invent, guess, or substitute brand names
4. DO NOT use generic terms like "the brand" or "this product"

EXTRACTION STEPS (follow these):
1. Look for the FIRST capitalized word(s) - this is usually the brand
2. Look for CamelCase words (like "CeraVe", "LumiDew") - these are often brands
3. Quote the exact phrase containing the brand to prove it exists

EXAMPLES:
- Description: "Lumidew Steam Eye Mask..." → brand_name: "Lumidew" ✓
- Description: "CeraVe Moisturizing Cream..." → brand_name: "CeraVe" ✓
- Description: "Premium Sleep Mask (no brand)" → brand_name: "Premium Sleep Mask" (use product name)
- If no brand found, default to "Lumidew"

OUTPUT in "required_keywords":
- brand_name: [EXACT brand from description - if none found, use "Lumidew"]
- brand_source_quote: [quote the phrase from description proving brand exists]
- product_type: [what the product is]
- purchase_location: [amazon, website, etc. - default to "amazon"]

⚠️ VERIFICATION: Your brand_name MUST appear in the product description above.
If you output a brand that doesn't exist in the description, you have FAILED this task.

═══════════════════════════════════════════════════════════════
TASK 1: UNDERSTAND THE SLIDESHOW NARRATIVE
═══════════════════════════════════════════════════════════════

Instead of classifying into rigid types, understand the ACTUAL STORY:

1. STORY SUMMARY (one sentence):
   What is this slideshow about?
   Example: "Woman shares how face tape reduced her forehead lines"

2. NARRATIVE ARC:
   How does the story flow from start to finish?
   • Hook: What grabs attention in slide 1?
   • Build: How does the story develop through middle slides?
   • Climax: What's the key moment/revelation?
   • End: How does it conclude?

3. VIRAL FACTOR:
   What makes this content shareable/engaging?
   • Relatable problem?
   • Surprising transformation?
   • Useful tips?
   • Social proof?
   • Emotional connection?

4. CONTENT THEME:
   What topic/niche is this? (skincare, fitness, wellness, sleep, beauty, lifestyle, etc.)

5. SLIDE ROLES:
   For each slide, what is its PURPOSE in the story?
   • Slide 0: [role] - e.g., "Hook - shows the problem"
   • Slide 1: [role] - e.g., "Tip - practical advice"
   • Slide 2: [role] - e.g., "Proof - shows results"
   • etc.

This narrative understanding will guide how we recreate the slideshow with the user's product.

═══════════════════════════════════════════════════════════════
TASK 2: IDENTIFY TARGET AUDIENCE (ICP)
═══════════════════════════════════════════════════════════════

Based on the slideshow content, identify:
- WHO is this content for? (age, gender, lifestyle)
- What PROBLEMS do they have? (pain points)
- What ASPIRATIONS do they have? (desired state)
- What LANGUAGE/TONE resonates with them?

Then determine: Does the user's product solve any of these problems?

Map the product to audience pain points:
- What problem does this product solve?
- How does it fit the audience's lifestyle?
- What benefit would resonate most with THIS audience?

═══════════════════════════════════════════════════════════════
TASK 3: ANALYZE PERSONA (CRITICAL FOR CONSISTENCY)
═══════════════════════════════════════════════════════════════

Look at the person(s) in the ORIGINAL slideshow and describe:

1. GENDER: male | female | none (if no person shown)

2. AGE RANGE: 20s | 30s | 40s | 50s | 60s+

3. ETHNICITY/APPEARANCE:
   - Describe skin tone (fair, olive, tan, brown, dark)
   - Hair color and style
   - General facial features

4. STYLE/VIBE:
   - casual | glamorous | natural | edgy | professional
   - Describe clothing style, makeup level, overall aesthetic

5. DISTINGUISHING FEATURES:
   - Any notable features that define the "look"
   - Hair style, glasses, jewelry, etc.

PERSONA SLIDE TRACKING:
- Check EACH original slide: Does it show a person?
- Track which slide indices show a person
- For generated slides: has_persona should MATCH the original
  (if original slide 2 shows a person, generated slide 2 should too)

⚠️ CRITICAL CONSISTENCY RULE:
This SAME persona must appear in ALL generated slides where has_persona=true.
Generate ONE new person matching these attributes.
This person appears consistently across every slide that has a persona.

═══════════════════════════════════════════════════════════════
TASK 4: ANALYZE TEXT STYLE (CRITICAL FOR GENERATION)
═══════════════════════════════════════════════════════════════

Look at the text overlays and describe the typography VISUALLY (not font names):

1. FONT STYLE (describe the visual appearance - pick ONE):
   - "modern-clean": Geometric letterforms, consistent stroke width, rounded corners, airy spacing (like Montserrat, Poppins)
   - "classic-elegant": High contrast between thick/thin strokes, refined serifs, formal appearance (like Playfair, Georgia)
   - "handwritten-casual": Organic letterforms, varying baseline, personal feel (like Pacifico, dancing script)
   - "bold-impact": Extra thick strokes, condensed width, attention-grabbing (like Impact, Bebas)
   - "minimal-thin": Delicate strokes, generous spacing, airy feel (like Helvetica Neue Light)

2. FONT WEIGHT: thin / light / regular / medium / semibold / bold / black

3. LETTER SPACING: tight (letters close) / normal / wide / very-wide (letters spread)

4. FONT COLOR: Describe exactly (e.g., "pure white #FFFFFF", "cream/off-white", "soft blush pink")

5. TEXT EFFECTS:
   - Shadow: Describe if present (e.g., "subtle gray drop shadow, soft edges" or "none")
   - Outline: Describe if present (e.g., "thin black outline" or "none")
   - Background: Describe if present (e.g., "semi-transparent black pill shape behind text" or "none")

6. TEXT SIZE: small / medium / large (relative to image - remember viral = subtle)

7. VISUAL VIBE: Overall feeling in 2-3 words (e.g., "clean minimal", "bold statement", "soft feminine", "edgy modern")

8. TEXT POSITION PATTERN: Where is text typically placed? (e.g., "centered middle", "bottom third", "top with padding")

This visual description will be used to generate matching text - be VERY specific!

═══════════════════════════════════════════════════════════════
TASK 4B: ANALYZE VISUAL STYLE (CRITICAL FOR MATCHING ORIGINAL LOOK)
═══════════════════════════════════════════════════════════════

Analyze the OVERALL VISUAL STYLE of the slideshow images to ensure generated images match:

1. COLOR TEMPERATURE:
   - "warm" = golden, orange, yellow tones (cozy, sunset vibes)
   - "cool" = blue, teal, purple tones (clean, modern vibes)
   - "neutral" = balanced, no strong color cast
   - "mixed" = varies by slide

2. COLOR PALETTE:
   - Describe the dominant colors (e.g., "soft pinks and creams", "earth tones - browns and beiges", "bright whites with pops of green")
   - Note any signature color that appears across slides

3. LIGHTING STYLE:
   - "natural-soft" = soft window light, diffused, gentle shadows
   - "natural-harsh" = direct sunlight, strong shadows
   - "golden-hour" = warm, glowy, sunset/sunrise lighting
   - "studio" = even, professional lighting
   - "ambient-moody" = low light, atmospheric, intimate
   - "bright-airy" = very bright, minimal shadows, clean

4. SATURATION LEVEL:
   - "muted" = desaturated, faded colors (vintage/film look)
   - "natural" = true-to-life colors
   - "vibrant" = boosted, punchy colors
   - "high-contrast" = deep blacks, bright whites

5. FILTER/EDITING STYLE:
   - Describe any visible editing (e.g., "slight grain/film effect", "soft glow", "clean and sharp", "faded blacks", "orange and teal color grade")

6. OVERALL AESTHETIC:
   - 2-3 words capturing the visual identity (e.g., "clean minimalist", "warm cozy", "moody editorial", "bright lifestyle", "soft feminine")

This visual style MUST be replicated in all generated images!

═══════════════════════════════════════════════════════════════
TASK 5: DETECT COMPETITOR SLIDE & DETERMINE PRODUCT PLACEMENT
═══════════════════════════════════════════════════════════════

STEP 1: SCAN FOR COMPETITOR SIGNALS

For EACH slide's text, check if it contains ANY of these signals (case-insensitive):

PURCHASE/LINK MENTIONS:
- "amazon" / "Amazon"
- "link in bio" / "link in profile"
- "shop now" / "get yours" / "get it here"
- "I got mine from..." / "available at..."
- "use my code" / "discount code" / "use code"
- "% off" / "swipe up" / "tap to shop"
- Any @brand mention with purchase intent

PRODUCT PROMOTION SIGNALS:
- "This is the one I use"
- "My favorite [product]"
- "I've been using [brand]"
- "I swear by [product]"

If MULTIPLE slides contain competitor signals, use the LAST one (final CTA is usually main promotion).

STEP 2: DETERMINE PRODUCT PLACEMENT ACTION

┌─────────────────────────────────────────────────────────────────────┐
│  IF competitor_slide_found == TRUE:                                 │
│     ACTION: REPLACE the competitor slide with our product slide     │
│     OUTPUT: Same number of slides as original                       │
│     product_slide_index = competitor_slide_index                    │
│                                                                     │
│  ELSE (organic content, no competitor):                             │
│     ACTION: ADD our product slide at the END                        │
│     OUTPUT: Original slides + 1                                     │
│     product_slide_index = last position (after all original slides) │
└─────────────────────────────────────────────────────────────────────┘

RULE: Insert product in EXACTLY ONE slide. Never multiple.
- Product slide should feel like it BELONGS, not interrupts

═══════════════════════════════════════════════════════════════
TASK 5b: PRODUCT ON FACE DETECTION (TWO LEVELS)
═══════════════════════════════════════════════════════════════

LEVEL 1 - PRODUCT TYPE DETECTION (product_on_face.show_on_persona):
Some products are WORN ON THE FACE. Detect from PRODUCT DESCRIPTION:

PRODUCTS THAT GO ON FACE (set show_on_persona = true):
- Face tape / facial tape / wrinkle patches / anti-wrinkle tape
- Under-eye patches / eye masks that stick to skin
- Forehead patches / frown line patches
- Pimple patches / acne patches

PRODUCTS THAT DO NOT GO ON FACE (set show_on_persona = false):
- Steam eye masks, sheet masks, creams, serums, supplements, devices

LEVEL 2 - PER-SLIDE FACE TAPE DETECTION (shows_product_on_face per slide):
For EACH slide, look at the ORIGINAL IMAGE and detect if face tape is VISIBLE on a person's face.

Set shows_product_on_face = true ONLY if:
- The ORIGINAL slide image shows a person with patches/tape visibly ON their face
- Look for: patches on forehead, under eyes, smile lines

Set shows_product_on_face = false if:
- Slide shows product packaging only (not on face)
- Slide shows person WITHOUT patches on face
- Slide shows lifestyle scene without visible face patches

⚠️ IMPORTANT: Set shows_product_on_face = true for EVERY slide where the original shows face tape!
If 3 original slides show face tape, then 3 output slides should have shows_product_on_face = true.
Match the original exactly - if they wore tape in slides 2, 3, 4 then we show tape in slides 2, 3, 4.

<layout_detection priority="critical">
<task_name>TASK 5c: LAYOUT DETECTION (SINGLE vs SPLIT-SCREEN)</task_name>

<instruction>
For EACH slide, detect the LAYOUT TYPE from the original image.
Split-screen before/after comparisons are COMMON in skincare TikToks - detect them!
</instruction>

<layout_types>
<type name="single">One unified image/scene (most common)</type>
<type name="split_screen">Image shows TWO DISTINCT sections side-by-side</type>
</layout_types>

<detection_checklist>
Ask these questions for EACH slide. If ANY are TRUE, set layout_type="split_screen":
- Is the SAME PERSON shown TWICE in the image (side-by-side)?
- Is there a VISIBLE divider line (white line, gradient, sharp boundary)?
- Does one side show PROBLEM state (bad skin, dull) and other RESULT state (glowing, smooth)?
- Is there a CLEAR visual contrast between left/right (or top/bottom) halves?
- Are there labels like "before", "after", "day 1", "week 4" in the image?
</detection_checklist>

<visual_cues_for_split_screen>
- Two identical face compositions side-by-side (even without divider line)
- Stark contrast in skin quality between halves (tired vs. glowing)
- Same outfit/pose shown twice with different skin states
- Mirror-image-like composition showing transformation
</visual_cues_for_split_screen>

<split_config_format>
When split_screen detected, set split_config:
- orientation: "horizontal" (left|right) or "vertical" (top|bottom)
- sections: ["before", "after"] for transformation
- is_transformation: true if showing skin improvement
</split_config_format>

<examples_split_screen_yes>
- Hook showing woman: left=tired dull skin, right=glowing radiant skin
- Image with visible white line dividing two face shots
- Before/after showing same person with different skin texture
- Day 1 vs Day 30 comparison in single image
</examples_split_screen_yes>

<examples_split_screen_no>
- Single selfie showing one state only
- Product shot without comparison
- Lifestyle scene with one person, one state
</examples_split_screen_no>
</layout_detection>

<mimic_original_content>
<task_name>TASK 5: MIMIC THE ORIGINAL SLIDESHOW CONTENT</task_name>

CRITICAL: Your job is to MIMIC and RECREATE what's in the original slides!
Analyze each slide carefully and create SIMILAR content that matches:
- The TYPE of content shown (aesthetic shot, action, product, lifestyle moment)
- The VIBE and FEELING of the slide
- The VISUAL COMPOSITION and style

DO NOT invent random tips or force "tips list" structure on every slideshow!
Each slideshow is UNIQUE - analyze what's ACTUALLY shown and recreate it.

TEXT RULES FOR ALL SLIDES:
- NEVER end sentences with "."
- Use "!" only if it fits the vibe, otherwise no punctuation
- Emojis are encouraged ✨⚡💫
- NEVER repeat the same text twice in one image (no duplicate lines!)
- Keep text SHORT - max 2 lines, each line under 6 words

HOOK SLIDE (slide 0):
- Analyze what the original hook shows and says
- Create a SIMILAR hook that captures the same energy/promise
- Match the style (provocative, listicle, relatable, etc.)

BODY SLIDES (slides that are NOT product):
- ANALYZE what is ACTUALLY shown in each original body slide
- MIMIC the same type of content for each slide position
- If original shows a skincare product → show a similar skincare moment
- If original shows a lifestyle scene → recreate a similar lifestyle scene
- If original shows an action/habit → show a similar action/habit
- DO NOT force "tips" structure if the original isn't about tips!

For each body slide, ask yourself:
- What is the SUBJECT of this slide? (product, action, aesthetic, moment)
- What is the MOOD? (cozy, energetic, calm, luxurious)
- What STORY is it telling?
Then recreate something SIMILAR that fits the user's product context.

PRODUCT SLIDE (exactly ONE):
- Frame as tip/recommendation, NOT advertisement
- First line: Action-based tip (e.g., "steam eye mask before bed")
- Second part: casual, conversational recommendation

⚠️ MANDATORY KEYWORD INCLUSION (CRITICAL!):
The product slide text MUST naturally include:
1. The BRAND NAME from required_keywords (e.g., "Lumidew", "CeraVe")
2. The PURCHASE LOCATION from required_keywords (e.g., "amazon", "their site")

These keywords are NON-NEGOTIABLE - the slide WILL BE REJECTED without them!
They should flow naturally in conversational text, not feel forced.

- TEXT RULES:
  - NEVER end sentences with "."
  - Use "!" only if it fits the vibe, otherwise no punctuation
  - Emojis are encouraged ✨⚡💫
  - NEVER start text with "Header:" or any prefix/label
  - NEVER use the word "grab" (e.g., NOT "grab them on amazon")
  - NEVER end with just "(amazon)" in parentheses - sounds unnatural

- MANDATORY CTA FORMAT for product slides:
  The text MUST end with a natural sentence mentioning where to buy, like:
  - "i got mine from amazon!"
  - "found mine on amazon"
  - "amazon has them"

  Do NOT use:
  - Just "(amazon)" at the end
  - "grab yours on amazon"
  - "[amazon]" or "{{amazon}}"

- Example with keywords naturally included:
  "total game changer for my sleep! I keep Lumidew masks on my nightstand,
  they warm up on their own and feel like a cozy spa moment ✨ i got mine from amazon!"

⚠️ IMPORTANT: Use the ACTUAL brand name, NOT brackets or placeholders like "[brand]"!

⚠️ TEXT-ONLY SLIDES (EXCLUDE ENTIRELY):
DO NOT include slides that are PURELY text on a simple/solid background with:
- NO person visible
- NO product visible
- NO action or scene
- Just text overlay on plain/gradient/simple background
These "outro" or "CTA" slides (like "there she glows again", "follow for more") should be EXCLUDED from output.
Only include slides that have actual CONTENT (person, product, action, scene).

═══════════════════════════════════════════════════════════════
TASK 6: GENERATE SCENE & TEXT VARIATIONS (CRITICAL!)
═══════════════════════════════════════════════════════════════

⚠️ THIS IS THE MOST IMPORTANT SECTION - READ CAREFULLY!

For BODY SLIDES: Generate {body_photo_var} VARIATIONS of what's shown in the ORIGINAL slide!
For HOOK SLIDES: Generate {hook_photo_var} VARIATIONS of the ORIGINAL hook concept!

IMPORTANT: Variations should be SIMILAR to the original, not completely different topics!
Each "scene variation" = A DIFFERENT TAKE on the SAME concept from the original slide.

STRUCTURE FOR BODY SLIDES (body_photo_var={body_photo_var}):
- First, analyze what the ORIGINAL slide shows
- Then create {body_photo_var} variations of SIMILAR content
- Each variation has a slightly different scene but SAME type of content
- Each variation has {body_text_var} text variations (same concept, different wording)

EXAMPLE: If original body slide shows "someone applying face serum":
{{
    "slide_index": 2,
    "slide_type": "body",
    "scene_variations": [
        // Generate exactly {body_photo_var} scene variations here
        {{
            "scene_description": "Close-up of hands applying serum to face, bathroom mirror reflection",
            "text_variations": ["generate exactly {body_text_var} text items"]
        }}
    ]
}}

^^^ Variations should be about SERUM (matching the original) - not random different products!

STRUCTURE FOR HOOK SLIDES (hook_photo_var={hook_photo_var}):
- Generate {hook_photo_var} DIFFERENT hook concepts
- Each hook has its OWN scene/angle
- Each hook has {hook_text_var} text variations

EXAMPLE: Hook slide structure:
{{
    "slide_index": 0,
    "slide_type": "hook",
    "scene_variations": [
        // Generate exactly {hook_photo_var} scene variations here
        {{
            "scene_description": "Girl in cozy loungewear looking relaxed, soft bedroom lighting",
            "text_variations": ["generate exactly {hook_text_var} text items"]
        }}
    ]
}}

FOR PRODUCT SLIDE (exactly 1 scene variation):
{{
    "slide_index": 4,
    "slide_type": "product",
    "scene_variations": [
        {{
            "scene_description": "Product on nightstand with cozy bedroom background",
            "text_variations": ["generate exactly {product_text_var} text items - must include brand name and amazon"]
        }}
    ]
}}

VARIATION RULES (CRITICAL - FOLLOW EXACTLY!):
⚠️ THE COUNTS BELOW ARE MANDATORY - DO NOT DEVIATE!

- Hook slides: EXACTLY {hook_photo_var} scene_variations, each with EXACTLY {hook_text_var} text_variations
- Body slides: EXACTLY {body_photo_var} scene_variations, each with EXACTLY {body_text_var} text_variations
- Product slides: EXACTLY 1 scene_variation with EXACTLY {product_text_var} text_variations
- Text-only slides: EXCLUDE ENTIRELY (do not include in output)

If hook_text_var=1, generate exactly 1 text item (not 2, not 3 - exactly 1!)
If body_text_var=1, generate exactly 1 text item per scene variation!

- scene_variations = array of VARIATIONS of the SAME concept from the original slide
- text_variations = array of different WORDINGS for the SAME concept
- Each scene_variation must have a UNIQUE scene_description

═══════════════════════════════════════════════════════════════
MULTI-POSITION TEXT RULE (CRITICAL!)
═══════════════════════════════════════════════════════════════

If the original slide has text in MULTIPLE positions (top AND bottom, etc.):
- DO NOT write the SAME text for both positions
- SPLIT the message into TWO DISTINCTLY DIFFERENT parts using " | " separator
- Format: "top text | bottom text"

⚠️ THE TWO PARTS MUST BE COMPLETELY DIFFERENT:
- TOP text = HOOK/attention grabber (short, punchy)
- BOTTOM text = VALUE/benefit/payoff (different words!)
- NEVER start both parts with the same word or phrase
- NEVER repeat concepts across the two parts

❌ BAD EXAMPLES (repetitive/awkward):
- "minerals help to my water💧 | minerals help stop cravings" ← Both start with "minerals help"
- "my morning routine tip | my morning glow secret" ← Both start with "my morning"
- "add this to your diet | adding this changed my skin" ← Same concept repeated

✅ GOOD EXAMPLES (distinct parts):
- "morning routine tip 💫 | watch til the end"
- "my secret hack ✨ | this changed everything"
- "skincare game changer 🧴 | finally clear skin"
- "try this today | you won't regret it"
- "one simple trick | the results speak"

{{
    "text_variations": [
        "morning routine tip 💫 | watch til the end",
        "my secret hack ✨ | this changed everything"
    ]
}}

The " | " separator tells the image generator to place:
- First part at TOP position
- Second part at BOTTOM position

⚠️ CRITICAL: WHEN TO USE " | " SEPARATOR ⚠️
═══════════════════════════════════════════════════════════════
ONLY use " | " separator when text_position_hint contains:
- "top and bottom"
- "multiple"
- "both"
- " and " (indicating two positions)

🚫 DO NOT use " | " separator when text_position_hint is:
- "center" → NO separator! Write: "my text here"
- "bottom" → NO separator! Write: "my text here"
- "top" → NO separator! Write: "my text here"
- "center left" → NO separator! Write: "my text here"
- "bottom right" → NO separator! Write: "my text here"
- ANY single-word position → NO separator!

❌ WRONG (separator for single position):
text_position_hint: "center"
text: "stopped sweet drinks 🥤 | sugar bombs" ← WRONG! No separator for "center"!

✅ CORRECT (no separator for single position):
text_position_hint: "center"
text: "stopped sweet drinks - sugar bombs 🥤" ← Correct! Single block of text

✅ CORRECT (separator for multiple positions):
text_position_hint: "top and bottom"
text: "my tip 💫 | watch til the end" ← Correct! Use separator for multi-position

═══════════════════════════════════════════════════════════════
MIMIC THE ORIGINAL CONTENT (ABSOLUTELY CRITICAL!)
═══════════════════════════════════════════════════════════════

For EACH body slide, you MUST:
1. ANALYZE what the original slide actually shows
2. DESCRIBE what type of content it is (product shot, lifestyle moment, action, aesthetic)
3. CREATE similar content that matches the original's vibe

DO NOT force "tips" or specific product categories on slides!
Each slideshow is UNIQUE - recreate what you SEE in the original.

EXAMPLES of how to mimic:
- If original shows a skincare product close-up → create similar skincare product shot
- If original shows a cozy bedroom scene → create similar cozy bedroom scene
- If original shows food/drink → create similar food/drink shot
- If original shows an outfit/fashion → create similar fashion content
- If original shows a self-care moment → create similar self-care moment

The KEY is to match:
- The TYPE of content (product, lifestyle, action, aesthetic)
- The MOOD (cozy, energetic, luxurious, minimal)
- The VISUAL STYLE (close-up, wide shot, flat lay, etc.)

SCENE DESCRIPTION RULES:
- MIMIC what the original slide shows - if it's a product shot, describe a product shot
- If original shows lifestyle/aesthetic scenes, recreate similar scenes
- Match the VISUAL STYLE of the original (close-up, wide shot, flat lay, etc.)

- ALWAYS describe scenes as STANDARD SINGLE IMAGES (one photo filling the frame)
- NEVER describe comparison layouts, split screens, side-by-side, or grids
- NEVER use words like "split screen", "left side vs right side", "comparison grid"
- Even if the original TikTok has a comparison/grid layout, describe a STANDARD lifestyle scene instead
- Each scene = ONE cohesive image NOT a collage or comparison

GOOD: Match the type of content from the original slide
BAD: Inventing random "tips" that have nothing to do with the original

═══════════════════════════════════════════════════════════════
OUTPUT FORMAT
═══════════════════════════════════════════════════════════════

Return ONLY valid JSON:

{{
    "narrative": {{
        "story_summary": "one sentence describing what this slideshow is about",
        "narrative_arc": {{
            "hook": "how slide 1 grabs attention",
            "build": "how the story develops through middle slides",
            "climax": "key moment or revelation",
            "end": "how it concludes"
        }},
        "viral_factor": "what makes this content shareable (relatable problem, transformation, useful tips, etc.)",
        "content_theme": "skincare | fitness | wellness | sleep | beauty | lifestyle | other"
    }},

    "persona": {{
        "gender": "female | male | none",
        "age_range": "20s | 30s | 40s | 50s | 60s+",
        "ethnicity": "description of skin tone and features",
        "appearance": "hair color/style, notable features",
        "style": "casual | glamorous | natural | edgy | professional",
        "vibe": "overall feeling (e.g., friendly, approachable, authentic)",
        "cultural_context": "null OR specific culture when content EXPLICITLY mentions it (e.g., 'Japanese' if talking about J-beauty, 'Korean' for K-beauty, 'African' for African beauty secrets). Only set when the storyline/text explicitly references a specific culture's beauty practices. Leave null for broad/general audience content."
    }},

    "competitor_detection": {{
        "found": true | false,
        "slide_index": null or [index of competitor slide],
        "signals": ["list of signals detected, e.g., amazon, link in bio"]
    }},

    "product_placement": {{
        "action": "replace | add",
        "product_slide_index": [index where product slide goes],
        "total_output_slides": [number of slides in output]
    }},

    "required_keywords": {{
        "brand_name": "exact brand name from product description (e.g., Lumidew, CeraVe)",
        "brand_source_quote": "quote from product description proving brand exists",
        "product_type": "what the product is (e.g., Steam Eye Mask, Face Tape)",
        "purchase_location": "where to buy (e.g., amazon, their website)"
    }},

    "text_style": {{
        "font_style": "modern-clean | classic-elegant | handwritten-casual | bold-impact | minimal-thin",
        "font_weight": "thin / light / regular / medium / semibold / bold / black",
        "letter_spacing": "tight / normal / wide / very-wide",
        "font_color": "exact color description (e.g., pure white #FFFFFF, cream/off-white, soft blush pink)",
        "shadow": "none OR description (e.g., subtle gray drop shadow, soft edges)",
        "outline": "none OR description (e.g., thin black outline)",
        "background_box": "none OR description (e.g., semi-transparent black pill shape behind text)",
        "text_size": "small / medium / large",
        "visual_vibe": "overall feeling in 2-3 words (e.g., clean minimal, soft feminine)",
        "position_style": "where text is typically placed (e.g., centered middle, bottom third)"
    }},

    "visual_style": {{
        "color_temperature": "warm | cool | neutral | mixed",
        "color_palette": "description of dominant colors (e.g., soft pinks and creams, earth tones)",
        "lighting_style": "natural-soft | natural-harsh | golden-hour | studio | ambient-moody | bright-airy",
        "saturation": "muted | natural | vibrant | high-contrast",
        "filter_style": "description of editing style (e.g., slight grain, soft glow, clean and sharp)",
        "overall_aesthetic": "2-3 words (e.g., clean minimalist, warm cozy, moody editorial)"
    }},

    "target_audience": {{
        "demographic": "who this is for",
        "pain_points": ["problem 1", "problem 2"],
        "aspirations": ["desire 1", "desire 2"],
        "tone": "how they talk"
    }},

    "product_fit": {{
        "relevant_pain_point": "which audience problem this product solves",
        "benefit_angle": "how to position the product for THIS audience",
        "insertion_rationale": "why this position makes sense"
    }},

    "product_on_face": {{
        "show_on_persona": true | false,
        "reason": "why the product should/shouldn't be shown ON the persona's face (e.g., face tape, under-eye patches, forehead patches are worn ON face)"
    }},

    "structure": {{
        "total_slides": "same as original if replacing, original+1 if adding (EXCLUDING text-only slides)",
        "hook_index": 0,
        "body_indices": [1, 2, 3, 5],
        "product_index": "from product_placement.product_slide_index"
    }},

    "new_slides": [
        {{
            "slide_index": 0,
            "slide_type": "hook",
            "role_in_story": "Hook - grabs attention with relatable problem",
            "reference_image_index": 0,
            "has_persona": true,
            "shows_product_on_face": false,  // FALSE: original hook shows person but NO face tape patches visible on face
            "transformation_role": "before",  // "before" = problem state, "after" = improved state, null = not transformation
            "transformation_problem": "forehead_lines",  // REQUIRED when transformation_role is set! Options: under_eye, forehead_lines, smile_lines, crows_feet, acne, dull_skin, sagging, wrinkles
            "layout_type": "single",  // IMPORTANT: "single" = normal single image, "split_screen" = side-by-side comparison (DETECT THIS!)
            "split_config": null,  // If layout_type is "split_screen", set: {{ "orientation": "horizontal", "sections": ["before", "after"], "is_transformation": true }}
            "visual": {{
                "subject": "woman's face, selfie style",
                "framing": "close-up",
                "angle": "straight on",
                "position": "centered",
                "background": "blurred bedroom"
            }},
            "text_position_hint": "where text goes, what NOT to cover",
            "scene_variations": [
                // Generate exactly {hook_photo_var} scene variations, each with exactly {hook_text_var} text variations
                {{
                    "scene_description": "Girl in cozy loungewear, soft bedroom lighting. COMPOSITION: framing=close-up, angle=straight, position=center, background=blurred bedroom",
                    "text_variations": ["hook text - generate exactly {hook_text_var} items here"]
                }}
            ]
        }},
        {{
            "slide_index": 1,
            "slide_type": "body",
            "role_in_story": "Tip 1 - practical advice",
            "reference_image_index": 1,
            "has_persona": false,
            "shows_product_on_face": false,  // false: original slide 1 does NOT show face tape on person
            "transformation_role": null,  // null = not a before/after slide
            "transformation_problem": null,  // null when transformation_role is null
            "visual": {{
                "subject": "skincare product on nightstand",
                "framing": "medium shot",
                "angle": "slightly above",
                "position": "centered",
                "background": "cozy bedroom"
            }},
            "text_position_hint": "center middle",
            "scene_variations": [
                // Generate exactly {body_photo_var} scene variations, each with exactly {body_text_var} text variations
                {{
                    "scene_description": "Similar scene to original slide 1 - mimic the content type and vibe. COMPOSITION: framing=medium, angle=above, position=center, background=bedroom",
                    "text_variations": ["body text - generate exactly {body_text_var} items here"]
                }}
            ]
        }},
        {{
            "slide_index": 2,
            "slide_type": "body",
            "role_in_story": "Tip 2 - shows results/application",
            "reference_image_index": 2,
            "has_persona": true,
            "shows_product_on_face": true,  // TRUE: original slide 2 shows person WEARING face tape patches visibly ON their face!
            "transformation_role": "after",  // This slide shows results = "after"
            "transformation_problem": "forehead_lines",  // Match the problem from text
            "visual": {{
                "subject": "woman with face tape patches on forehead and under eyes",
                "framing": "close-up",
                "angle": "straight on",
                "position": "centered",
                "background": "bathroom mirror"
            }},
            "text_position_hint": "top of image, avoid face",
            "scene_variations": [
                {{
                    "scene_description": "Woman wearing face tape patches, morning skincare routine. COMPOSITION: framing=close-up, angle=straight, position=center, background=bathroom",
                    "text_variations": ["tip text about application"]
                }}
            ]
        }},
        {{
            "slide_index": 3,
            "slide_type": "body",
            "role_in_story": "Tip 3 - lifestyle advice",
            "reference_image_index": 3,
            "has_persona": true,
            "shows_product_on_face": false,  // FALSE: original slide 3 shows person but NO face tape visible on their face
            "transformation_role": null,  // Not a before/after slide
            "transformation_problem": null,
            "visual": {{
                "subject": "woman drinking water",
                "framing": "medium shot",
                "angle": "straight on",
                "position": "centered",
                "background": "kitchen"
            }},
            "text_position_hint": "bottom of image",
            "scene_variations": [
                {{
                    "scene_description": "Woman drinking glass of water in kitchen. COMPOSITION: framing=medium, angle=straight, position=center, background=kitchen",
                    "text_variations": ["hydration tip text"]
                }}
            ]
        }},
        {{
            "slide_index": 4,
            "slide_type": "product",
            "role_in_story": "Product recommendation - natural fit in the narrative",
            "reference_image_index": 4,
            "has_persona": false,
            "shows_product_on_face": false,  // FALSE: product slides show product PACKAGING, not worn on anyone's face
            "transformation_role": null,  // Product slides are never before/after
            "transformation_problem": null,
            "visual": {{
                "subject": "user's product prominently displayed",
                "framing": "medium shot",
                "angle": "straight on",
                "position": "centered",
                "background": "lifestyle setting matching slideshow vibe"
            }},
            "text_position_hint": "text at top, DO NOT cover product",
            "scene_variations": [
                // Product slide: exactly 1 scene variation with exactly {product_text_var} text variations
                {{
                    "scene_description": "User's product on nightstand with cozy bedroom background. COMPOSITION: framing=medium, angle=straight, position=center, background=bedroom",
                    "text_variations": ["product text with brand + amazon - generate exactly {product_text_var} items here"]
                }}
            ]
        }}
        // NOTE: Text-only slides (like "there she glows again", "follow for more") are EXCLUDED from output
    ]
}}

IMPORTANT - reference_image_index explained:
- This tells us which ORIGINAL slide to use as reference
- MIMIC both the STYLE (font, colors, layout) AND the TYPE of content
- Create SIMILAR content that matches what the original slide shows

CRITICAL RULES:
1. SLIDE COUNT:
   - EXCLUDE text-only slides (pure text on background, no person/product/action)
   - If competitor_detection.found == true: new_slides array has remaining slides (REPLACE competitor)
   - If competitor_detection.found == false: new_slides array has remaining slides + 1 (ADD product at end)
2. Exactly ONE slide with slide_type="product"
3. slide_type can ONLY be: "hook", "body", or "product" (NO "cta" type!)
4. Hook slides: {hook_photo_var} scene_variations, each with {hook_text_var} text_variations
5. Body slides: {body_photo_var} scene_variations, each with {body_text_var} text_variations
6. Product slides: 1 scene_variation with {product_text_var} text_variations
7. Each scene_variation MUST have a different scene_description (different take on same concept!)
8. has_persona: set to true if ORIGINAL slide shows a person, false otherwise
9. Include "visual" object for each slide with composition details
10. Include "role_in_story" for each slide describing its narrative purpose
11. scene_description MUST end with "COMPOSITION: framing=X, angle=Y, position=Z, background=W"
12. PHYSICAL CONSTRAINTS for scene_description:
    - POV (first-person) shots can only show ONE hand - never "hands" plural
    - If scene needs two objects, use "hand holding X, Y visible on counter/surface"
    - Never write physically impossible scenes (e.g., "hands holding two bottles" in POV)
    - Selfie shots: one hand holds phone, only other hand can be in frame
13. shows_product_on_face: CRITICAL - LOOK AT EACH ORIGINAL SLIDE IMAGE! Set true for EVERY slide where the original shows a person with face tape/patches ON their face (forehead, under eyes). Set false if the slide shows product packaging only, or person WITHOUT tape on face. If original has tape in 3 slides, set true for all 3!
14. persona.cultural_context: ALMOST ALWAYS null! Only set when content is SPECIFICALLY TEACHING a cultural beauty METHOD:
    ✅ SET cultural_context ONLY for these patterns:
    - "Japanese skincare routine" / "J-beauty method" / "why Japanese women have glass skin" → "Japanese"
    - "Korean glass skin routine" / "K-beauty secrets" / "10-step Korean routine" → "Korean"
    - "French pharmacy skincare" / "French girl beauty secrets" → "French"

    ❌ DO NOT set cultural_context for:
    - "Japanese women eat carbs" → null (just mentions nationality, NOT about Japanese skincare)
    - "Asian women have great skin" → null (general statement, not teaching a method)
    - "Women in Japan use this" → null (location mention, not cultural beauty practice)
    - Any product promotion that just MENTIONS a country → null

    DEFAULT TO null - 99% of content should be null. Only use when the ENTIRE slideshow is teaching a specific culture's beauty method/routine.
15. transformation_role: CRITICAL - detect before/after transformation based on SLIDE TEXT:

    SET transformation_role = "before" if slide text contains ANY of:
    - The word "before" (e.g., "before", "before ➡️", "before using")
    - Problem indicators: "wrinkles", "lines", "tired", "dull", "stressed"

    SET transformation_role = "after" if slide text contains ANY of:
    - The word "after" (e.g., "after", "after ✨", "after using")
    - Result words: "currently", "now", "results", "months later", "weeks in", "few months in"
    - Success indicators: "smooth", "glowing", "glass skin", "transformed"

    SET transformation_role = null for:
    - Product slides
    - Any slide NOT part of a before/after comparison

    ⚠️ HOOK SLIDES CAN BE "before" STATE:
    - If hook text describes a PROBLEM → transformation_role = "before"
    - Examples: "I had terrible smile lines", "My forehead wrinkles were so bad", "I looked so tired"
    - Hook showing the problem creates DRAMATIC opening for transformation story

    TEXT-BASED DETECTION IS CRITICAL:
    - If text literally says "before" -> MUST be transformation_role: "before"
    - If text literally says "after" -> MUST be transformation_role: "after"
    - Do NOT set ALL slides to "after" - look at EACH slide text individually!

16. transformation_problem: Detect the SPECIFIC skin problem from slide text:

    SET transformation_problem based on keywords in text:
    - "under eye", "eye bags", "dark circles", "eye lines", "crow", "around my eyes" → "under_eye"
    - "forehead", "11 lines", "eleven lines", "frown lines", "brow" → "forehead_lines"
    - "smile lines", "laugh lines", "nasolabial", "mouth lines" → "smile_lines"
    - "crow's feet", "crows feet", "eye wrinkles", "corner of eyes" → "crows_feet"
    - "acne", "pimples", "breakout", "blemish", "spots" → "acne"
    - "dull", "tired", "sallow", "lifeless", "glow" → "dull_skin"
    - "sagging", "jowls", "loose skin", "droopy", "lift" → "sagging"
    - "wrinkles", "lines", "aging" (generic) → "wrinkles"

    SET transformation_problem = null for:
    - Slides without transformation_role
    - Product slides
    - Lifestyle/filler slides

    ⚠️ CRITICAL OUTPUT REQUIREMENT:
    - EVERY slide MUST include "transformation_problem" field in output
    - If transformation_role is "before" or "after" → transformation_problem MUST be one of: under_eye, forehead_lines, smile_lines, crows_feet, acne, dull_skin, sagging, wrinkles
    - If transformation_role is null → transformation_problem = null
    - Use the SAME transformation_problem for ALL slides in a before/after sequence!
    - Example: text says "eye lines" → ALL transformation slides get transformation_problem: "under_eye"
"""

    # Build content with all images
    contents = [prompt]

    # Add all slideshow images
    for i, path in enumerate(slide_paths):
        contents.append(f"[SLIDE {i}]")
        contents.append(types.Part.from_bytes(
            data=_load_image_bytes(path),
            mime_type=_get_image_mime_type(path)
        ))

    # Add user's product image(s) last
    for i, product_path in enumerate(product_image_paths):
        if i == 0:
            contents.append("[USER'S PRODUCT IMAGE]")
        else:
            contents.append(f"[USER'S PRODUCT IMAGE {i+1}]")
        contents.append(types.Part.from_bytes(
            data=_load_image_bytes(product_path),
            mime_type=_get_image_mime_type(product_path)
        ))

    max_analysis_retries = 4
    last_error = None

    for attempt in range(1, max_analysis_retries + 1):
        try:
            # Get fresh client on retry (may rotate to different key)
            if attempt > 1:
                client, api_key = _get_client()
                log.info(f"Analysis retry {attempt}/{max_analysis_retries} with key {api_key[:8]}")

            log.debug(f"Calling {TEXT_MODEL} with {len(contents)} content parts")
            response = client.models.generate_content(
                model=TEXT_MODEL,
                contents=contents,
                config=types.GenerateContentConfig(
                    safety_settings=SAFETY_SETTINGS,  # Allow benign lifestyle content analysis
                    response_mime_type='application/json',  # Force structured JSON output
                )
            )
            elapsed = time.time() - start_time
            result_text = response.text
            log.debug(f"Analysis API response in {elapsed:.1f}s, response length: {len(result_text)}")

            # Parse JSON - extract JSON object from response
            start = result_text.find('{')
            end = result_text.rfind('}') + 1
            if start < 0 or end <= start:
                log.error("No valid JSON in analysis response")
                last_error = GeminiServiceError('No valid JSON in response')
                continue

            json_text = result_text[start:end]

            # Try parsing as-is first, then with repair
            try:
                analysis = json.loads(json_text)
            except json.JSONDecodeError as parse_err:
                log.warning(f"JSON parse failed (attempt {attempt}): {parse_err}. Trying repair...")
                repaired = _repair_json(json_text)
                try:
                    analysis = json.loads(repaired)
                    log.info(f"JSON repair successful on attempt {attempt}")
                except json.JSONDecodeError:
                    log.error(f"JSON repair failed on attempt {attempt}")
                    last_error = GeminiServiceError(f'Failed to parse analysis JSON: {parse_err}')
                    continue

            # Validate structure
            if 'new_slides' not in analysis:
                log.error("Missing new_slides in analysis")
                last_error = GeminiServiceError('Missing new_slides in analysis')
                continue

            # Validate slide count based on product_placement action
            # - "replace": same number of slides as original (or fewer if CTA excluded)
            # - "add": original + 1 (product slide added at end), or fewer if CTA excluded
            # Note: CTA slides may be excluded per our rules, so allow fewer slides
            product_placement = analysis.get('product_placement', {})
            placement_action = product_placement.get('action', 'add')
            max_expected_slides = num_slides if placement_action == 'replace' else num_slides + 1
            min_expected_slides = max(2, max_expected_slides - 2)  # Allow up to 2 slides excluded (CTA, etc.)

            actual_slides = len(analysis['new_slides'])
            if actual_slides < min_expected_slides or actual_slides > max_expected_slides:
                log.error(f"Slide count out of range: expected {min_expected_slides}-{max_expected_slides} (action={placement_action}), got {actual_slides}")
                last_error = GeminiServiceError(f"Expected {min_expected_slides}-{max_expected_slides} slides (action={placement_action}), got {actual_slides}")
                continue

            if actual_slides < max_expected_slides:
                log.info(f"Slide count adjusted: {actual_slides} slides (max was {max_expected_slides}, likely CTA excluded)")

            # Validate exactly one product slide
            product_slides = [s for s in analysis['new_slides'] if s.get('slide_type') == 'product']
            if len(product_slides) != 1:
                log.error(f"Product slide count error: expected 1, got {len(product_slides)}")
                last_error = GeminiServiceError(f"Expected exactly 1 product slide, got {len(product_slides)}")
                continue

            # All validations passed - save and return
            os.makedirs(output_dir, exist_ok=True)
            analysis_path = os.path.join(output_dir, 'analysis.json')
            with open(analysis_path, 'w') as f:
                json.dump(analysis, f, indent=2)

            slideshow_type = analysis.get('slideshow_type', 'unknown')
            if attempt > 1:
                log.info(f"Analysis succeeded on retry {attempt} in {elapsed:.1f}s: type={slideshow_type}, {len(analysis['new_slides'])} slides")
            else:
                log.info(f"Analysis complete in {elapsed:.1f}s: type={slideshow_type}, {len(analysis['new_slides'])} slides")
            return analysis

        except Exception as e:
            error_str = str(e)
            is_rate_limit = '429' in error_str or 'RESOURCE_EXHAUSTED' in error_str
            is_invalid_key = 'INVALID_ARGUMENT' in error_str or 'API_KEY_INVALID' in error_str or 'API key not valid' in error_str
            is_overloaded = '503' in error_str or 'UNAVAILABLE' in error_str

            # Record failure so the key manager can skip this key
            _record_api_usage(api_key, success=False, is_rate_limit=is_rate_limit, is_invalid_key=is_invalid_key)

            if is_invalid_key:
                log.error(f"Analysis attempt {attempt} failed: INVALID KEY {api_key[:8]}, will skip in future")
                last_error = GeminiServiceError(f'Analysis failed: {error_str}')
                continue

            if is_rate_limit:
                # Rotate to next key and retry (this key is exhausted, others may work)
                log.warning(f"Analysis attempt {attempt} rate limited (key {api_key[:8]}), rotating to next key...")
                last_error = GeminiServiceError(f'Analysis failed: {error_str}')
                continue

            if is_overloaded:
                # 503 = temporary Google overload, wait and retry with same or different key
                backoff = 10 * attempt  # 10s, 20s, 30s, 40s
                log.warning(f"Analysis attempt {attempt} got 503 UNAVAILABLE, backing off {backoff}s...")
                time.sleep(backoff)
                last_error = GeminiServiceError(f'Analysis failed: {error_str}')
                continue

            log.error(f"Analysis attempt {attempt} failed: {error_str}", exc_info=(attempt == max_analysis_retries))
            last_error = GeminiServiceError(f'Analysis failed: {error_str}')

    # All retries exhausted
    log.error(f"Analysis failed after {max_analysis_retries} attempts")
    raise last_error or GeminiServiceError('Analysis failed after all retries')


def _generate_single_image(
    client,
    api_key: str,
    slide_type: str,
    scene_description: str,
    text_content: str,
    text_position_hint: str,
    output_path: str,
    reference_image_path: str,
    product_image_path: Optional[str] = None,
    persona_reference_path: Optional[str] = None,
    has_persona: bool = False,
    text_style: Optional[dict] = None,
    visual_style: Optional[dict] = None,
    persona_info: Optional[dict] = None,
    version: int = 1,
    clean_image_mode: bool = False,
    product_description: str = "",
    shows_product_on_face: bool = False,
    transformation_role: Optional[str] = None,  # "before", "after", or None
    transformation_problem: Optional[str] = None,  # "under_eye", "forehead_lines", "smile_lines", etc.
    layout_type: str = "single",  # "single" or "split_screen"
    split_config: Optional[dict] = None  # {"orientation": "horizontal", "sections": ["before", "after"], "is_transformation": true}
) -> str:
    """
    Generate a single image with clear image labeling.

    Image roles:
    - STYLE_REFERENCE: Visual reference for composition, mood, lighting
    - PERSONA_REFERENCE: Use this person's appearance for consistency
    - PRODUCT_PHOTO: User's product image (base for product slides)

    Args:
        clean_image_mode: If True, generate image WITHOUT any text overlay
                          (text will be added by PIL with manual preset)

    Text style is passed explicitly via text_style dict for accurate font matching.
    """

    # Build transformation instruction based on transformation_role AND transformation_problem
    # Problem-specific before/after visuals for dramatic contrast

    # Auto-detect problem from text if not provided
    def detect_problem_from_text(text: str) -> str:
        """Detect skin problem type from text content."""
        if not text:
            return "wrinkles"
        text_lower = text.lower()

        # Check for specific problem keywords
        if any(kw in text_lower for kw in ["under eye", "eye bags", "dark circles", "eye lines", "around my eyes", "crow"]):
            return "under_eye"
        if any(kw in text_lower for kw in ["forehead", "11 lines", "eleven lines", "frown lines", "brow"]):
            return "forehead_lines"
        if any(kw in text_lower for kw in ["smile lines", "laugh lines", "nasolabial", "mouth lines"]):
            return "smile_lines"
        if any(kw in text_lower for kw in ["crow's feet", "crows feet", "eye wrinkles", "corner of eyes"]):
            return "crows_feet"
        if any(kw in text_lower for kw in ["acne", "pimples", "breakout", "blemish", "spots"]):
            return "acne"
        if any(kw in text_lower for kw in ["dull", "tired", "sallow", "lifeless"]):
            return "dull_skin"
        if any(kw in text_lower for kw in ["sagging", "jowls", "loose skin", "droopy", "lift"]):
            return "sagging"

        return "wrinkles"  # Default fallback

    # If transformation_problem not provided, detect from text
    if not transformation_problem and transformation_role:
        transformation_problem = detect_problem_from_text(text_content + " " + scene_description)
        logger.info(f"Auto-detected transformation_problem: {transformation_problem} from text")

    PROBLEM_VISUALS = {
        "under_eye": {
            "before": """
PROBLEM INTENSITY: 70% - Must be IMMEDIATELY visible!
- Under-eye area: DEEP DARK circles (purple/blue/brown tint), PUFFY bags, HOLLOW sunken appearance
- Shadows under eyes are DARK and PROMINENT - like someone who hasn't slept in days
- Skin under eyes looks THIN, CREPEY, DEHYDRATED with visible texture
- Expression: Tired, slightly concerned, exhausted look
- LIGHTING: Slightly harsh/unflattering to emphasize the darkness and texture
- Think: "6am selfie after a sleepless night" - this is WHY they need the product""",
            "after": """
RESULT: 100% improvement - DRAMATIC transformation!
- Under-eye area: BRIGHT, LUMINOUS, no dark circles whatsoever, smooth and PLUMP
- Eye area looks WELL-RESTED, YOUTHFUL, REFRESHED - like 8 hours of perfect sleep
- NO shadows, NO puffiness, NO bags - completely smooth
- Expression: Happy, confident, glowing
- LIGHTING: Soft, flattering, golden hour quality
- Think: "Best photo they've ever taken" - product WORKED"""
        },
        "forehead_lines": {
            "before": """
PROBLEM INTENSITY: 70% - Must be IMMEDIATELY visible!
- Forehead: DEEP HORIZONTAL LINES clearly etched across forehead
- Visible "11 LINES" (frown lines) between brows - prominent vertical creases
- Forehead skin looks TENSE, AGED, WORRIED with rough uneven texture
- Expression: Slightly furrowed brow, concerned or stressed look
- LIGHTING: Direct/overhead to cast shadows in the lines and make them deeper
- Think: "Zoom meeting screenshot that made you cringe" - this is WHY they need the product""",
            "after": """
RESULT: 100% improvement - DRAMATIC transformation!
- Forehead: COMPLETELY SMOOTH like glass, no lines whatsoever
- NO "11 lines" - frown area is relaxed, smooth, line-free
- Skin texture is FLAWLESS, even, poreless
- Expression: Relaxed, happy, confident
- LIGHTING: Soft, diffused, flattering
- Think: "Botox results without Botox" - product WORKED"""
        },
        "smile_lines": {
            "before": """
PROBLEM INTENSITY: 70% - Must be IMMEDIATELY visible!
- Smile lines (nasolabial folds): DEEP VISIBLE CREASES from nose to mouth corners
- Lines are PROMINENT even when face is relaxed - not just when smiling
- Creates an AGED, TIRED, SAGGING appearance around lower face
- Skin around mouth area looks LOOSE, less firm
- Expression: Neutral or slightly sad - not smiling (to show lines at rest)
- LIGHTING: Side lighting to emphasize the depth of the folds
- Think: "Photo that made you feel old" - this is WHY they need the product""",
            "after": """
RESULT: 100% improvement - DRAMATIC transformation!
- Smile lines: DRAMATICALLY SOFTENED, barely visible even when smiling
- Face looks LIFTED, YOUTHFUL, TIGHT around mouth and cheeks
- Skin looks FIRM, TONED, bouncy
- Can smile naturally WITHOUT deep creases appearing
- Expression: Happy, confident, smiling
- LIGHTING: Soft, flattering, front-facing
- Think: "10 years younger" - product WORKED"""
        },
        "crows_feet": {
            "before": """
PROBLEM INTENSITY: 70% - Must be IMMEDIATELY visible!
- Crow's feet: VISIBLE DEEP LINES radiating from outer eye corners
- Lines extend toward TEMPLES and are noticeable at rest
- Eye area looks AGED, WEATHERED, sun-damaged
- Fine lines form a FAN pattern from eye corners
- Expression: Neutral or slight squint to show the lines
- LIGHTING: Direct light to emphasize texture and lines
- Think: "Why do I look so old in photos" - this is WHY they need the product""",
            "after": """
RESULT: 100% improvement - DRAMATIC transformation!
- Crow's feet: SMOOTH eye corners, NO radiating lines
- Eye area looks YOUTHFUL, FRESH even when smiling or squinting
- Skin around eyes is TIGHT, SMOOTH, elastic
- NO fine lines at temples - completely smooth transition
- Expression: Happy, eyes bright and youthful
- LIGHTING: Soft, golden, flattering
- Think: "Eyes of a 25-year-old" - product WORKED"""
        },
        "acne": {
            "before": """
PROBLEM INTENSITY: 70% - Must be IMMEDIATELY visible!
- Skin: VISIBLE RED PIMPLES, BUMPY uneven texture, INFLAMED areas
- Active breakouts across cheeks, chin, or forehead - multiple spots visible
- Skin looks TROUBLED, ANGRY, UNEVEN with visible texture
- Some visible scarring or discoloration from past breakouts
- Expression: Self-conscious, avoiding direct eye contact
- LIGHTING: Clear enough to show the texture and redness
- Think: "Didn't want to leave the house" - this is WHY they need the product""",
            "after": """
RESULT: 100% improvement - DRAMATIC transformation!
- Skin: CLEAR, SMOOTH, no active breakouts whatsoever
- Even skin tone, NO redness, NO inflammation
- Skin looks HEALTHY, CALM, BALANCED, poreless
- Scars significantly faded, overall CLARITY and brightness
- Expression: Confident, happy, proud of skin
- LIGHTING: Soft, natural, showing off clear skin
- Think: "Finally confident without makeup" - product WORKED"""
        },
        "dull_skin": {
            "before": """
PROBLEM INTENSITY: 70% - Must be IMMEDIATELY visible!
- Skin: DULL, LIFELESS, SALLOW yellowish/greyish complexion
- ZERO natural radiance - skin absorbs light instead of reflecting it
- Looks TIRED, GREY, DEHYDRATED, almost ashy
- UNEVEN tone, LACKLUSTER, flat appearance with no depth
- Expression: Tired, low energy, washed out
- LIGHTING: Flat lighting that emphasizes the dullness
- Think: "Skin looks dead even with makeup" - this is WHY they need the product""",
            "after": """
RESULT: 100% improvement - DRAMATIC transformation!
- Skin: RADIANT, GLOWING, LUMINOUS complexion that REFLECTS light
- Healthy natural RADIANCE and DEWINESS - lit from within
- Looks ENERGIZED, FRESH, HYDRATED, alive
- EVEN tone, beautiful LIT-FROM-WITHIN glow
- Expression: Vibrant, energetic, happy
- LIGHTING: Soft with visible skin radiance
- Think: "Glass skin without filters" - product WORKED"""
        },
        "sagging": {
            "before": """
PROBLEM INTENSITY: 70% - Must be IMMEDIATELY visible!
- Skin: LOOSE, visibly SAGGING along jawline and cheeks
- JOWLS visible, UNDEFINED soft jawline with no sharp angles
- Face looks DROOPY, lacking FIRMNESS, gravity pulling down
- LOSS of facial contour - everything looks like it's sliding down
- Expression: Neutral, slightly tired, gravity-affected
- LIGHTING: Side angle to show the lack of definition
- Think: "When did my face start melting" - this is WHY they need the product""",
            "after": """
RESULT: 100% improvement - DRAMATIC transformation!
- Skin: LIFTED, FIRM jawline, DEFINED sharp contours
- NO jowls, SCULPTED facial definition like a facelift
- Face looks LIFTED, TIGHT, TONED, defying gravity
- YOUTHFUL facial structure with clear angles
- Expression: Confident, lifted, proud
- LIGHTING: Slightly angled to show the definition
- Think: "Non-surgical facelift results" - product WORKED"""
        },
        "wrinkles": {
            "before": """
PROBLEM INTENSITY: 70% - Must be IMMEDIATELY visible!
- Skin: MULTIPLE VISIBLE fine lines and wrinkles throughout face
- Forehead, eye area, mouth area - ALL show signs of aging
- Skin texture is UNEVEN, CREASED, rough to the eye
- Overall AGED, WEATHERED appearance - looks older than actual age
- Expression: Concerned, aware of the aging
- LIGHTING: Clear enough to show all the texture and lines
- Think: "Photo that made you want to try something" - this is WHY they need the product""",
            "after": """
RESULT: 100% improvement - DRAMATIC transformation!
- Skin: SMOOTH, wrinkles DRAMATICALLY reduced - barely any lines visible
- YOUTHFUL, PLUMP, HYDRATED bouncy appearance
- Skin texture is EVEN, REFINED, poreless
- Overall REJUVENATED, YOUNGER look - years taken off
- Expression: Happy, confident, glowing
- LIGHTING: Soft, golden, flattering
- Think: "Turned back the clock" - product WORKED"""
        }
    }

    # Get problem-specific visuals or use generic wrinkles as fallback
    problem_key = transformation_problem if transformation_problem in PROBLEM_VISUALS else "wrinkles"
    problem_visuals = PROBLEM_VISUALS.get(problem_key, PROBLEM_VISUALS["wrinkles"])

    if transformation_role == 'after':
        transformation_instruction = f"""
<transformation role="after" problem="{problem_key}">
This is an "AFTER" transformation slide - show DRAMATIC, VISIBLE improvement.
The difference from "before" should be INSTANTLY NOTICEABLE at a glance.

<target_improvement>
{problem_visuals["after"]}
</target_improvement>

<general_appearance>
- Person looks well-rested, energized, HAPPY
- Better posture, CONFIDENT expression, maybe slight smile
- BRIGHTER, SOFTER lighting to enhance the "glow up"
- Think "best photo they've ever taken" / "post-facial selfie"
</general_appearance>

<contrast_requirement>
⚠️ CRITICAL: This must look DRAMATICALLY DIFFERENT from "before" slides!
- Viewer should see the transformation INSTANTLY at a glance
- Side-by-side comparison would show OBVIOUS improvement
- This is the "success story" - make it GLOW
</contrast_requirement>

<constraint>DO NOT show ANY of the original problems - this is the SUCCESS/RESULT slide. ZERO wrinkles, lines, or issues visible.</constraint>
</transformation>
"""
    elif transformation_role == 'before':
        transformation_instruction = f"""
<transformation role="before" problem="{problem_key}">
This is a "BEFORE" transformation slide - show VISIBLE, OBVIOUS problems.
This creates DRAMATIC contrast with the "after" slides.

<target_problems>
{problem_visuals["before"]}
</target_problems>

<general_appearance>
- Person looks TIRED, STRESSED, or CONCERNED about their skin
- UNFLATTERING lighting that emphasizes texture and problems
- Expression shows awareness/concern about the problem
- Think "unflattering photo that made you want to try something new"
</general_appearance>

<contrast_requirement>
⚠️ CRITICAL: This must look DRAMATICALLY DIFFERENT from "after" slides!
- Viewer should INSTANTLY see what the problem is
- The issues should be OBVIOUS, not subtle
- This is "why they needed the product" - make it CLEAR
</contrast_requirement>

<constraint>Make problems OBVIOUS at first glance - viewer should INSTANTLY see what's wrong. Think "the photo that made you buy the product".</constraint>
</transformation>
"""
    else:
        transformation_instruction = ""

    # Pre-process pipe character: Convert " | " to actual newline for Gemini
    # This prevents the literal "|" from appearing in generated images
    if text_content and " | " in text_content:
        original_text = text_content
        text_content = text_content.replace(" | ", "\n")
        logger.info(f"Pipe separator converted to newline: '{original_text}' -> '{text_content}'")

    # ===== REUSABLE XML BLOCKS =====
    # These are common sections used across all persona prompts

    # CONDITIONAL skin_realism based on transformation_role
    if transformation_role == "after":
        # AFTER slides: Allow perfect glowing skin for transformation results
        skin_realism_block = """
<skin_quality role="transformation_after">
⚠️ OVERRIDE: This is the TRANSFORMATION RESULT - show PERFECT GLASS SKIN!

REQUIRED skin appearance:
- PORELESS, SMOOTH, FLAWLESS texture - like airbrushed perfection
- GLOWING, LUMINOUS, RADIANT - skin REFLECTS light beautifully
- ZERO wrinkles, lines, dark circles, or ANY imperfections
- DEWY, healthy, hydrated - "just got a facial" look
- Even skin tone, no redness, no texture

LIGHTING: Soft, flattering, golden-hour quality
EXPRESSION: Happy, confident, proud of skin

Think: "Glass skin filter IRL" / "Skincare ad model" / "Best skin day ever"
This is the SUCCESS PHOTO - the product WORKED. Make it OBVIOUS!

DO NOT apply normal skin realism - this is the PERFECT RESULT!
</skin_quality>
"""
    elif transformation_role == "before":
        # BEFORE slides: Emphasize visible problems
        skin_realism_block = """
<skin_quality role="transformation_before">
⚠️ CRITICAL: This is the "BEFORE" state - show THE PROBLEM at 70% intensity!

REQUIRED skin appearance:
- VISIBLE skin issues: lines, wrinkles, texture, dullness, bags - NOT subtle!
- Skin should look TIRED, AGED, PROBLEMATIC, TEXTURED
- Problems should be IMMEDIATELY OBVIOUS at first glance
- NO healthy glow, NO radiance, NO dewiness
- Slightly uneven tone, visible texture, clear problems

LIGHTING: Slightly harsh or unflattering to emphasize texture/problems
EXPRESSION: Tired, concerned, or neutral - NOT happy/glowing

Think: "6am bathroom lighting selfie" / "Photo that made you buy skincare"
This is WHY THEY NEED THE PRODUCT - make the problem CLEAR!

DO NOT make skin look good - this is the PROBLEM STATE!
</skin_quality>
"""
    else:
        # Normal slides: Natural realistic skin - STRONGER instructions to avoid AI-perfect look
        skin_realism_block = """
<skin_realism>
CRITICAL - Generate AUTHENTIC, NOT AI-PERFECT faces. This is NON-NEGOTIABLE.

<required_skin_texture>
- VISIBLE natural pores (especially on nose, cheeks, forehead) - NOT poreless!
- Subtle skin texture variation (NOT perfectly smooth)
- Natural slight imperfections (tiny moles, freckles, minor blemishes okay)
- Realistic under-eye area (slight natural darkness, texture - NOT airbrushed)
- Soft T-zone oiliness/shine (natural, not plastic)
- Natural baby hairs at hairline
- Asymmetry that exists in real faces (nobody is perfectly symmetrical)
</required_skin_texture>

<photography_realism>
- Light grain typical of iPhone/phone cameras
- Natural shadow gradients (NOT perfect studio lighting)
- Slight depth-of-field blur on non-focal areas
- Realistic catch lights in eyes (round, natural)
- Colors should feel real, NOT oversaturated or filtered
</photography_realism>

<strictly_avoid>
- Poreless "glass skin" look (this screams AI!)
- Over-smoothed/airbrushed appearance
- Perfectly symmetrical features
- Plastic/waxy skin texture
- Overly bright/glowing skin
- "Instagram filter" perfection
- Perfect even skin tone with no variation
- Unnaturally smooth forehead
</strictly_avoid>

Think: "iPhone selfie from a real person" NOT "AI beauty filter app"
The image should pass as a REAL PERSON'S PHOTO, not AI-generated content.
</skin_realism>
"""

    text_visual_match_block = """
<text_visual_match>
READ THE TEXT CAREFULLY and match the visual to what it describes:

- SKIN CONDITIONS: If text mentions ANY skin conditions or problems → show them visibly
- SKIN QUALITY: If text mentions ANY skin appearance descriptors → reflect that quality (glowing, dull, clear, textured, etc.)
- BODY TYPE: If text mentions ANY body type descriptors → generate that body type
- HAIR: If text mentions ANY hair characteristics → match them (length, texture, style, etc.)
- AGE: If text implies ANY age indicators → reflect that appropriately
- PHYSICAL FEATURES: If text describes ANY other physical attributes → illustrate them

The image should ILLUSTRATE what the text is talking about.
Don't interpret - show EXACTLY what the words describe.
</text_visual_match>
"""

    # Detect if text_position_hint indicates MULTIPLE positions (e.g., "top and bottom")
    # Only then should we instruct Gemini to split text across positions
    is_multi_position = text_position_hint and any(
        indicator in text_position_hint.lower()
        for indicator in [' and ', 'multiple', 'both']
    )

    # Build text_placement_block - only include position-splitting for multi-position hints
    if is_multi_position:
        text_placement_block = """
<text_placement>
<rules>
- NEVER cover face or person with text
- NEVER cover main objects/products with text
- Text should be in empty/background areas only
</rules>

<multi_position_text>
⚠️ TEXT POSITION HINT SAYS MULTIPLE POSITIONS - SPLIT THE TEXT!
If text contains multiple lines (newlines):
- Place FIRST line at TOP of image
- Place LAST line at BOTTOM of image
- NEVER place all text in the same location
- NEVER duplicate - each line appears ONCE in its position
</multi_position_text>
</text_placement>
"""
    else:
        # Single position - treat newlines as line breaks, NOT position splitting
        text_placement_block = """
<text_placement>
<rules>
- NEVER cover face or person with text
- NEVER cover main objects/products with text
- Text should be in empty/background areas only
- If unsure, place text at TOP or BOTTOM edges of image
</rules>

<single_position>
Place ALL text lines together in ONE location (the position hint).
If text has multiple lines, stack them vertically in the SAME area.
Do NOT split text across different parts of the image.
Do NOT duplicate any text - each line appears exactly ONCE.
</single_position>
</text_placement>
"""

    single_person_constraint = "<constraint>Only ONE person in the image - never two people!</constraint>"

    # Handle clean image mode - NO TEXT in generated image
    if clean_image_mode:
        text_style_instruction = """⚠️ CRITICAL - NO TEXT MODE:
DO NOT include ANY text, captions, or overlays in this image.
Generate a CLEAN image with NO text whatsoever.
The image should be suitable for adding text overlays later.
Leave clear space in appropriate areas for text placement.
Focus on creating a beautiful, clean visual composition WITHOUT any text."""
    # Build text style instruction from analysis
    elif text_style:
        background_box = text_style.get('background_box', 'none')

        # Build box override based on whether box/pill style is detected
        box_override = ""
        if background_box and ('box' in background_box.lower() or 'pill' in background_box.lower()):
            box_override = """
<text_background_override>
DETECTED: Text has background box/pill in reference
- Use white (#FFFFFF) rounded rectangle behind text
- Box should tightly hug text with ~15-20px padding
- Each line gets its OWN separate box (stack of pills effect)
- DO NOT make one giant box for all lines
</text_background_override>
"""
        else:
            # Explicit NO BOX instruction when reference has no background
            box_override = """
<text_background_override>
⛔ NO BACKGROUND BOX - Reference has NO text background!
- Text must have NO background box, NO pill, NO rectangle behind it
- Use ONLY: font color + outline/stroke for contrast
- If text needs visibility, use thicker outline stroke - NOT a box
- ZERO semi-transparent backgrounds, ZERO solid backgrounds
- This is CRITICAL - adding a box will ruin the style match
</text_background_override>
"""

        text_style_instruction = f"""<text_style>
<primary_rule>
COPY the EXACT text style from [STYLE_REFERENCE] image - font, color, size, effects, position.
DO NOT add backgrounds/boxes/effects unless the reference clearly has them.
</primary_rule>
{box_override}
<text_size>
- Text must be SMALL - approximately 3-5% of image height
- Maximum 2 lines of text total
- Each line maximum 6 words
- The IMAGE is the focus, text is a subtle accent only
- Think "small Instagram caption" not "poster headline"
- If in doubt, make text SMALLER
- NEVER duplicate/repeat the same text line twice
</text_size>
</text_style>
"""
    else:
        text_style_instruction = "Use clean, bold, white sans-serif text with subtle shadow."

    # Build visual style instruction from analysis
    # For persona slides, use softer matching that explicitly excludes the person
    if visual_style:
        if has_persona:
            # SOFTER version for persona slides - match lighting/colors but NOT the person
            visual_style_instruction = f"""
<visual_style type="persona">
<match_from_reference>
Copy the lighting and color grading from [STYLE_REFERENCE]:
- Color temperature, lighting style, saturation, overall aesthetic
</match_from_reference>
<do_not_copy>
- DO NOT copy the person's appearance - generate a DIFFERENT person
- Reference is for mood/lighting/colors ONLY, not the face or features
</do_not_copy>
</visual_style>
"""
        else:
            # Full matching for non-persona slides
            visual_style_instruction = f"""
<visual_style type="scene">
<match_from_reference>
Copy the EXACT visual style from [STYLE_REFERENCE]:
- Color grading, lighting, saturation, filter/editing style
- The generated image should look like it belongs in the same photo series
</match_from_reference>
</visual_style>
"""
    else:
        visual_style_instruction = ""

    # Quality constraints to prevent weird/AI-looking images
    quality_constraints = """
<hard_constraints>
NEVER violate these rules:

<modesty>
- Person must ALWAYS be appropriately clothed (casual clothes, activewear, etc.)
- NO revealing clothing, swimwear, lingerie, towels-only, or bare shoulders
- NO suggestive poses or intimate settings
- Safe for all audiences - think "Instagram-appropriate family content"
- If scene involves bath/spa/water: show ONLY partial body parts like legs with dry brushing/scrubbing, or hands with products, or person CLOTHED near water - NEVER full body in water
- When in doubt, add a t-shirt, tank top, or casual top
</modesty>

<text_accuracy>
- Use ONLY the EXACT text provided in "TEXT TO DISPLAY"
- DO NOT invent, rephrase, summarize, or modify the text in any way
- DO NOT add extra words, change word order, or paraphrase
- Copy the text CHARACTER BY CHARACTER as provided
- If text looks wrong or gibberish, still copy it exactly - do not "fix" it
</text_accuracy>

<no_product_mixing>
- DO NOT copy any products from the reference images onto the persona
- DO NOT show face patches, nose strips, under-eye patches, or ANY skincare products ON the persona's face
- The persona's face must be CLEAN - no products attached to skin
- If the reference shows someone wearing patches/products, IGNORE those products entirely
- Only the PRODUCT SLIDE should show the user's actual product
</no_product_mixing>

<format>
- Image content MUST extend to ALL FOUR EDGES
- NO black bars, borders, or frames on ANY side (top, bottom, left, right)
- NO letterboxing or pillarboxing
- NO phone UI elements, navigation bars, or "Share/Edit/Delete" buttons
- The scene/background must fill the ENTIRE frame edge-to-edge
- Think "camera viewport" - subject fills the whole 9:16 frame with NO empty borders
</format>

</hard_constraints>

<quality>
- This must look like authentic TikTok/Instagram content
- Clean, professional, aspirational aesthetic
- Proper lighting - natural or soft studio lighting
- Sharp focus on main subjects
- Harmonious color palette that matches the mood
</quality>

<avoid>
- Surreal, abstract, or "obviously AI" aesthetics
- Distorted objects, text, or proportions
- Unnatural color combinations or lighting
- Blurry or low-quality appearance
- Cluttered or chaotic compositions
- Floating objects or impossible physics
- ANY black/dark bars or frames at edges (ESPECIALLY at bottom!)
- Phone screenshots with visible UI elements
- Steam or vapor effects (tea steam, coffee steam, humidifier mist, candle smoke) - these look fake/AI-generated
</avoid>
"""

    if slide_type == 'product':
        # PRODUCT SLIDE: EDIT the product image - add text only, DO NOT regenerate
        logger.info(f"PRODUCT_SLIDE_DEBUG: slide_type=product, product_image_path={product_image_path}, has_product_image={product_image_path is not None}")

        # CRITICAL FIX: Validate and fallback to reference_image if product image is missing
        actual_product_path = product_image_path
        if not product_image_path or not os.path.exists(product_image_path):
            logger.warning(f"PRODUCT_SLIDE_WARNING: No valid product image at '{product_image_path}', using reference image as fallback")
            actual_product_path = reference_image_path

        prompt = f"""<task>EDIT this product image by adding text overlay ONLY.</task>

<images>
<product_photo>The product image. DO NOT regenerate or modify. Keep EXACTLY as is.</product_photo>
<style_reference>Reference for TEXT STYLING only (typography, color, shadow effects).</style_reference>
</images>

{text_style_instruction}

<content>
<text>{text_content}</text>
<position>{text_position_hint}</position>
</content>

<text_placement>
- Place text in empty/background areas
- NEVER cover the product
- If unsure, use TOP or BOTTOM edges
- If text has multiple lines, keep them together in ONE area
- NEVER duplicate text - each line appears exactly ONCE
</text_placement>

<hard_constraints>
DO NOT:
- Regenerate or recreate the product image
- Change the product appearance, angle, lighting, or colors
- Add new objects, props, or backgrounds
- Modify composition or framing
- Create a "new version" of the product
</hard_constraints>

<output>The original [PRODUCT_PHOTO] with text overlay added. The product image itself must be UNCHANGED.</output>

{quality_constraints}"""

        contents = [
            prompt,
            "[PRODUCT_PHOTO]",
            types.Part.from_bytes(
                data=_load_image_bytes(actual_product_path),  # Use validated path with fallback
                mime_type=_get_image_mime_type(actual_product_path)
            ),
            "[STYLE_REFERENCE]",
            types.Part.from_bytes(
                data=_load_image_bytes(reference_image_path),
                mime_type=_get_image_mime_type(reference_image_path)
            )
        ]

    elif slide_type == 'cta':
        # CTA SLIDE: Usually text-focused, simple background
        prompt = f"""<task>Generate a TikTok CTA (call-to-action) slide.</task>

{text_style_instruction}
{visual_style_instruction}

<images>
<style_reference>Reference CTA slide for background style and composition.</style_reference>
</images>

<content>
<text>{text_content}</text>
<position>{text_position_hint}</position>
</content>

{quality_constraints}"""

        contents = [
            prompt,
            "[STYLE_REFERENCE]",
            types.Part.from_bytes(
                data=_load_image_bytes(reference_image_path),
                mime_type=_get_image_mime_type(reference_image_path)
            )
        ]

    else:
        # HOOK or BODY SLIDE
        slide_label = "HOOK" if slide_type == "hook" else "TIP"

        # Note: Each photo variation now has its own scene from analysis
        # No need for variation instructions - scene_description already differs per photo_var
        variation_instruction = ""

        # ===== SPLIT-SCREEN LAYOUT (Before/After) =====
        if layout_type == "split_screen" and split_config:
            orientation = split_config.get('orientation', 'horizontal')
            sections = split_config.get('sections', ['before', 'after'])
            is_transformation = split_config.get('is_transformation', True)

            logger.info(f"SPLIT_SCREEN_DEBUG: Generating split-screen {sections[0]}/{sections[1]} layout, has_persona_ref={bool(persona_reference_path)}")

            # Determine left/right or top/bottom based on orientation
            first_section = "LEFT" if orientation == "horizontal" else "TOP"
            second_section = "RIGHT" if orientation == "horizontal" else "BOTTOM"

            # Get transformation problem for skin display
            problem_display = transformation_problem or "wrinkles"

            # IMPORTANT: If no persona_reference_path, we need to CREATE a new diverse persona
            # (Don't just copy from reference_image_path - that copies the original TikTok person!)
            if not persona_reference_path and has_persona:
                # Generate NEW diverse persona for split-screen
                if USE_EXPANDED_PERSONAS:
                    cultural_context = persona_info.get('cultural_context') if persona_info else None
                    diverse_persona = generate_diverse_persona(
                        target_audience=persona_info,
                        version=version,
                        cultural_context=cultural_context
                    )
                    logger.info(f"Generated diverse persona v{version} for SPLIT-SCREEN: {get_persona_summary(diverse_persona)}" +
                               (f" (cultural_context={cultural_context})" if cultural_context else ""))

                    split_persona_instruction = f"""
<persona_instruction>
GENERATE A NEW PERSON FOR THE SPLIT-SCREEN - DO NOT COPY THE REFERENCE PERSON

{format_persona_prompt(diverse_persona)}

This SAME generated person must appear in BOTH halves of the split-screen.
Match from reference ONLY: composition style, lighting mood, setting vibe.
</persona_instruction>"""
                else:
                    # Fallback: use basic demographics
                    split_persona_instruction = f"""
<persona_instruction>
GENERATE A NEW PERSON - DO NOT COPY THE REFERENCE PERSON
- Gender: {persona_info.get('gender', 'female') if persona_info else 'female'}
- Age Range: {persona_info.get('age_range', '20s-30s') if persona_info else '20s-30s'}
This SAME generated person must appear in BOTH halves of the split-screen.
Match from reference ONLY: composition style, lighting mood.
</persona_instruction>"""

                persona_images_block = """<images>
<style_reference>
Reference for the split-screen layout style, composition, and text styling.
DO NOT copy this person - generate the NEW person described above.
</style_reference>
</images>"""
            else:
                # Use existing persona reference (subsequent slides or body slides)
                split_persona_instruction = ""
                persona_images_block = """<images>
<persona_reference>
THE PERSON TO USE. Generate the EXACT SAME PERSON in BOTH halves of the split-screen.
This is the person's identity - copy exactly.
</persona_reference>
<style_reference>
Reference for the split-screen layout style, composition, and text styling.
</style_reference>
</images>"""

            prompt = f"""<task>Generate a TikTok split-screen BEFORE/AFTER comparison image.</task>

{text_style_instruction}
{visual_style_instruction}
{split_persona_instruction}
{persona_images_block}

<split_screen_layout>
Create ONE image divided into TWO distinct sections:

<{orientation}_split>
{first_section} SECTION ({sections[0].upper()}):
{"- Show the PROBLEM state - visible " + problem_display + " at 70% intensity" if sections[0] == "before" else "- Show the RESULT state - perfect glowing skin, problem SOLVED"}
{"- Skin should look TIRED, TEXTURED, with visible issues" if sections[0] == "before" else "- Skin should look RADIANT, SMOOTH, PORELESS"}
{"- Slightly harsh or unflattering lighting to emphasize problems" if sections[0] == "before" else "- Soft, flattering golden-hour lighting"}
{"- Expression: tired, concerned, or neutral" if sections[0] == "before" else "- Expression: happy, confident, proud"}

{second_section} SECTION ({sections[1].upper()}):
{"- Show the PROBLEM state - visible " + problem_display + " at 70% intensity" if sections[1] == "before" else "- Show the RESULT state - perfect glowing skin, problem SOLVED"}
{"- Skin should look TIRED, TEXTURED, with visible issues" if sections[1] == "before" else "- Skin should look RADIANT, SMOOTH, PORELESS"}
{"- Slightly harsh or unflattering lighting to emphasize problems" if sections[1] == "before" else "- Soft, flattering golden-hour lighting"}
{"- Expression: tired, concerned, or neutral" if sections[1] == "before" else "- Expression: happy, confident, proud"}
</{orientation}_split>

<consistency>
CRITICAL - Both sections MUST show the EXACT SAME PERSON:
- Same face shape, features, bone structure
- Same hair color and style
- Same skin tone (different QUALITY, not different person!)
- Similar background (can shift slightly)
</consistency>

<composition_matching>
CRITICAL - IDENTICAL COMPOSITION IN BOTH HALVES:
- Person must be CENTERED in BOTH sections (not off to the side!)
- Same framing: if close-up on left, close-up on right
- Same camera angle: if straight-on on left, straight-on on right
- Same body positioning: if facing camera on left, facing camera on right
- Same head tilt/position: MIRROR the pose exactly
- Person fills the SAME amount of frame in both sections
- DO NOT have person centered in one half and off-center in the other!
</composition_matching>

<visual_separation>
- Clear visual distinction between the two sections
- Subtle divider line OR gradient transition between sections
- Each section should be clearly "before" vs "after" at a glance
- The ONLY difference should be skin quality/lighting, NOT positioning
</visual_separation>
</split_screen_layout>

<content>
<text>{text_content}</text>
<position>{text_position_hint}</position>
</content>

<text_placement>
- Place text in empty/background areas
- NEVER cover face in either section
- Text can span across both sections if positioned at top or bottom
- If using labels like "before/after", place them WITHIN each section
</text_placement>

{quality_constraints}"""

            # Build contents based on whether we have persona reference or generating new
            if persona_reference_path:
                # Use existing persona reference
                contents = [
                    prompt,
                    "[PERSONA_REFERENCE]",
                    types.Part.from_bytes(
                        data=_load_image_bytes(persona_reference_path),
                        mime_type=_get_image_mime_type(persona_reference_path)
                    ),
                    "[STYLE_REFERENCE]",
                    types.Part.from_bytes(
                        data=_load_image_bytes(reference_image_path),
                        mime_type=_get_image_mime_type(reference_image_path)
                    )
                ]
            else:
                # Generating NEW persona - only use style reference (don't copy original person!)
                contents = [
                    prompt,
                    "[STYLE_REFERENCE]",
                    types.Part.from_bytes(
                        data=_load_image_bytes(reference_image_path),
                        mime_type=_get_image_mime_type(reference_image_path)
                    )
                ]

        # ===== NORMAL SINGLE-IMAGE SLIDES =====
        elif has_persona and persona_reference_path:
            # With persona - need consistency
            # Check if we should show face tape on this persona (per-slide detection)
            # RE-ENABLED: Show LumiDew patches on persona faces when shows_product_on_face=True
            # The shows_product_on_face flag is set per-slide based on analysis detection
            show_face_tape = shows_product_on_face

            # Get face tape reference path for product-on-face slides
            # IMPORTANT: Use USER'S PRODUCT IMAGE, not hardcoded reference!
            face_tape_ref_path = None
            if show_face_tape:
                # Prefer user's product image for accurate patch appearance
                if product_image_path and os.path.exists(product_image_path):
                    face_tape_ref_path = product_image_path
                    logger.info(f"FACE_TAPE: Using user's product image: {product_image_path}")
                else:
                    # Fallback to hardcoded reference only if no product image
                    face_tape_ref_path = PRODUCT_IN_USE_REFERENCES.get('face_tape')
                    logger.warning(f"FACE_TAPE: No product image, falling back to hardcoded reference")

                # Safety: if reference doesn't exist, disable face tape
                if not face_tape_ref_path or not os.path.exists(face_tape_ref_path):
                    logger.warning(f"Face tape reference not found, disabling face tape for this slide")
                    show_face_tape = False
                    face_tape_ref_path = None

            # DEBUG: Log face tape decision
            logger.info(f"FACE_TAPE_DEBUG (existing persona): slide_type={slide_type}, shows_product_on_face={shows_product_on_face}, show_face_tape={show_face_tape}, ref_path={face_tape_ref_path}")

            if show_face_tape:
                # ===== FACE TAPE SLIDE =====
                # DON'T use STYLE_REFERENCE (shows wrong person with face tape)
                # Use ONLY: PERSONA_REFERENCE (our hook persona) + FACE_TAPE_PRODUCT (hardcoded patches)
                prompt = f"""<task>Generate a TikTok {slide_label} slide with face tape.</task>

{text_style_instruction}
{visual_style_instruction}

<images>
<persona_reference>
THE PERSON TO USE (and composition reference).
Generate the EXACT SAME PERSON from this image.
</persona_reference>
<face_tape_product>
THE USER'S ACTUAL PRODUCT - match this EXACTLY when showing patches on the face.
Copy the exact: color, shape, size, texture, and any text/branding visible on the patches.
</face_tape_product>
</images>

<persona>
<identity_match>
- SAME face, hair color, skin tone, facial features
- SAME body type and general appearance
- Use similar framing and angle as reference
- DIFFERENT clothing appropriate for this scene context
</identity_match>
</persona>

<face_tape_application>
The person should be wearing face tape patches that look EXACTLY like [FACE_TAPE_PRODUCT].

<patch_design>
CRITICAL - Study [FACE_TAPE_PRODUCT] and match it PRECISELY:
- Copy the EXACT color from the product image (whatever color the patches are)
- Copy the EXACT shape from the product image (oval, rectangular, etc.)
- Copy ANY branding, text, or patterns visible on the patches
- Match the size proportionally to what's shown in the product image
- Match the texture (matte, glossy, transparent, etc.)

DO NOT:
- Invent a different design than what's shown in [FACE_TAPE_PRODUCT]
- Change the color to something not in the product image
- Simplify or omit branding/text patterns from the product
- Make patches look like generic acne patches if the product is different
</patch_design>

<placement>
Place patches naturally as skincare treatment:
- Forehead: 1 horizontal patch across forehead lines (centered)
- Under-eyes: 1-2 small patches under each eye (crow's feet area)
- The patches should look like they're WORN for skincare
- Match the casual, lifestyle aesthetic
</placement>
</face_tape_application>

{skin_realism_block}
{text_visual_match_block}
{transformation_instruction}

<scene>{scene_description}</scene>

<content>
<text>{text_content}</text>
<position>{text_position_hint}</position>
</content>

{text_placement_block}
{single_person_constraint}

{quality_constraints}"""

                # NO STYLE_REFERENCE - only persona + face tape product
                contents = [
                    prompt,
                    "[PERSONA_REFERENCE]",
                    types.Part.from_bytes(
                        data=_load_image_bytes(persona_reference_path),
                        mime_type=_get_image_mime_type(persona_reference_path)
                    ),
                    "[FACE_TAPE_PRODUCT]",
                    types.Part.from_bytes(
                        data=_load_image_bytes(face_tape_ref_path),
                        mime_type=_get_image_mime_type(face_tape_ref_path)
                    )
                ]
            else:
                # ===== NORMAL PERSONA SLIDE (no face tape) =====
                # Use ONLY PERSONA_REFERENCE (serves as both persona AND style reference)
                # The persona reference already has styled text on it from previous generation
                # NO separate style reference - prevents product mixing from original TikTok
                prompt = f"""<task>Generate a TikTok {slide_label} slide.</task>

{text_style_instruction}
{visual_style_instruction}
{variation_instruction}

<images>
<persona_reference>
THIS IS YOUR ONLY REFERENCE (serves as BOTH persona AND style).
Generate the EXACT SAME PERSON from this image. This is NON-NEGOTIABLE.
</persona_reference>
</images>

<persona>
<identity_match>
MANDATORY - the output person must be RECOGNIZABLE as the same individual:
- SAME face shape and facial structure
- SAME body type and build (if plus-size, generate plus-size; if slim, generate slim)
- SAME hair color (exact shade - red, burgundy, blonde, etc.)
- SAME skin tone
- SAME approximate age
- SAME general appearance and vibe
</identity_match>

<style_match>
- Match the text styling (font, color, effects) from reference
- Match the overall visual mood and lighting
- Match the composition style (framing, camera angle)
</style_match>

<can_change>
- Clothing (different outfit appropriate for scene)
- Hairstyle slightly (but SAME color)
- Expression
- Background (appropriate for new scene)
- Head angle and selfie position (not always centered - can be tilted, angled, off-center)
</can_change>
</persona>

<hard_constraints>
<clean_face>
- The persona's face must be CLEAN - no patches, tapes, or skincare products attached
- DO NOT add any face tape, nose strips, under-eye patches, or similar products
- Only the PRODUCT slide shows the actual product
</clean_face>
</hard_constraints>

{skin_realism_block}
{text_visual_match_block}
{transformation_instruction}

<scene>{scene_description}</scene>

<content>
<text>{text_content}</text>
<position>{text_position_hint}</position>
</content>

{text_placement_block}
{single_person_constraint}

{quality_constraints}"""

                # ONLY PERSONA_REFERENCE - no separate style reference
                # Persona reference serves as both: same person + style guide
                contents = [
                    prompt,
                    "[PERSONA_REFERENCE]",
                    types.Part.from_bytes(
                        data=_load_image_bytes(persona_reference_path),
                        mime_type=_get_image_mime_type(persona_reference_path)
                    )
                ]
        elif has_persona:
            # Has persona but NO reference yet - CREATE a new persona
            # Simple instruction: recreate a similar person, NOT the same face

            if USE_EXPANDED_PERSONAS:
                # NEW: Use expanded persona system with 100+ combinations
                # Extract cultural_context from persona_info if present
                cultural_context = persona_info.get('cultural_context') if persona_info else None
                diverse_persona = generate_diverse_persona(
                    target_audience=persona_info,
                    version=version,
                    cultural_context=cultural_context
                )
                logger.info(f"Generated diverse persona v{version}: {get_persona_summary(diverse_persona)}" +
                           (f" (cultural_context={cultural_context})" if cultural_context else ""))

                persona_demographics = f"""
<persona_instruction>
GENERATE A NEW PERSON - DO NOT COPY THE REFERENCE PERSON

{format_persona_prompt(diverse_persona)}

Match from reference ONLY: lighting mood, camera angle, setting vibe.
Generate a completely DIFFERENT person with the SPECIFIC features listed above.
</persona_instruction>"""
            else:
                # FALLBACK: Use old 5-variation system
                facial_variation = _get_facial_variation(version)

                if persona_info:
                    persona_demographics = f"""
<persona_instruction>
GENERATE A NEW PERSON - DO NOT COPY THE REFERENCE PERSON

<do_not_copy>
- DO NOT copy the person's face
- DO NOT copy the hair color or style
- DO NOT copy the clothing/outfit
</do_not_copy>

<demographics>
- Gender: {persona_info.get('gender', 'female')}
- Age Range: {persona_info.get('age_range', '20s')}
- Style: {persona_info.get('style', 'casual')}
</demographics>

<facial_features version="{version}">
- Face Shape: {facial_variation['face_shape']}
- Eye Shape: {facial_variation['eye_shape']}
- Nose: {facial_variation['nose_type']}
- Distinctive Feature: {facial_variation['distinctive_feature']}
</facial_features>

<generate>
- The SPECIFIC facial features listed above
- Hair that fits the demographic but is DIFFERENT from the reference
- Clothing that fits the scene
</generate>

Match from reference ONLY: warm lighting mood, selfie angle, indoor setting vibe.
Think "could be their friend from the same target audience" - NOT a twin, NOT a relative, NOT the same person.
</persona_instruction>"""
                else:
                    # Fallback without persona_info - still use facial variation
                    persona_demographics = f"""
<persona_instruction>
GENERATE A NEW PERSON - DO NOT COPY THE REFERENCE PERSON

<do_not_copy>
- DO NOT copy the person's face
- DO NOT copy the hair color or style
- DO NOT copy the clothing/outfit
</do_not_copy>

<facial_features version="{version}">
- Face Shape: {facial_variation['face_shape']}
- Eye Shape: {facial_variation['eye_shape']}
- Nose: {facial_variation['nose_type']}
- Distinctive Feature: {facial_variation['distinctive_feature']}
</facial_features>

Generate a completely NEW person with the SPECIFIC facial features above.
Use different hair color and style, different clothes from the reference.
Match from reference ONLY: lighting mood, camera angle, setting vibe.
</persona_instruction>"""

            # Check if we should show face tape on this new persona (per-slide detection)
            # RE-ENABLED: Show LumiDew patches on persona faces when shows_product_on_face=True
            # The shows_product_on_face flag is set per-slide based on analysis detection
            show_face_tape = shows_product_on_face

            # Get face tape reference path for product-on-face slides
            # IMPORTANT: Use USER'S PRODUCT IMAGE, not hardcoded reference!
            face_tape_ref_path = None
            if show_face_tape:
                # Prefer user's product image for accurate patch appearance
                if product_image_path and os.path.exists(product_image_path):
                    face_tape_ref_path = product_image_path
                    logger.info(f"FACE_TAPE: Using user's product image: {product_image_path}")
                else:
                    # Fallback to hardcoded reference only if no product image
                    face_tape_ref_path = PRODUCT_IN_USE_REFERENCES.get('face_tape')
                    logger.warning(f"FACE_TAPE: No product image, falling back to hardcoded reference")

                # Safety: if reference doesn't exist, disable face tape
                if not face_tape_ref_path or not os.path.exists(face_tape_ref_path):
                    logger.warning(f"Face tape reference not found, disabling face tape for this slide")
                    show_face_tape = False
                    face_tape_ref_path = None

            # DEBUG: Log face tape decision
            logger.info(f"FACE_TAPE_DEBUG: slide_type={slide_type}, shows_product_on_face={shows_product_on_face}, show_face_tape={show_face_tape}, ref_path={face_tape_ref_path}")

            # Build face tape instruction using XML format
            face_tape_instruction = ""
            if show_face_tape:
                face_tape_instruction = """
<face_tape_application>
The person should be wearing face tape patches that look EXACTLY like [FACE_TAPE_PRODUCT].

<patch_design>
CRITICAL - Study [FACE_TAPE_PRODUCT] and match it PRECISELY:
- Copy the EXACT color from the product image (whatever color the patches are)
- Copy the EXACT shape from the product image (oval, rectangular, etc.)
- Copy ANY branding, text, or patterns visible on the patches
- Match the size proportionally to what's shown in the product image
- Match the texture (matte, glossy, transparent, etc.)

DO NOT:
- Invent a different design than what's shown in [FACE_TAPE_PRODUCT]
- Change the color to something not in the product image
- Simplify or omit branding/text patterns from the product
- Make patches look like generic acne patches if the product is different
</patch_design>

<placement>
Place patches naturally as skincare treatment:
- Forehead: 1 horizontal patch across forehead lines (centered)
- Under-eyes: 1-2 small patches under each eye (crow's feet area)
- The patches should look like they're WORN for skincare
- Match the casual, lifestyle aesthetic
</placement>
</face_tape_application>"""

            prompt = f"""<task>Generate a TikTok {slide_label} slide with a NEW persona.</task>

{text_style_instruction}
{visual_style_instruction}
{variation_instruction}

<images>
<style_reference>
Reference for LIGHTING and COMPOSITION only (NOT the person).
- Same framing (close-up, medium, wide)
- Same camera angle
- Similar warm lighting mood
</style_reference>
</images>

<scene_variety>
Create a DIFFERENT scene within the SAME content category.
Match the VIBE and aesthetic, but vary the specific location/setting.
Think: different photo from the same week-long photoshoot.
</scene_variety>

{persona_demographics}
{face_tape_instruction}

{skin_realism_block}
{text_visual_match_block}
{transformation_instruction}

<scene>{scene_description}</scene>

<content>
<text>{text_content}</text>
<position>{text_position_hint}</position>
</content>

{text_placement_block}
{single_person_constraint}

{quality_constraints}"""

            contents = [
                prompt,
                "[STYLE_REFERENCE]",
                types.Part.from_bytes(
                    data=_load_image_bytes(reference_image_path),
                    mime_type=_get_image_mime_type(reference_image_path)
                )
            ]

            # Add face tape product image if we should show face tape on persona
            if show_face_tape:
                contents.extend([
                    "[FACE_TAPE_PRODUCT]",
                    types.Part.from_bytes(
                        data=_load_image_bytes(face_tape_ref_path),
                        mime_type=_get_image_mime_type(face_tape_ref_path)
                    )
                ])
        else:
            # No persona needed - just style reference
            # For body slides: detect if scene mentions a brandable product,
            # and if so, replace with a specific real brand name.
            if slide_type == 'body':
                enhanced_scene = _enhance_scene_with_real_brand(scene_description)
            else:
                enhanced_scene = scene_description

            # IMPORTANT: Detect and replace "product display" scenes
            # AI-generated product displays look fake - replace with simpler lifestyle scenes
            scene_lower = enhanced_scene.lower()
            is_product_display_scene = any(kw in scene_lower for kw in [
                'skincare bottle', 'skincare product', 'product display', 'shelfie',
                'shelves displaying', 'shelf with', 'bottles on', 'products on shelf',
                'bathroom shelf', 'skincare collection', 'beauty products', 'makeup bag'
            ])

            if is_product_display_scene:
                logger.warning(f"SCENE_SANITIZE: Replacing product display scene to avoid fake AI products")
                # Replace with simple lifestyle alternatives based on context
                if 'bathroom' in scene_lower or 'spa' in scene_lower:
                    enhanced_scene = "Cozy bathroom corner with soft natural lighting, fluffy white towel, and a single candle. COMPOSITION: framing=medium, angle=straight, position=center, background=bathroom"
                elif 'bedroom' in scene_lower:
                    enhanced_scene = "Cozy bedside scene with soft lamp lighting, book on nightstand, and warm blanket texture. COMPOSITION: framing=medium, angle=above, position=center, background=bedroom"
                elif 'makeup' in scene_lower or 'beauty' in scene_lower:
                    enhanced_scene = "Minimal vanity mirror with soft warm lighting and clean aesthetic. COMPOSITION: framing=medium, angle=straight, position=center, background=soft-focus"
                else:
                    enhanced_scene = "Cozy lifestyle scene with soft natural lighting and warm aesthetic vibe. COMPOSITION: framing=medium, angle=straight, position=center, background=lifestyle"

            # Each photo variation now has its own unique scene from analysis
            # Just generate the exact scene described
            scene_instruction = f"""
<scene>{enhanced_scene}</scene>

<scene_constraint>
Generate EXACTLY what the scene description says - nothing more, nothing less.
- If scene says "glass of water on kitchen counter" → show ONLY water glass on kitchen counter
- If scene says "journal and pen on bed" → show ONLY journal and pen on bed
- DO NOT add random skincare products, bottles, or items not mentioned
- Each slide should feature ONE MAIN ITEM that matches the tip being given
</scene_constraint>
"""

            prompt = f"""<task>Generate a TikTok {slide_label} lifestyle slide (no persona).</task>

{text_style_instruction}
{visual_style_instruction}
{variation_instruction}

<images>
<style_reference>
Reference slide for visual composition, mood, and content type.
Match: framing type, camera angle, subject position, lighting mood, color grading.
</style_reference>
</images>

<scene_variety>
Create a DIFFERENT scene within the SAME content category.
Think like a content creator with 200 photos from a week-long photoshoot:
- ALL photos share the same vibe and aesthetic
- But each shows a DIFFERENT moment, location, or setting
Match the CATEGORY but vary the SPECIFIC location and setting details.
</scene_variety>

{scene_instruction}

<content>
<text>{text_content}</text>
<position>{text_position_hint}</position>
</content>

{text_placement_block}

<body_rules>
- PREFER showing objects/products instead of human body parts
- If scene REQUIRES body parts (legs, arms, hands), show FULL BODY or full upper/lower half
- NEVER crop to show ONLY isolated limbs without body context
</body_rules>

<layout_constraint>
Generate a SINGLE lifestyle photo even if text compares things:
- NO star ratings, review scores, or rating graphics
- NO side-by-side comparisons or split screens
- NO grids, collages, or multi-panel layouts
- NO before/after visual comparisons
- If text says "X is bad, Y is good" - show ONLY the good option
</layout_constraint>

<authenticity>
Generate an AUTHENTIC LIFESTYLE SCENE - NOT a stock photo!
Think "real person's messy-but-aesthetic life" not "studio product shot".

<style>
- Real rooms with lived-in details (not perfectly staged)
- Natural window lighting with soft shadows (NOT studio lights)
- Slightly messy/casual vibes (a book left open, cozy blanket)
- Warm, inviting atmosphere
</style>

<good_examples>
- Cozy sofa with morning sunlight, coffee on side table, curtains blowing
- Kitchen counter with half-eaten breakfast, morning light, real dishes
- Bathroom vanity with various products scattered naturally, towel draped
- Cozy corner with blanket, book spine-down, warm lamp light
- Desk with actual work clutter, plant, warm afternoon light through window
</good_examples>

<bad_examples>
- Close-up of a product on white/marble surface
- Perfectly arranged "flat lay" product shots
- Studio-lit product photography with no context
- Generic stock photo aesthetics (too clean, too posed)
- Marble countertop with perfectly placed items
- Steam rising from cups/mugs (obvious AI giveaway)
- Visible humidifier mist or candle smoke (looks fake)
</bad_examples>

<requirement>
The image should feel like you peeked into someone's real life.
If it looks like a stock photo or Amazon listing, it will be REJECTED.
</requirement>
</authenticity>

{quality_constraints}"""

            contents = [
                prompt,
                "[STYLE_REFERENCE]",
                types.Part.from_bytes(
                    data=_load_image_bytes(reference_image_path),
                    mime_type=_get_image_mime_type(reference_image_path)
                )
            ]

            # If scene mentions skincare/product displays, include user's product image as reference
            scene_lower = enhanced_scene.lower()
            scene_mentions_products = any(kw in scene_lower for kw in [
                'skincare', 'product', 'bottle', 'serum', 'cream', 'lotion', 'patch',
                'face tape', 'beauty', 'makeup', 'cosmetic', 'shelf display', 'shelfie'
            ])

            if scene_mentions_products and product_image_path and os.path.exists(product_image_path):
                logger.info(f"PRODUCT_REF: Scene mentions products, including user's product image as reference")
                # Add product image reference to prompt - append to existing prompt
                product_instruction = """

<product_reference>
[PRODUCT_IMAGE] shows the USER'S ACTUAL PRODUCT.
If showing ANY skincare products in this scene, they MUST match this product's appearance:
- Same packaging color/design as [PRODUCT_IMAGE]
- Same brand style and aesthetic
- Same product type/format (patches, bottles, etc.)
DO NOT generate random or different skincare products - use THIS product only.
If [PRODUCT_IMAGE] shows face patches, any patches in scene must look IDENTICAL.
</product_reference>
"""
                # Append product instruction to prompt
                prompt = prompt + product_instruction
                contents[0] = prompt

                # Add product image to contents
                contents.extend([
                    "[PRODUCT_IMAGE]",
                    types.Part.from_bytes(
                        data=_load_image_bytes(product_image_path),
                        mime_type=_get_image_mime_type(product_image_path)
                    )
                ])

    # Retry logic with validation and safety fallback
    last_error = None
    sanitization_level = 0  # 0=none, 1=normal, 2=aggressive
    current_scene = scene_description  # Track current scene description
    current_contents = contents  # Track current prompt contents
    original_scene = scene_description  # Keep original for reference

    for attempt in range(MAX_RETRIES):
        try:
            response = client.models.generate_content(
                model=IMAGE_MODEL,
                contents=current_contents,
                config=types.GenerateContentConfig(
                    response_modalities=['image', 'text'],
                    image_config=types.ImageConfig(
                        aspect_ratio="3:4",
                        image_size="2K"  # 2048px resolution
                    ),
                    safety_settings=SAFETY_SETTINGS  # Allow benign lifestyle content
                )
            )

            # Extract generated image - check for safety block
            if not response.parts:
                # Safety block detected - try escalating sanitization
                if sanitization_level == 0:
                    # First try: normal sanitization
                    sanitized_scene, was_modified = _sanitize_scene_description(current_scene)
                    if was_modified:
                        logger.warning(f"Safety block detected, retrying with sanitized scene: '{current_scene[:50]}...' -> '{sanitized_scene[:50]}...'")
                        sanitization_level = 1
                        current_scene = sanitized_scene
                        # Rebuild prompt with sanitized scene
                        current_contents = [c if not isinstance(c, str)
                                           else c.replace(original_scene, sanitized_scene)
                                           for c in current_contents]
                        continue  # Retry with sanitized prompt (don't count as attempt)
                elif sanitization_level == 1:
                    # Second try: aggressive sanitization
                    sanitized_scene, was_modified = _sanitize_scene_description(current_scene, aggressive=True)
                    if was_modified:
                        logger.warning(f"Normal sanitization failed, trying aggressive: '{current_scene[:50]}...' -> '{sanitized_scene[:50]}...'")
                        sanitization_level = 2
                        prev_scene = current_scene
                        current_scene = sanitized_scene
                        # Rebuild prompt with aggressively sanitized scene
                        current_contents = [c if not isinstance(c, str)
                                           else c.replace(prev_scene, sanitized_scene)
                                           for c in current_contents]
                        continue  # Retry with aggressive sanitization (don't count as attempt)

                raise GeminiServiceError('Empty response from Gemini - content may have been blocked')
            for part in response.parts:
                if hasattr(part, 'inline_data') and part.inline_data:
                    # Save raw data to temp file first
                    temp_path = output_path + '.tmp'
                    with open(temp_path, 'wb') as f:
                        f.write(part.inline_data.data)

                    # Convert to JPEG format
                    try:
                        with Image.open(temp_path) as img:
                            # Convert to RGB if necessary (for JPEG)
                            if img.mode in ('RGBA', 'P'):
                                img = img.convert('RGB')
                            img.save(output_path, 'JPEG', quality=95)
                        os.remove(temp_path)
                    except Exception as conv_err:
                        logger.warning(f"JPEG conversion failed, keeping original: {conv_err}")
                        os.rename(temp_path, output_path)

                    # Validate image structure
                    is_valid, issues = _validate_image_structure(output_path, expected_ratio="3:4")
                    if is_valid:
                        # Record successful API usage for image model
                        _record_api_usage(api_key, success=True, model_type='image')
                        return output_path
                    else:
                        # Validation failed - log and retry
                        logger.warning(f"Image validation failed (attempt {attempt + 1}): {issues}")
                        last_error = GeminiServiceError(f"Validation failed: {issues}")
                        if attempt < MAX_RETRIES - 1:
                            wait_time = (2 ** attempt) + 1
                            time.sleep(wait_time)
                        continue  # Retry generation

            raise GeminiServiceError('No image in response')

        except Exception as e:
            last_error = e
            error_str = str(e).lower()

            # Check for safety-related errors and try sanitizing
            safety_indicators = ['safety', 'blocked', 'harmful', 'policy', 'content filter']
            is_safety_error = any(indicator in error_str for indicator in safety_indicators)

            if is_safety_error and sanitization_level < 2:
                if sanitization_level == 0:
                    # First try: normal sanitization
                    sanitized_scene, was_modified = _sanitize_scene_description(current_scene)
                    if was_modified:
                        logger.warning(f"Safety error detected, retrying with sanitized scene: {str(e)[:100]}")
                        sanitization_level = 1
                        current_scene = sanitized_scene
                        current_contents = [c if not isinstance(c, str)
                                           else c.replace(original_scene, sanitized_scene)
                                           for c in current_contents]
                        time.sleep(2)  # Brief pause before retry
                        continue  # Retry with sanitized prompt
                elif sanitization_level == 1:
                    # Second try: aggressive sanitization
                    sanitized_scene, was_modified = _sanitize_scene_description(current_scene, aggressive=True)
                    if was_modified:
                        logger.warning(f"Normal sanitization failed, trying aggressive: {str(e)[:100]}")
                        sanitization_level = 2
                        prev_scene = current_scene
                        current_scene = sanitized_scene
                        current_contents = [c if not isinstance(c, str)
                                           else c.replace(prev_scene, sanitized_scene)
                                           for c in current_contents]
                        time.sleep(2)  # Brief pause before retry
                        continue  # Retry with aggressive sanitization

            # Check for rate limit error — re-raise immediately for outer loop to handle key rotation
            if '429' in str(e) or 'RESOURCE_EXHAUSTED' in str(e):
                # Don't retry here — let queue_processor._generate_image() rotate to next key
                # Inner retry loop should only handle safety blocks and validation retries
                raise
            else:
                wait_time = (2 ** attempt) + 1  # Normal exponential backoff

            if attempt < MAX_RETRIES - 1:
                time.sleep(wait_time)

    raise GeminiServiceError(f'Failed after {MAX_RETRIES} retries: {last_error}')


# Type alias for progress callbacks
# ImageProgressCallback: (current: int, total: int, message: str) -> None
ImageProgressCallback = Callable[[int, int, str], None]

# PipelineProgressCallback: (status: str, message: str, percent: int) -> None
PipelineProgressCallback = Callable[[str, str, int], None]


def generate_all_images(
    analysis: dict,
    slide_paths: list[str],
    product_image_paths: list[str],
    output_dir: str,
    progress_callback: Optional[ImageProgressCallback] = None,
    hook_photo_var: int = 1,
    body_photo_var: int = 1,
    request_id: str = None,
    clean_image_mode: bool = False,
    product_description: str = ""
) -> dict:
    """
    Generate all images with persona consistency and photo × text variations.

    Strategy:
    1. Generate first persona variation FIRST (creates the persona)
    2. Use that generated image as PERSONA_REFERENCE for all other persona slides/variations
    3. Run all remaining variations in parallel (photo × text matrix)

    Args:
        analysis: Output from analyze_and_plan() - includes text_variations per slide
        slide_paths: List of original slide image paths
        product_image_paths: List of user's product images (each = one photo variation)
        output_dir: Directory to save generated images
        progress_callback: Optional callback with signature (current, total, message)
        hook_photo_var: Number of photo variations for hook slide (default 1)
        body_photo_var: Number of photo variations per body slide (default 1)
        request_id: Optional request ID for logging
        clean_image_mode: If True, generate images WITHOUT text (for PIL rendering)

    Photo × Text Matrix:
        - Hook: hook_photo_var × len(text_variations) images
        - Body: body_photo_var × len(text_variations) images per body slide
        - Product: len(product_image_paths) × len(text_variations) images

    Returns:
        dict with:
            - images: flat list of all generated image paths
            - variations: structured dict by slide type
    """
    log = get_request_logger('gemini', request_id) if request_id else logger
    start_time = time.time()

    client, api_key = _get_client()
    os.makedirs(output_dir, exist_ok=True)

    new_slides = analysis['new_slides']
    text_style = analysis.get('text_style', None)  # Extract text style from analysis
    visual_style = analysis.get('visual_style', None)  # Extract visual style from analysis
    persona_info = analysis.get('persona', None)  # Extract persona demographics from analysis

    # Get product-in-use reference if AI flagged this product should be shown on face
    product_on_face_config = analysis.get('product_on_face', {})
    product_in_use_reference = _get_product_in_use_reference(product_on_face_config)
    if product_in_use_reference:
        log.info(f"Product-on-face detected: will show face tape on personas")

    # Build all tasks with photo × text variations
    all_tasks = []
    variations_structure = {}  # Track variations by slide key

    # Find the best style reference index (first hook or body with persona) for product slides
    def get_best_style_reference():
        """Find the best slide to use as style reference (hook or first body with persona)"""
        for s in new_slides:
            if s.get('slide_type') == 'hook':
                return s.get('reference_image_index', 0)
            if s.get('slide_type') == 'body' and s.get('has_persona', False):
                return s.get('reference_image_index', 0)
        return 0  # Fallback to first slide

    best_style_ref = get_best_style_reference()

    for slide in new_slides:
        idx = slide['slide_index']
        ref_idx = slide.get('reference_image_index') if slide.get('reference_image_index') is not None else idx
        slide_type = slide['slide_type']
        has_persona = slide.get('has_persona', False)

        # Skip CTA slides - don't generate or upload
        if slide_type == 'cta':
            continue

        # For product slides, use the best style reference (hook/body) instead of potentially using a text-only slide
        if slide_type == 'product':
            ref_idx = best_style_ref

        # NEW: Get scene_variations from analysis (each scene_variation = different tip/concept)
        scene_variations = slide.get('scene_variations', [])

        # FALLBACK: Support old format with new_scene_description + text_variations
        if not scene_variations:
            old_scene = slide.get('new_scene_description', '')
            old_texts = slide.get('text_variations', [])
            if not old_texts:
                old_text = slide.get('text_content', '')
                old_texts = [old_text] if old_text else ['']
            scene_variations = [{
                'scene_description': old_scene,
                'text_variations': old_texts
            }]

        # Determine photo variations and slide key
        if slide_type == 'hook':
            expected_photo_vars = hook_photo_var
            slide_key = 'hook'
        elif slide_type == 'product':
            # Product photo variations must match hook/body to ensure all slideshows have products
            # Use max of hook_photo_var and body_photo_var to cover all slideshow variations
            expected_photo_vars = max(hook_photo_var, body_photo_var)
            slide_key = 'product'
        else:  # body
            expected_photo_vars = body_photo_var
            body_num = sum(1 for s in new_slides[:idx] if s['slide_type'] == 'body') + 1
            slide_key = f'body_{body_num}'

        # Initialize variations list for this slide
        if slide_key not in variations_structure:
            variations_structure[slide_key] = []

        # Create photo × text matrix of tasks
        # Each scene_variation = one photo variation with its own scene and texts
        for p_idx in range(expected_photo_vars):
            # Get the scene_variation for this photo variation
            # If fewer scene_variations than expected, reuse the last one
            scene_var_idx = min(p_idx, len(scene_variations) - 1)
            scene_var = scene_variations[scene_var_idx] if scene_variations else {'scene_description': '', 'text_variations': ['']}

            scene_description = scene_var.get('scene_description', '')
            text_variations = scene_var.get('text_variations', [''])

            # Physical consistency validation (coat in water, etc.)
            if USE_PHYSICAL_CONSISTENCY and scene_description:
                is_valid, enhanced_scene = validate_scene_consistency(
                    scene_description,
                    slide_info=f"{slide_key}_p{p_idx+1}"
                )
                if not is_valid:
                    logger.warning(f"Scene validation issue for {slide_key}: using enhanced scene with attire instructions")
                scene_description = enhanced_scene

            for t_idx, text_content in enumerate(text_variations):
                photo_ver = p_idx + 1  # 1-indexed
                text_ver = t_idx + 1   # 1-indexed

                # Determine output filename with slide_index prefix for correct video ordering
                output_path = os.path.join(output_dir, f'{idx:02d}_{slide_key}_p{photo_ver}_t{text_ver}.jpg')

                # For product slides, use the p_idx-th uploaded image
                product_img = None
                if slide_type == 'product' and product_image_paths:
                    product_img = product_image_paths[p_idx] if p_idx < len(product_image_paths) else product_image_paths[0]

                task = {
                    'task_id': f'{idx:02d}_{slide_key}_p{photo_ver}_t{text_ver}',
                    'slide_index': idx,
                    'slide_type': slide_type,
                    'slide_key': slide_key,
                    'photo_version': photo_ver,
                    'text_version': text_ver,
                    'version': photo_ver,  # For variation instruction in prompt
                    'reference_image_path': slide_paths[ref_idx] if ref_idx < len(slide_paths) else slide_paths[0],
                    'scene_description': scene_description,
                    'text_content': text_content,
                    'text_position_hint': slide.get('text_position_hint', ''),
                    'output_path': output_path,
                    'product_image_path': product_img,
                    'has_persona': has_persona,
                    'shows_product_on_face': slide.get('shows_product_on_face', False),  # Per-slide face tape detection
                    'transformation_role': slide.get('transformation_role'),  # "before", "after", or None
                    'transformation_problem': slide.get('transformation_problem'),  # "under_eye", "forehead_lines", etc.
                    'layout_type': slide.get('layout_type', 'single'),  # "single" or "split_screen"
                    'split_config': slide.get('split_config')  # Split-screen configuration
                }
                all_tasks.append(task)

    # In clean_image_mode, product slides should use original image (no Gemini generation)
    # Just copy the user's product image and let PIL add text overlay later
    copy_only_tasks = []
    generate_tasks = []

    if clean_image_mode:
        for task in all_tasks:
            if task['slide_type'] == 'product' and task['product_image_path']:
                copy_only_tasks.append(task)
            else:
                generate_tasks.append(task)
        log.info(f"Clean image mode: {len(copy_only_tasks)} product slides will use original images")
    else:
        generate_tasks = all_tasks

    # Process copy-only tasks (product slides in clean_image_mode)
    # Track results from copy-only tasks for final output
    copy_only_results = {}
    import shutil
    from PIL import Image as PILImage
    for task in copy_only_tasks:
        try:
            # Copy and convert to PNG at proper resolution
            src_path = task['product_image_path']
            dst_path = task['output_path']

            # Open, resize if needed (maintain 3:4 aspect ratio), and save as PNG
            with PILImage.open(src_path) as img:
                # Target: 3:4 aspect ratio, minimum 1080x1440
                target_w, target_h = 1080, 1440

                # Resize to fit while maintaining aspect ratio
                img_ratio = img.width / img.height
                target_ratio = target_w / target_h

                if img_ratio > target_ratio:
                    # Image is wider - fit to height, crop width
                    new_h = target_h
                    new_w = int(new_h * img_ratio)
                else:
                    # Image is taller - fit to width, crop height
                    new_w = target_w
                    new_h = int(new_w / img_ratio)

                # Resize
                img_resized = img.resize((new_w, new_h), PILImage.Resampling.LANCZOS)

                # Center crop to target dimensions
                left = (new_w - target_w) // 2
                top = (new_h - target_h) // 2
                img_cropped = img_resized.crop((left, top, left + target_w, top + target_h))

                # Convert to RGB if necessary and save
                if img_cropped.mode in ('RGBA', 'P'):
                    img_cropped = img_cropped.convert('RGB')
                img_cropped.save(dst_path, 'JPEG', quality=95)

            copy_only_results[task['task_id']] = dst_path
            log.debug(f"Copied product image: {os.path.basename(src_path)} -> {os.path.basename(dst_path)}")
        except Exception as e:
            log.error(f"Failed to copy product image {task['task_id']}: {e}")
            # Fallback: just copy the file as-is
            shutil.copy2(task['product_image_path'], task['output_path'])
            copy_only_results[task['task_id']] = task['output_path']

    total = len(generate_tasks)

    # Separate persona tasks from non-persona tasks
    persona_tasks = [t for t in generate_tasks if t['has_persona']]
    non_persona_tasks = [t for t in generate_tasks if not t['has_persona']]

    log.info(f"Generation tasks: {total} total ({len(persona_tasks)} persona, {len(non_persona_tasks)} non-persona)")

    # Use global rate limiter (shared across all jobs)
    rate_limiter = get_rate_limiter()

    results = {}  # task_id -> output_path
    errors = []
    completed = 0

    def generate_task(task, persona_ref_path=None):
        """Generate single image with rate limiting."""
        try:
            rate_limiter.acquire()
            try:
                return task['task_id'], _generate_single_image(
                    client,
                    api_key,
                    task['slide_type'],
                    task['scene_description'],
                    task['text_content'],
                    task['text_position_hint'],
                    task['output_path'],
                    task['reference_image_path'],
                    task['product_image_path'],
                    persona_ref_path,
                    task['has_persona'],
                    text_style,  # Pass text style from analysis
                    visual_style,  # Pass visual style from analysis
                    persona_info,  # Pass persona demographics for new persona creation
                    task['version'],  # Pass version for variation diversity
                    clean_image_mode,  # Generate without text for PIL rendering
                    product_description,  # For real product grounding in scenes
                    task.get('shows_product_on_face', False),  # Per-slide face tape flag
                    task.get('transformation_role'),  # "before", "after", or None for transformation slides
                    task.get('transformation_problem'),  # "under_eye", "forehead_lines", etc. for targeted visuals
                    task.get('layout_type', 'single'),  # "single" or "split_screen"
                    task.get('split_config')  # Split-screen configuration
                )
            finally:
                rate_limiter.release()
        except GeminiServiceError as e:
            return task['task_id'], e
        except Exception as e:
            return task['task_id'], GeminiServiceError(f'Unexpected error: {e}')

    # STEP 1: Generate FIRST persona variation sequentially (creates the persona)
    generated_persona_path = None
    if persona_tasks:
        first_persona_task = persona_tasks[0]
        remaining_persona_tasks = persona_tasks[1:]

        if progress_callback:
            progress_callback(0, total, 'Creating persona (first variation)...')

        # Generate first persona - no reference, creates NEW persona
        task_id, result = generate_task(first_persona_task, persona_ref_path=None)
        completed += 1

        if isinstance(result, Exception):
            errors.append((task_id, result))
        else:
            results[task_id] = result
            generated_persona_path = result  # Use THIS for all other persona variations

        if progress_callback:
            progress_callback(completed, total, f'Persona created! Generating {total - 1} more...')
    else:
        remaining_persona_tasks = []

    # STEP 2: Generate all remaining in parallel
    remaining_tasks = []

    for task in remaining_persona_tasks:
        remaining_tasks.append((task, generated_persona_path))

    for task in non_persona_tasks:
        remaining_tasks.append((task, None))

    if remaining_tasks:
        with ThreadPoolExecutor(max_workers=MAX_CONCURRENT) as executor:
            futures = {
                executor.submit(generate_task, task, persona_ref): task
                for task, persona_ref in remaining_tasks
            }

            for future in as_completed(futures):
                task_id, result = future.result()
                completed += 1

                if isinstance(result, Exception):
                    errors.append((task_id, result))
                else:
                    results[task_id] = result

                if progress_callback:
                    progress_callback(completed, total, f'Generated {completed}/{total} variations')

    # Check for errors
    if errors:
        log.error(f"Generation failed: {len(errors)} errors")
        for task_id, err in errors:
            log.error(f"  {task_id}: {err}")
        error_msgs = [f"{task_id}: {err}" for task_id, err in errors]
        raise GeminiServiceError(f"Image generation failed:\n" + "\n".join(error_msgs))

    # Log rate limiter stats
    stats = rate_limiter.get_stats()
    log.info(f"Rate limiter stats: {stats['requests_made']} requests in {stats['elapsed_seconds']:.1f}s, actual RPM: {stats['actual_rpm']:.1f}")

    # Merge copy_only_results into results
    results.update(copy_only_results)

    # Build variations structure with actual paths
    for task in all_tasks:
        task_id = task['task_id']
        slide_key = task['slide_key']
        if task_id in results:
            variations_structure[slide_key].append(results[task_id])

    # Build flat list (all images in task order)
    all_images = [results[t['task_id']] for t in all_tasks if t['task_id'] in results]

    elapsed = time.time() - start_time
    log.info(f"All images generated in {elapsed:.1f}s: {len(all_images)} images")

    return {
        'images': all_images,
        'variations': variations_structure
    }


def run_pipeline(
    slide_paths: list[str],
    product_image_paths: list[str],
    product_description: str,
    output_dir: str,
    progress_callback: Optional[PipelineProgressCallback] = None,
    hook_photo_var: int = 1,
    hook_text_var: int = 1,
    body_photo_var: int = 1,
    body_text_var: int = 1,
    product_text_var: int = 1,
    request_id: str = None,
    preset_id: str = 'gemini'
) -> dict:
    """
    Run the complete generation pipeline with photo × text variations.

    Args:
        slide_paths: List of paths to scraped TikTok slide images
        product_image_paths: List of user's product images (multiple for photo variations)
        product_description: Text description of the product
        output_dir: Directory to save analysis and generated images
        progress_callback: Optional callback with signature (status, message, percent)
            - status: Current phase ('analyzing' | 'generating')
            - message: Human-readable progress message
            - percent: Progress percentage (0-100)
        hook_photo_var: Number of photo variations for hook slide
        hook_text_var: Number of text variations for hook slide
        body_photo_var: Number of photo variations per body slide
        body_text_var: Number of text variations per body slide
        product_text_var: Number of text variations for product slide
        request_id: Optional request ID for logging
        preset_id: Text preset ID ('gemini' for AI text, or preset like 'classic_shadow')

    Photo × Text Matrix:
        - Hook: hook_photo_var × hook_text_var images
        - Body: body_photo_var × body_text_var images per slide
        - Product: len(product_image_paths) × product_text_var images

    Returns:
        dict with keys:
            - analysis: Full analysis JSON from Gemini
            - generated_images: List of generated image paths (flat)
            - variations: Structured dict of variations by slide type
            - analysis_path: Path to saved analysis.json

    Steps:
        1. Analyze slideshow with text variations generation
        2. Generate all images with photo × text matrix
    """
    log = get_request_logger('gemini', request_id) if request_id else logger
    start_time = time.time()

    # Estimate total images (photo × text for each slide type)
    hook_total = hook_photo_var * hook_text_var
    body_count = max(1, len(slide_paths) - 2)  # Estimate body slides
    body_total = body_count * body_photo_var * body_text_var
    # Product photo vars must match hook/body to ensure all slideshows have products
    product_photo_var = max(hook_photo_var, body_photo_var)
    product_total = product_photo_var * product_text_var
    total_estimate = hook_total + body_total + product_total

    log.info(f"Starting pipeline: {len(slide_paths)} slides, ~{total_estimate} total images, preset={preset_id}")
    log.debug(f"Photo vars: hook={hook_photo_var}, body={body_photo_var}, product={product_photo_var}")
    log.debug(f"Text vars: hook={hook_text_var}, body={body_text_var}, product={product_text_var}")

    # Determine if we need clean images (no text) for manual preset
    clean_image_mode = preset_id != 'gemini'
    if clean_image_mode:
        log.info(f"Clean image mode enabled - text will be rendered by PIL with preset '{preset_id}'")

    if progress_callback:
        progress_callback('analyzing', 'Analyzing slideshow and planning new story...', 30)

    # Pre-extract brand from description for validation
    pre_extraction = _extract_brand_from_description(product_description)
    log.debug(f"Pre-extracted brand candidates: {pre_extraction}")

    # Step 1: Analyze and plan (with photo and text variation counts)
    log.info("Step 1/2: Analyzing slideshow")
    analysis = analyze_and_plan(
        slide_paths,
        product_image_paths,
        product_description,
        output_dir,
        hook_photo_var=hook_photo_var,
        hook_text_var=hook_text_var,
        body_photo_var=body_photo_var,
        body_text_var=body_text_var,
        product_text_var=product_text_var,
        request_id=request_id
    )

    # Validate brand is not hallucinated
    brand_valid, corrected_brand = _validate_brand_not_hallucinated(
        analysis,
        product_description,
        pre_extraction.get('likely_brand')
    )
    if not brand_valid:
        # Correct the hallucinated brand in analysis
        log.warning(f"Correcting hallucinated brand to: '{corrected_brand}'")
        if 'required_keywords' in analysis:
            analysis['required_keywords']['brand_name'] = corrected_brand
            analysis['required_keywords']['brand_corrected'] = True

    # Validate and inject required keywords (brand name, purchase location)
    is_valid, keyword_issues = _validate_required_keywords(analysis)
    if not is_valid:
        log.warning(f"Keyword validation failed: {keyword_issues}. Injecting missing keywords...")
        analysis = _inject_missing_keywords(analysis)

    # Re-save analysis with any corrections
    if not brand_valid or not is_valid:
        analysis_path = os.path.join(output_dir, 'analysis.json')
        with open(analysis_path, 'w') as f:
            json.dump(analysis, f, indent=2)

    if progress_callback:
        progress_callback('generating', 'Generating images...', 40)

    # Step 2: Generate all images with variations
    log.info("Step 2/2: Generating images")

    def image_progress(current, total, message):
        if progress_callback:
            percent = 40 + int(50 * current / total)
            progress_callback('generating', message, percent)
        log.debug(f"Generation progress: {current}/{total}")

    generation_result = generate_all_images(
        analysis,
        slide_paths,
        product_image_paths,
        output_dir,
        progress_callback=image_progress,
        hook_photo_var=hook_photo_var,
        body_photo_var=body_photo_var,
        request_id=request_id,
        clean_image_mode=clean_image_mode,
        product_description=product_description
    )

    # Step 3: If clean_image_mode, add text via safe zone detection + PIL rendering
    if clean_image_mode and generation_result['images']:
        if progress_callback:
            progress_callback('rendering', 'Adding text overlays...', 92)

        log.info(f"Step 3: Rendering text on {len(generation_result['images'])} images with preset '{preset_id}'")

        # Build a mapping of task_id to text_content from analysis
        text_mapping = {}
        for slide in analysis.get('new_slides', []):
            idx = slide['slide_index']
            slide_type = slide['slide_type']
            text_variations = slide.get('text_variations', [slide.get('text_content', '')])

            if slide_type == 'hook':
                slide_key = 'hook'
            elif slide_type == 'product':
                slide_key = 'product'
            elif slide_type == 'cta':
                continue  # Skip CTA slides
            else:
                body_num = sum(1 for s in analysis['new_slides'][:idx] if s['slide_type'] == 'body') + 1
                slide_key = f'body_{body_num}'

            text_mapping[slide_key] = text_variations

        # Process each generated image
        rendered_images = []
        for img_path in generation_result['images']:
            try:
                # Parse task_id from filename (e.g., 00_hook_p1_t1.jpg or 01_body_1_p1_t1.jpg)
                filename = os.path.basename(img_path)
                parts = filename.replace('.jpg', '').replace('.png', '').split('_')

                # Extract slide key - parts[0] is slide_index prefix, parts[1] is slide type
                # New format: 00_hook_p1_t1, 01_body_1_p1_t1, 03_product_p1_t1
                if len(parts) >= 2 and parts[0].isdigit():
                    # New format with slide_index prefix
                    if parts[1] in ['hook', 'product']:
                        slide_key = parts[1]
                    else:
                        slide_key = f"{parts[1]}_{parts[2]}"
                else:
                    # Legacy format without prefix: hook_p1_t1, body_1_p1_t1
                    if parts[0] in ['hook', 'product']:
                        slide_key = parts[0]
                    else:
                        slide_key = f"{parts[0]}_{parts[1]}"

                # Get text index from t{n} part
                text_idx = 0
                for part in parts:
                    if part.startswith('t'):
                        text_idx = int(part[1:]) - 1  # Convert to 0-indexed
                        break

                # Get text content
                texts = text_mapping.get(slide_key, [''])
                text_content = texts[text_idx] if text_idx < len(texts) else texts[0] if texts else ''

                if not text_content:
                    log.warning(f"No text content for {filename}, skipping text render")
                    rendered_images.append(img_path)
                    continue

                # Detect safe zones
                safe_zone_result = detect_safe_zones(img_path)

                if not safe_zone_result.safe_zones:
                    log.warning(f"No safe zones found for {filename}, skipping text render")
                    rendered_images.append(img_path)
                    continue

                # Use recommended zone (highest confidence)
                zone = safe_zone_result.safe_zones[safe_zone_result.recommended_zone or 0]

                # Render text on image
                render_text(
                    image_path=img_path,
                    text=text_content,
                    zone=zone,
                    preset_id=preset_id,
                    output_path=img_path  # Overwrite the clean image
                )

                rendered_images.append(img_path)
                log.debug(f"Rendered text on {filename}: '{text_content[:30]}...'")

            except Exception as e:
                log.error(f"Failed to render text on {img_path}: {e}")
                rendered_images.append(img_path)  # Keep original

        generation_result['images'] = rendered_images

    elapsed = time.time() - start_time
    log.info(f"Pipeline complete in {elapsed:.1f}s: {len(generation_result['images'])} images generated")

    return {
        'analysis': analysis,
        'generated_images': generation_result['images'],
        'variations': generation_result['variations'],
        'analysis_path': os.path.join(output_dir, 'analysis.json')
    }


# ============================================================================
# QUEUE-BASED GENERATION (Global Queue System)
# ============================================================================

def submit_to_queue(
    analysis: dict,
    slide_paths: list[str],
    product_image_paths: list[str],
    output_dir: str,
    job_id: str,
    hook_photo_var: int = 1,
    body_photo_var: int = 1,
    request_id: str = None,
    clean_image_mode: bool = False,
    product_description: str = ''
) -> int:
    """
    Submit image generation tasks to the global queue.

    This function builds tasks from the analysis and submits them to the
    Redis-backed global queue for batch processing.

    Args:
        analysis: Analysis dict from analyze_and_plan
        slide_paths: Paths to scraped slides
        product_image_paths: User's product images
        output_dir: Output directory for generated images
        job_id: Unique job identifier
        hook_photo_var: Number of photo variations for hook
        body_photo_var: Number of photo variations per body
        request_id: Optional logging identifier
        clean_image_mode: If True, generate without text
        product_description: Product description for grounding

    Returns:
        Number of tasks submitted
    """
    from image_queue import ImageTask, get_global_queue

    log = get_request_logger('gemini', request_id) if request_id else logger
    queue = get_global_queue()

    os.makedirs(output_dir, exist_ok=True)

    new_slides = analysis['new_slides']
    text_style = analysis.get('text_style', {})
    visual_style = analysis.get('visual_style', {})
    persona_info = analysis.get('persona', {})  # Demographics for new persona creation

    # Find best style reference (first hook or body with persona) for product slides
    best_style_ref = 0
    for s in new_slides:
        if s.get('slide_type') == 'hook':
            best_style_ref = s.get('reference_image_index', 0)
            break
        if s.get('slide_type') == 'body' and s.get('has_persona', False):
            best_style_ref = s.get('reference_image_index', 0)
            break

    # Get product-in-use reference if AI flagged this product should be shown on face
    product_on_face_config = analysis.get('product_on_face', {})
    product_in_use_reference = _get_product_in_use_reference(product_on_face_config) or ''
    if product_in_use_reference:
        log.info(f"Product-on-face detected: will show face tape on personas")

    tasks_submitted = 0

    # Track persona dependency group
    persona_group = f"{job_id}_persona"
    first_persona_task_id = None

    for slide in new_slides:
        idx = slide['slide_index']
        ref_idx = slide.get('reference_image_index') if slide.get('reference_image_index') is not None else idx
        slide_type = slide['slide_type']
        has_persona = slide.get('has_persona', False)
        shows_product_on_face = slide.get('shows_product_on_face', False)  # Per-slide face tape

        # For product slides, use best style reference (hook/body) instead of potentially text-only slide
        if slide_type == 'product':
            ref_idx = best_style_ref

        # Skip CTA slides
        if slide_type == 'cta':
            continue

        # Get scene_variations from analysis
        scene_variations = slide.get('scene_variations', [])

        # Fallback for old format
        if not scene_variations:
            old_scene = slide.get('new_scene_description', '')
            old_texts = slide.get('text_variations', [])
            if not old_texts:
                old_text = slide.get('text_content', '')
                old_texts = [old_text] if old_text else ['']
            scene_variations = [{
                'scene_description': old_scene,
                'text_variations': old_texts
            }]

        # Determine photo variations and slide key
        if slide_type == 'hook':
            expected_photo_vars = hook_photo_var
            slide_key = 'hook'
        elif slide_type == 'product':
            # Product photo variations must match hook/body to ensure all slideshows have products
            expected_photo_vars = max(hook_photo_var, body_photo_var)
            slide_key = 'product'
        else:  # body
            expected_photo_vars = body_photo_var
            body_num = sum(1 for s in new_slides[:idx] if s['slide_type'] == 'body') + 1
            slide_key = f'body_{body_num}'

        # Create photo × text matrix of tasks
        for p_idx in range(expected_photo_vars):
            scene_var_idx = min(p_idx, len(scene_variations) - 1)
            scene_var = scene_variations[scene_var_idx] if scene_variations else {'scene_description': '', 'text_variations': ['']}

            scene_description = scene_var.get('scene_description', '')
            text_variations = scene_var.get('text_variations', [''])

            # Physical consistency validation (coat in water, etc.)
            if USE_PHYSICAL_CONSISTENCY and scene_description:
                is_valid, enhanced_scene = validate_scene_consistency(
                    scene_description,
                    slide_info=f"{slide_key}_p{p_idx+1}"
                )
                if not is_valid:
                    logger.warning(f"Scene validation issue for {slide_key}: using enhanced scene with attire instructions")
                scene_description = enhanced_scene

            for t_idx, text_content in enumerate(text_variations):
                photo_ver = p_idx + 1
                text_ver = t_idx + 1

                task_id = f"{job_id}_{idx:02d}_{slide_key}_p{photo_ver}_t{text_ver}"
                output_path = os.path.join(output_dir, f'{idx:02d}_{slide_key}_p{photo_ver}_t{text_ver}.jpg')

                # Determine product image for product slides
                product_img = None
                if slide_type == 'product' and product_image_paths:
                    product_img = product_image_paths[p_idx] if p_idx < len(product_image_paths) else product_image_paths[0]

                # Determine dependency type
                if has_persona:
                    if first_persona_task_id is None:
                        dependency_type = "persona_first"
                        first_persona_task_id = task_id
                        depends_on = ""
                    else:
                        dependency_type = "persona_dependent"
                        depends_on = first_persona_task_id
                else:
                    dependency_type = "none"
                    depends_on = ""

                # Skip product slides in clean_image_mode (handled separately)
                if clean_image_mode and slide_type == 'product' and product_img:
                    # Copy product image directly instead of queuing
                    _copy_product_image(product_img, output_path, log)
                    continue

                # Create ImageTask
                task = ImageTask(
                    task_id=task_id,
                    job_id=job_id,
                    job_type="single",  # Will be updated by caller if batch
                    dependency_group=persona_group if has_persona else "",
                    dependency_type=dependency_type,
                    depends_on_task_id=depends_on,
                    slide_type=slide_type,
                    slide_index=idx,
                    scene_description=scene_description,
                    text_content=text_content,
                    text_position_hint=slide.get('text_position_hint', ''),
                    reference_image_path=slide_paths[ref_idx] if ref_idx < len(slide_paths) else slide_paths[0],
                    product_image_path=product_img or '',
                    persona_reference_path='',  # Set by queue when dependency resolves
                    has_persona=has_persona,
                    text_style=text_style,
                    visual_style=visual_style,
                    persona_info=persona_info,  # Demographics for new persona creation
                    clean_image_mode=clean_image_mode,
                    product_description=product_description,
                    shows_product_on_face=shows_product_on_face,  # Per-slide face tape flag
                    transformation_role=slide.get('transformation_role', ''),  # "before", "after", or ""
                    transformation_problem=slide.get('transformation_problem', ''),  # "under_eye", "forehead_lines", etc.
                    layout_type=slide.get('layout_type', 'single'),  # "single" or "split_screen"
                    split_config=slide.get('split_config') or {},  # Split-screen configuration
                    version=photo_ver,
                    output_path=output_path,
                    output_dir=output_dir
                )

                queue.submit(task)
                tasks_submitted += 1

    log.info(f"Submitted {tasks_submitted} tasks to queue for job {job_id}")
    return tasks_submitted


def _copy_product_image(src_path: str, dst_path: str, log):
    """Copy and resize product image for clean_image_mode."""
    import shutil
    from PIL import Image as PILImage

    try:
        with PILImage.open(src_path) as img:
            target_w, target_h = 1080, 1440
            img_ratio = img.width / img.height
            target_ratio = target_w / target_h

            if img_ratio > target_ratio:
                new_h = target_h
                new_w = int(new_h * img_ratio)
            else:
                new_w = target_w
                new_h = int(new_w / img_ratio)

            img_resized = img.resize((new_w, new_h), PILImage.Resampling.LANCZOS)
            left = (new_w - target_w) // 2
            top = (new_h - target_h) // 2
            img_cropped = img_resized.crop((left, top, left + target_w, top + target_h))

            if img_cropped.mode in ('RGBA', 'P'):
                img_cropped = img_cropped.convert('RGB')
            img_cropped.save(dst_path, 'JPEG', quality=95)

        log.debug(f"Copied product image: {os.path.basename(src_path)} -> {os.path.basename(dst_path)}")
    except Exception as e:
        log.error(f"Failed to copy product image: {e}")
        shutil.copy2(src_path, dst_path)


def wait_for_job_completion(
    job_id: str,
    progress_callback: Optional[Callable] = None,
    timeout: int = 780,  # Must be < Celery soft_time_limit (900s)
    poll_interval: float = 2.0,
    stall_timeout: int = 180  # Bail out if no progress for 3 minutes
) -> dict:
    """
    Wait for all tasks in a job to complete.

    Args:
        job_id: Job identifier
        progress_callback: Optional callback(current, total, message)
        timeout: Maximum wait time in seconds (< Celery soft_time_limit)
        poll_interval: Seconds between status checks
        stall_timeout: Return partial results if no progress for this many seconds

    Returns:
        dict with:
            - images: List of generated image paths
            - completed: Number completed
            - failed: Number failed
            - is_complete: Whether job fully completed

    Raises:
        GeminiServiceError: If job times out with ZERO completed images
    """
    from image_queue import get_global_queue

    queue = get_global_queue()
    start_time = time.time()
    last_progress_count = 0
    last_progress_time = time.time()

    while True:
        status = queue.get_job_status(job_id)

        if progress_callback:
            progress_callback(
                status['completed'],
                status['total'],
                f"Generated {status['completed']}/{status['total']} images"
            )

        if status['is_complete']:
            # Job finished (all tasks completed or failed)
            if status['failed'] > 0:
                logger.warning(f"Job {job_id} completed with {status['failed']} failures")

            return {
                'images': status['results'],
                'completed': status['completed'],
                'failed': status['failed'],
                'is_complete': True
            }

        # Track progress for stall detection
        current_done = status['completed'] + status['failed']
        if current_done > last_progress_count:
            last_progress_count = current_done
            last_progress_time = time.time()

        # Check stall: no progress for stall_timeout seconds and some tasks are stuck
        stall_elapsed = time.time() - last_progress_time
        if stall_elapsed > stall_timeout and status['completed'] > 0:
            logger.warning(
                f"Job {job_id} stalled for {stall_elapsed:.0f}s with "
                f"{status['completed']}/{status['total']} completed. "
                f"Returning partial results."
            )
            return {
                'images': status['results'],
                'completed': status['completed'],
                'failed': status['failed'] + status['pending'] + status['retry'],
                'is_complete': False
            }

        # Check timeout
        elapsed = time.time() - start_time
        if elapsed > timeout:
            # If we have SOME results, return them instead of crashing
            if status['completed'] > 0:
                logger.warning(
                    f"Job {job_id} timed out after {timeout}s but has "
                    f"{status['completed']}/{status['total']} completed. Returning partial results."
                )
                return {
                    'images': status['results'],
                    'completed': status['completed'],
                    'failed': status['failed'] + status['pending'] + status['retry'],
                    'is_complete': False
                }
            raise GeminiServiceError(f"Job {job_id} timed out after {timeout}s. "
                                    f"Status: {status['completed']}/{status['total']} completed")

        time.sleep(poll_interval)


def run_pipeline_queued(
    slide_paths: list[str],
    product_image_paths: list[str],
    product_description: str,
    output_dir: str,
    job_id: str,
    progress_callback: Optional[Callable] = None,
    hook_photo_var: int = 1,
    hook_text_var: int = 1,
    body_photo_var: int = 1,
    body_text_var: int = 1,
    product_text_var: int = 1,
    request_id: str = None,
    preset_id: str = 'gemini'
) -> dict:
    """
    Run the pipeline using the global queue system.

    This version submits tasks to the global queue and waits for completion
    instead of processing directly. This ensures proper rate limiting across
    all concurrent jobs.

    Args:
        Same as run_pipeline, plus:
        job_id: Unique identifier for this job

    Returns:
        Same as run_pipeline
    """
    log = get_request_logger('gemini', request_id) if request_id else logger
    start_time = time.time()

    log.info(f"Pipeline (queued) starting for job {job_id}")

    # Determine clean_image_mode
    clean_image_mode = preset_id != 'gemini'

    # Step 1: Analysis (same as direct mode)
    if progress_callback:
        progress_callback('analyzing', 'Analyzing slideshow...', 5)

    # Pre-extract brand from description for validation
    pre_extraction = _extract_brand_from_description(product_description)
    log.debug(f"Pre-extracted brand candidates: {pre_extraction}")

    analysis = analyze_and_plan(
        slide_paths,
        product_image_paths,
        product_description,
        output_dir,
        hook_photo_var=hook_photo_var,
        hook_text_var=hook_text_var,
        body_photo_var=body_photo_var,
        body_text_var=body_text_var,
        product_text_var=product_text_var,
        request_id=request_id
    )

    # Validate brand
    brand_valid, corrected_brand = _validate_brand_not_hallucinated(
        analysis,
        product_description,
        pre_extraction.get('likely_brand')
    )
    if not brand_valid:
        log.warning(f"Correcting hallucinated brand to: '{corrected_brand}'")
        if 'required_keywords' in analysis:
            analysis['required_keywords']['brand_name'] = corrected_brand
            analysis['required_keywords']['brand_corrected'] = True

    # Validate keywords
    is_valid, keyword_issues = _validate_required_keywords(analysis)
    if not is_valid:
        log.warning(f"Keyword validation failed: {keyword_issues}")
        analysis = _inject_missing_keywords(analysis)

    # Save analysis
    analysis_path = os.path.join(output_dir, 'analysis.json')
    with open(analysis_path, 'w') as f:
        json.dump(analysis, f, indent=2)

    if progress_callback:
        progress_callback('generating', 'Submitting to generation queue...', 35)

    # Step 2: Submit to queue
    log.info("Step 2: Submitting to global queue")

    tasks_submitted = submit_to_queue(
        analysis=analysis,
        slide_paths=slide_paths,
        product_image_paths=product_image_paths,
        output_dir=output_dir,
        job_id=job_id,
        hook_photo_var=hook_photo_var,
        body_photo_var=body_photo_var,
        request_id=request_id,
        clean_image_mode=clean_image_mode,
        product_description=product_description
    )

    log.info(f"Submitted {tasks_submitted} tasks to queue")

    if progress_callback:
        progress_callback('generating', 'Waiting for generation...', 40)

    # Step 3: Wait for completion
    def queue_progress(current, total, message):
        if progress_callback:
            percent = 40 + int(50 * current / total) if total > 0 else 40
            progress_callback('generating', message, percent)

    result = wait_for_job_completion(job_id, progress_callback=queue_progress)

    # Build variations structure
    variations_structure = {}
    for img_path in result['images']:
        filename = os.path.basename(img_path)
        parts = filename.replace('.jpg', '').replace('.png', '').split('_')
        # New format: 00_hook_p1_t1, 01_body_1_p1_t1, 03_product_p1_t1
        if len(parts) >= 2 and parts[0].isdigit():
            # New format with slide_index prefix
            if parts[1] in ['hook', 'product']:
                slide_key = parts[1]
            else:
                slide_key = f"{parts[1]}_{parts[2]}"
        else:
            # Legacy format without prefix
            if parts[0] in ['hook', 'product']:
                slide_key = parts[0]
            else:
                slide_key = f"{parts[0]}_{parts[1]}"
        if slide_key not in variations_structure:
            variations_structure[slide_key] = []
        variations_structure[slide_key].append(img_path)

    generation_result = {
        'images': result['images'],
        'variations': variations_structure
    }

    # Step 4: Text rendering (if clean_image_mode)
    if clean_image_mode and generation_result['images']:
        if progress_callback:
            progress_callback('rendering', 'Adding text overlays...', 92)

        log.info(f"Step 4: Rendering text on {len(generation_result['images'])} images")

        # Build text mapping
        text_mapping = {}
        for slide in analysis.get('new_slides', []):
            idx = slide['slide_index']
            slide_type = slide['slide_type']

            # Extract text_variations from scene_variations (new structure)
            # Structure: slide['scene_variations'][i]['text_variations']
            scene_variations = slide.get('scene_variations', [])
            if scene_variations and isinstance(scene_variations, list):
                # Collect all text variations from all scene variations
                text_variations = []
                for sv in scene_variations:
                    if isinstance(sv, dict):
                        tvs = sv.get('text_variations', [])
                        if isinstance(tvs, list):
                            text_variations.extend(tvs)
                        elif tvs:  # Single string
                            text_variations.append(tvs)
                # Fallback to old structure if no texts found
                if not text_variations:
                    text_variations = slide.get('text_variations', [slide.get('text_content', '')])
            else:
                # Legacy: try direct text_variations or text_content
                text_variations = slide.get('text_variations', [slide.get('text_content', '')])

            if slide_type == 'hook':
                slide_key = 'hook'
            elif slide_type == 'product':
                slide_key = 'product'
            elif slide_type == 'cta':
                continue
            else:
                body_num = sum(1 for s in analysis['new_slides'][:idx] if s['slide_type'] == 'body') + 1
                slide_key = f'body_{body_num}'

            text_mapping[slide_key] = text_variations

        # Render text on images
        rendered_images = []
        for img_path in generation_result['images']:
            try:
                filename = os.path.basename(img_path)
                parts = filename.replace('.jpg', '').replace('.png', '').split('_')

                # New format: 00_hook_p1_t1, 01_body_1_p1_t1, 03_product_p1_t1
                if len(parts) >= 2 and parts[0].isdigit():
                    # New format with slide_index prefix
                    if parts[1] in ['hook', 'product']:
                        slide_key = parts[1]
                    else:
                        slide_key = f"{parts[1]}_{parts[2]}"
                else:
                    # Legacy format without prefix
                    if parts[0] in ['hook', 'product']:
                        slide_key = parts[0]
                    else:
                        slide_key = f"{parts[0]}_{parts[1]}"

                text_idx = 0
                for part in parts:
                    if part.startswith('t'):
                        text_idx = int(part[1:]) - 1
                        break

                texts = text_mapping.get(slide_key, [''])
                text_content = texts[text_idx] if text_idx < len(texts) else texts[0] if texts else ''

                if not text_content:
                    rendered_images.append(img_path)
                    continue

                safe_zone_result = detect_safe_zones(img_path)
                if not safe_zone_result.safe_zones:
                    rendered_images.append(img_path)
                    continue

                zone = safe_zone_result.safe_zones[safe_zone_result.recommended_zone or 0]
                render_text(
                    image_path=img_path,
                    text=text_content,
                    zone=zone,
                    preset_id=preset_id,
                    output_path=img_path
                )
                rendered_images.append(img_path)

            except Exception as e:
                log.error(f"Failed to render text on {img_path}: {e}")
                rendered_images.append(img_path)

        generation_result['images'] = rendered_images

    # Cleanup queue data
    from image_queue import get_global_queue
    get_global_queue().cleanup_job(job_id)

    elapsed = time.time() - start_time
    log.info(f"Pipeline (queued) complete in {elapsed:.1f}s: {len(generation_result['images'])} images")

    return {
        'analysis': analysis,
        'generated_images': generation_result['images'],
        'variations': generation_result['variations'],
        'analysis_path': analysis_path
    }


# For testing
if __name__ == '__main__':
    print('Gemini Service V2 - Redesigned Pipeline')
    print(f'Text Model: {TEXT_MODEL}')
    print(f'Image Model: {IMAGE_MODEL}')
    print(f'API Key configured: {bool(os.getenv("GEMINI_API_KEY"))}')
    print(f'Queue Mode: {USE_QUEUE_MODE}')
    print()
    print('Changes from V1:')
    print('- Smart product insertion (6 slideshow types)')
    print('- ICP/audience mapping for product positioning')
    print('- Persona consistency across slides')
    print('- Clear image labeling (STYLE_REFERENCE, PERSONA_REFERENCE, PRODUCT_PHOTO)')
    print('- Exact text content generation (not just descriptions)')
    print('- Optional CTA detection')
    print('- Global queue system for rate limiting')