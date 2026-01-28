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

from google import genai
from google.genai import types
from google.genai.types import HarmCategory, HarmBlockThreshold, SafetySetting

# Model names
ANALYSIS_MODEL = 'gemini-3-pro-preview'
IMAGE_MODEL = 'gemini-3-pro-image-preview'
GROUNDING_MODEL = 'gemini-2.0-flash'  # Fast model for grounding searches

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

    # Settings/atmosphere
    (r'\bbedroom\b', 'cozy indoor space'),
    (r'\bbed\b', 'cozy setting'),
    (r'\bsilk pillowcases?\b', 'soft cushion covers'),
    (r'\bpillowcases?\b', 'cushion covers'),
    (r'\bpillows?\b', 'cushions'),
    (r'\bsheets\b', 'soft linens'),
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
    (r'\blying in bed\b', 'relaxing at home'),
    (r'\blaying in bed\b', 'relaxing at home'),
    (r'\bon the bed\b', 'in cozy setting'),
    (r'\bundressing\b', 'getting ready'),
    (r'\bshowering\b', 'freshening up'),
    (r'\bbathing\b', 'relaxing'),
]

def _sanitize_scene_description(scene: str) -> tuple[str, bool]:
    """
    Sanitize a scene description by replacing potentially triggering words.

    Returns:
        tuple: (sanitized_scene, was_modified)
    """
    sanitized = scene
    was_modified = False

    for pattern, replacement in SAFETY_WORD_REPLACEMENTS:
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

    Args:
        timeout: HTTP request timeout in seconds (default: REQUEST_TIMEOUT)
    """
    api_key = os.getenv('GEMINI_API_KEY')  # Read dynamically for hot-reload support
    if not api_key:
        raise GeminiServiceError('GEMINI_API_KEY environment variable not set')
    return genai.Client(
        api_key=api_key,
        http_options={'timeout': timeout * 1000}  # Convert to milliseconds
    )


# Cache for grounded product searches (avoid repeated API calls)
_grounding_cache = {}
_grounding_cache_lock = threading.Lock()


def _get_real_products_for_scene(category: str, scene_type: str = "lifestyle") -> str:
    """
    Use Google Search grounding to find real product names for a scene.

    Args:
        category: Product category (e.g., "skincare", "sleep", "wellness")
        scene_type: Type of scene (e.g., "bathroom", "bedroom", "morning routine")

    Returns:
        String with real product names to include in scene generation
    """
    cache_key = f"{category}_{scene_type}"

    with _grounding_cache_lock:
        if cache_key in _grounding_cache:
            logger.debug(f"Using cached products for {cache_key}")
            return _grounding_cache[cache_key]

    try:
        client = _get_client(timeout=30)  # Quick timeout for grounding

        query = f"""For viral TikTok content about {category}, list 5-8 SPECIFIC real product names
that would naturally appear in a {scene_type} scene.

Return ONLY a comma-separated list of real brand + product names like:
"CeraVe Hydrating Cleanser, The Ordinary Niacinamide, Glow Recipe Watermelon Toner"

Focus on products that are:
- Actually popular on TikTok in 2024
- Recognizable brands (not generic)
- Would naturally be in this type of scene

Just the product names, nothing else."""

        response = client.models.generate_content(
            model=GROUNDING_MODEL,
            contents=query,
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())],
                temperature=0.3  # Lower temp for factual responses
            )
        )

        products = response.text.strip()

        # Cache the result
        with _grounding_cache_lock:
            _grounding_cache[cache_key] = products

        logger.info(f"Grounded products for {cache_key}: {products[:100]}...")
        return products

    except Exception as e:
        logger.warning(f"Grounding search failed for {cache_key}: {e}")
        # Return empty - scene will generate without specific products
        return ""


def _get_specific_brand_for_product(generic_product: str) -> str:
    """
    Get a SINGLE specific real brand name for a generic product.

    Args:
        generic_product: Generic product like "tart cherry juice", "weighted blanket"

    Returns:
        Specific brand name like "Cheribundi tart cherry juice" or empty if failed
    """
    cache_key = f"brand_{generic_product.lower().strip()}"

    with _grounding_cache_lock:
        if cache_key in _grounding_cache:
            logger.debug(f"Using cached brand for {generic_product}")
            return _grounding_cache[cache_key]

    try:
        client = _get_client(timeout=20)

        query = f"""What is ONE popular, recognizable brand of {generic_product} that's trending on TikTok?

Return ONLY the brand name + product, like:
- "Cheribundi Tart Cherry Juice"
- "Bearaby Napper Weighted Blanket"
- "Hatch Restore Sunrise Alarm"

Just the single brand + product name, nothing else. Pick something visually recognizable."""

        response = client.models.generate_content(
            model=GROUNDING_MODEL,
            contents=query,
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())],
                temperature=0.5  # Some variety in brand selection
            )
        )

        brand = response.text.strip().strip('"').strip("'")

        # Cache the result
        with _grounding_cache_lock:
            _grounding_cache[cache_key] = brand

        logger.info(f"Grounded brand for '{generic_product}': {brand}")
        return brand

    except Exception as e:
        logger.warning(f"Brand grounding failed for {generic_product}: {e}")
        return ""


# Products that should be replaced with real brands
BRANDABLE_PRODUCTS = [
    # Drinks
    ('tart cherry juice', 'tart cherry juice'),
    ('cherry juice', 'tart cherry juice'),
    ('chamomile tea', 'chamomile tea'),
    ('herbal tea', 'herbal tea'),
    ('golden milk', 'golden milk turmeric drink'),
    ('matcha', 'matcha powder'),

    # Sleep products
    ('weighted blanket', 'weighted blanket'),
    ('silk pillowcase', 'silk pillowcase'),
    ('sleep mask', 'sleep mask'),
    ('eye mask', 'sleep eye mask'),
    ('white noise machine', 'white noise machine'),
    ('sunrise alarm', 'sunrise alarm clock'),
    ('sound machine', 'sleep sound machine'),

    # Wellness
    ('magnesium spray', 'magnesium spray'),
    ('magnesium powder', 'magnesium supplement powder'),
    ('diffuser', 'aromatherapy diffuser'),
    ('essential oil', 'lavender essential oil'),
    ('humidifier', 'bedroom humidifier'),

    # Other
    ('blue light glasses', 'blue light blocking glasses'),
    ('journal', 'wellness journal'),
    ('yoga mat', 'yoga mat'),
    ('foam roller', 'foam roller'),
    ('ice roller', 'face ice roller'),
]


def _enhance_scene_with_real_brand(scene_description: str) -> str:
    """
    Detect if scene mentions a brandable product and replace with real brand.

    Only enhances if a specific product is mentioned - doesn't add random products.

    Args:
        scene_description: Original scene description

    Returns:
        Enhanced scene with real brand name, or original if no product detected
    """
    scene_lower = scene_description.lower()

    # Check if scene mentions any brandable product
    for product_phrase, search_term in BRANDABLE_PRODUCTS:
        if product_phrase in scene_lower:
            # Found a brandable product - get real brand
            real_brand = _get_specific_brand_for_product(search_term)

            if real_brand:
                # Replace generic with branded version in the scene
                # Make it natural - just mention the brand should be visible
                enhanced = f"""{scene_description}

SPECIFIC PRODUCT: Show {real_brand} (this exact brand should be recognizable in the image).
Only show this ONE product as the hero item - no other random products cluttering the scene."""

                logger.info(f"Enhanced scene with brand: {product_phrase} -> {real_brand}")
                return enhanced

            # If grounding failed, still return original
            break

    # No brandable product found or grounding failed - return original
    return scene_description


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

    product_text = product_slides[0].get('text_content', '').lower()

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

    client = _get_client()
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
TASK 5: MIMIC THE ORIGINAL SLIDESHOW CONTENT
═══════════════════════════════════════════════════════════════

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

CTA SLIDE (only if original has one):
- Keep engagement style
- Adapt question to new category
- If original doesn't have CTA, don't add one

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

FOR CTA SLIDE (exactly 1 scene variation):
{{
    "slide_index": 6,
    "slide_type": "cta",
    "scene_variations": [
        {{
            "scene_description": "Peaceful bedroom with morning sunlight",
            "text_variations": ["generate exactly 1 text item for CTA"]
        }}
    ]
}}

VARIATION RULES (CRITICAL - FOLLOW EXACTLY!):
⚠️ THE COUNTS BELOW ARE MANDATORY - DO NOT DEVIATE!

- Hook slides: EXACTLY {hook_photo_var} scene_variations, each with EXACTLY {hook_text_var} text_variations
- Body slides: EXACTLY {body_photo_var} scene_variations, each with EXACTLY {body_text_var} text_variations
- Product slides: EXACTLY 1 scene_variation with EXACTLY {product_text_var} text_variations
- CTA slides: EXACTLY 1 scene_variation with EXACTLY 1 text_variation

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
- SPLIT the message into TWO DIFFERENT parts using " | " separator
- Format: "top text | bottom text"

EXAMPLE - Original has text at top and bottom:
- WRONG: "skincare hack that actually works ⚡" (will be duplicated)
- CORRECT: "skincare hack ⚡ | that actually works"

EXAMPLE - Hook with top and bottom text:
{{
    "text_variations": [
        "morning routine tip 💫 | watch til the end",
        "my secret hack ✨ | this changed everything"
    ]
}}

The " | " separator tells the image generator to place:
- First part at TOP position
- Second part at BOTTOM position

If text_position_hint says "top and bottom" or "multiple positions" → USE THE SEPARATOR!
If text is only in ONE position → write normal text without separator.

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
        "vibe": "overall feeling (e.g., friendly, approachable, authentic)"
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

    "structure": {{
        "total_slides": "same as original if replacing, original+1 if adding",
        "hook_index": 0,
        "body_indices": [1, 2, 3, 5],
        "product_index": "from product_placement.product_slide_index",
        "cta_index": 6 or null
    }},

    "new_slides": [
        {{
            "slide_index": 0,
            "slide_type": "hook",
            "role_in_story": "Hook - grabs attention with relatable problem",
            "reference_image_index": 0,
            "has_persona": true,
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
            "slide_index": 4,
            "slide_type": "product",
            "role_in_story": "Product recommendation - natural fit in the narrative",
            "reference_image_index": 4,
            "has_persona": false,
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
    ]
}}

IMPORTANT - reference_image_index explained:
- This tells us which ORIGINAL slide to use as reference
- MIMIC both the STYLE (font, colors, layout) AND the TYPE of content
- Create SIMILAR content that matches what the original slide shows

CRITICAL RULES:
1. SLIDE COUNT:
   - If competitor_detection.found == true: new_slides array has {num_slides} slides (REPLACE competitor)
   - If competitor_detection.found == false: new_slides array has {num_slides} + 1 slides (ADD product at end)
2. Exactly ONE slide with slide_type="product"
3. Hook slides: {hook_photo_var} scene_variations, each with {hook_text_var} text_variations
4. Body slides: {body_photo_var} scene_variations, each with {body_text_var} text_variations
5. Product slides: 1 scene_variation with {product_text_var} text_variations
6. CTA slides: 1 scene_variation
7. Each scene_variation MUST have a different scene_description (different take on same concept!)
8. has_persona: set to true if ORIGINAL slide shows a person, false otherwise
9. Include "visual" object for each slide with composition details
10. Include "role_in_story" for each slide describing its narrative purpose
11. scene_description MUST end with "COMPOSITION: framing=X, angle=Y, position=Z, background=W"
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

    try:
        log.debug(f"Calling {ANALYSIS_MODEL} with {len(contents)} content parts")
        response = client.models.generate_content(
            model=ANALYSIS_MODEL,
            contents=contents,
            config=types.GenerateContentConfig(
                safety_settings=SAFETY_SETTINGS  # Allow benign lifestyle content analysis
            )
        )
        elapsed = time.time() - start_time
        result_text = response.text
        log.debug(f"Analysis API response in {elapsed:.1f}s, response length: {len(result_text)}")

        # Parse JSON
        start = result_text.find('{')
        end = result_text.rfind('}') + 1
        if start >= 0 and end > start:
            analysis = json.loads(result_text[start:end])
        else:
            log.error("No valid JSON in analysis response")
            raise GeminiServiceError('No valid JSON in response')

        # Validate structure
        if 'new_slides' not in analysis:
            log.error("Missing new_slides in analysis")
            raise GeminiServiceError('Missing new_slides in analysis')

        # Validate slide count based on product_placement action
        # - "replace": same number of slides as original
        # - "add": original + 1 (product slide added at end)
        product_placement = analysis.get('product_placement', {})
        placement_action = product_placement.get('action', 'add')
        expected_slides = num_slides if placement_action == 'replace' else num_slides + 1

        if len(analysis['new_slides']) != expected_slides:
            log.error(f"Slide count mismatch: expected {expected_slides} (action={placement_action}), got {len(analysis['new_slides'])}")
            raise GeminiServiceError(f"Expected {expected_slides} slides (action={placement_action}), got {len(analysis['new_slides'])}")

        # Validate exactly one product slide
        product_slides = [s for s in analysis['new_slides'] if s.get('slide_type') == 'product']
        if len(product_slides) != 1:
            log.error(f"Product slide count error: expected 1, got {len(product_slides)}")
            raise GeminiServiceError(f"Expected exactly 1 product slide, got {len(product_slides)}")

        # Save analysis.json
        os.makedirs(output_dir, exist_ok=True)
        analysis_path = os.path.join(output_dir, 'analysis.json')
        with open(analysis_path, 'w') as f:
            json.dump(analysis, f, indent=2)

        slideshow_type = analysis.get('slideshow_type', 'unknown')
        log.info(f"Analysis complete in {elapsed:.1f}s: type={slideshow_type}, {len(analysis['new_slides'])} slides")
        return analysis

    except json.JSONDecodeError as e:
        log.error(f"Failed to parse analysis JSON: {e}")
        raise GeminiServiceError(f'Failed to parse analysis JSON: {e}')
    except Exception as e:
        log.error(f"Analysis failed: {str(e)}", exc_info=True)
        raise GeminiServiceError(f'Analysis failed: {str(e)}')


def _generate_single_image(
    client,
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
    version: int = 1,
    clean_image_mode: bool = False,
    product_description: str = ""
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
        font_style = text_style.get('font_style', 'modern-clean')
        style_description = _get_style_description(font_style)
        background_box = text_style.get('background_box', 'none')

        # Build enhanced box instructions if box/pill style detected
        if background_box and ('box' in background_box.lower() or 'pill' in background_box.lower()):
            box_instruction = """
BOX STYLE SPECIFICATIONS (MATCH EXACTLY):

VISUAL REFERENCE: Think Instagram/TikTok caption bubbles - clean, rounded, tight fit around text.

CORE RULES:
- Background: Pure white (#FFFFFF) rounded rectangle that ENVELOPS the text
- Corner radius: Soft rounded corners (~10-15% of box height) - pill/capsule shape
- Padding: ~15-20px around text on all sides - box hugs the text, not oversized
- Text color: Pure black (#000000) on the white background
- Box width: ONLY as wide as the text line needs + padding (NOT full image width!)

LINE-SPECIFIC LAYOUT:
- For 1 line: Single white pill/capsule shape tightly around the text, centered
- For 2 lines: Each line gets its OWN separate box/pill, stacked vertically with ~8-10px gap
- For 3+ lines: Same as 2 lines - each line gets its own box, creates "stack of pills" effect

AVOID:
- One giant box containing all lines
- Boxes that extend to image edges
- Oversized boxes with too much padding
- Boxes that overlap each other
"""
        else:
            box_instruction = f"- Background: {background_box}"

        text_style_instruction = f"""TEXT STYLE REQUIREMENTS (MATCH EXACTLY):

TYPOGRAPHY APPEARANCE:
- Style: {font_style}
  (This means: {style_description})
- Weight: {text_style.get('font_weight', 'bold')} strokes
- Letter spacing: {text_style.get('letter_spacing', 'normal')}
- Color: {text_style.get('font_color', 'white')}

TEXT EFFECTS:
- Shadow: {text_style.get('shadow', 'none')}
- Outline: {text_style.get('outline', 'none')}
{box_instruction}

OVERALL VIBE: {text_style.get('visual_vibe', 'clean minimal')}
Position: {text_style.get('position_style', 'varies by slide')}

TEXT SIZE (CRITICAL - FOLLOW EXACTLY):
- Text must be SMALL - approximately 3-5% of image height
- Maximum 2 lines of text total
- Each line maximum 6 words
- NEVER generate large/bold/dominant text that takes over the image
- The IMAGE is the focus, text is a subtle accent only
- Think "small Instagram caption" not "poster headline"
- If in doubt, make text SMALLER
- NEVER duplicate/repeat the same text line twice in the image

The text appearance is CRITICAL for authenticity - match this exact visual style!
"""
    else:
        text_style_instruction = "Use clean, bold, white sans-serif text with subtle shadow."

    # Quality constraints to prevent weird/AI-looking images
    quality_constraints = """
IMAGE QUALITY REQUIREMENTS:
- This must look like authentic TikTok/Instagram content
- Clean, professional, aspirational aesthetic
- Proper lighting - natural or soft studio lighting
- Sharp focus on main subjects
- Harmonious color palette that matches the mood

⚠️ CRITICAL - FULL-BLEED FORMAT (MANDATORY):
- Image content MUST extend to ALL FOUR EDGES
- NO black bars, borders, or frames on ANY side (top, bottom, left, right)
- NO letterboxing or pillarboxing
- NO phone UI elements, navigation bars, or "Share/Edit/Delete" buttons
- The scene/background must fill the ENTIRE frame edge-to-edge
- Think "camera viewport" - subject fills the whole 9:16 frame with NO empty borders

DO NOT GENERATE:
- Surreal, abstract, or "obviously AI" aesthetics
- Distorted objects, text, or proportions
- Unnatural color combinations or lighting
- Blurry or low-quality appearance
- Cluttered or chaotic compositions
- Floating objects or impossible physics
- ANY black/dark bars or frames at edges (ESPECIALLY at bottom!)
- Phone screenshots with visible UI elements
- Steam or vapor effects (tea steam, coffee steam, humidifier mist, candle smoke) - these look fake/AI-generated
"""

    if slide_type == 'product':
        # PRODUCT SLIDE: User's product photo + style reference
        prompt = f"""Generate a TikTok slide featuring a product AS A CASUAL TIP.

{text_style_instruction}

[PRODUCT_PHOTO] - User's product image. THIS IS THE BASE IMAGE - keep it as the main visual.

[STYLE_REFERENCE] - Reference slide for visual composition and mood.

TEXT TO ADD:
{text_content}

LAYOUT: {text_position_hint}

CRITICAL TEXT PLACEMENT RULES:
- Product must remain FULLY VISIBLE - NEVER place text over the product
- Text should be in empty/background areas only
- If unsure, place text at TOP or BOTTOM edges of image
- Main subject/object must be completely unobstructed

MULTI-POSITION TEXT RULE:
- If TEXT TO DISPLAY contains " | " (pipe separator), it means TWO separate texts
- Format: "TOP_TEXT | BOTTOM_TEXT"
- Place the FIRST part (before |) at the TOP of the image
- Place the SECOND part (after |) at the BOTTOM of the image
- NEVER place both texts in the same location - they must be in DIFFERENT positions

LAYOUT RULES:
- Generate ONE SINGLE product image - no comparisons
- NO star ratings, review scores, or rating graphics
- NO side-by-side comparisons or split screens
- NO grids or collages

GOAL: Look like "just another tip" - NOT an advertisement.
{quality_constraints}"""

        contents = [
            prompt,
            "[PRODUCT_PHOTO]",
            types.Part.from_bytes(
                data=_load_image_bytes(product_image_path),
                mime_type=_get_image_mime_type(product_image_path)
            ),
            "[STYLE_REFERENCE]",
            types.Part.from_bytes(
                data=_load_image_bytes(reference_image_path),
                mime_type=_get_image_mime_type(reference_image_path)
            )
        ]
    
    elif slide_type == 'cta':
        # CTA SLIDE: Usually text-focused, simple background
        prompt = f"""Generate a TikTok CTA (call-to-action) slide.

{text_style_instruction}

[STYLE_REFERENCE] - Reference CTA slide for background style and composition.

TEXT TO DISPLAY:
{text_content}

LAYOUT: {text_position_hint}
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

        if has_persona and persona_reference_path:
            # With persona - need consistency
            prompt = f"""Generate a TikTok {slide_label} slide.

{text_style_instruction}
{variation_instruction}
[STYLE_REFERENCE] - Reference slide for visual composition and mood.
MIRROR the exact composition from the reference:
- Same framing (close-up, medium, wide)
- Same camera angle (straight, above, below, side)
- Same subject position in frame (center, left, right)
- Similar background vibe and setting

[PERSONA_REFERENCE] - Person to use. Generate the EXACT SAME PERSON in a new scene:
- SAME face, hair color, skin tone, facial features
- SAME body type and general appearance
- DIFFERENT clothing appropriate for this scene context
- The outfit should match the situation (casual at home, dressed for going out, workout clothes for gym, etc.)
- This must look like the same creator, just in different clothes

SKIN REALISM (CRITICAL - apply to all faces):
Increase skin realism with subtle natural pores, fine micro-bumps, and gentle uneven smoothness.
Add tasteful micro-imperfections: tiny blemishes, faint redness, subtle under-eye texture, slight natural tone variation.
Correct highlights to avoid plastic shine—soft realistic specular highlights with mild oiliness in the T-zone.
Add a few natural baby hairs and minimal stray strands around the hairline.
Introduce very subtle natural asymmetry without changing identity.
Finish with soft camera realism: light grain, mild shadow noise, natural micro-contrast, no over-sharpening.

DO NOT create: perfect poreless skin, overly smooth texture, plastic or waxy appearance, symmetrical "AI perfect" faces, over-brightened or glowing skin.

NEW SCENE: {scene_description}

TEXT TO DISPLAY:
{text_content}

LAYOUT: {text_position_hint}

CRITICAL TEXT PLACEMENT RULES:
- NEVER cover face or person with text
- NEVER cover main objects/products with text
- Text should be in empty/background areas only
- If unsure, place text at TOP or BOTTOM edges of image

MULTI-POSITION TEXT RULE:
- If TEXT TO DISPLAY contains " | " (pipe separator), it means TWO separate texts
- Format: "TOP_TEXT | BOTTOM_TEXT"
- Place the FIRST part (before |) at the TOP of the image
- Place the SECOND part (after |) at the BOTTOM of the image
- NEVER place both texts in the same location - they must be in DIFFERENT positions

IMPORTANT: Only ONE person in the image - never two people!
{quality_constraints}"""

            contents = [
                prompt,
                "[STYLE_REFERENCE]",
                types.Part.from_bytes(
                    data=_load_image_bytes(reference_image_path),
                    mime_type=_get_image_mime_type(reference_image_path)
                ),
                "[PERSONA_REFERENCE]",
                types.Part.from_bytes(
                    data=_load_image_bytes(persona_reference_path),
                    mime_type=_get_image_mime_type(persona_reference_path)
                )
            ]
        elif has_persona:
            # Has persona but NO reference yet - CREATE a new persona
            prompt = f"""Generate a TikTok {slide_label} slide.

{text_style_instruction}
{variation_instruction}
[STYLE_REFERENCE] - Reference slide for visual composition and mood.
MIRROR the exact composition from the reference:
- Same framing (close-up, medium, wide)
- Same camera angle (straight, above, below, side)
- Same subject position in frame (center, left, right)
- Similar background vibe and setting
(Do NOT copy the person - create a NEW person)

CREATE A NEW PERSONA:
- Attractive, relatable TikTok content creator
- Natural, authentic appearance
- Clothing appropriate for this scene context
- Will be used as reference for other slides

SKIN REALISM (CRITICAL - apply to all faces):
Increase skin realism with subtle natural pores, fine micro-bumps, and gentle uneven smoothness.
Add tasteful micro-imperfections: tiny blemishes, faint redness, subtle under-eye texture, slight natural tone variation.
Correct highlights to avoid plastic shine—soft realistic specular highlights with mild oiliness in the T-zone.
Add a few natural baby hairs and minimal stray strands around the hairline.
Introduce very subtle natural asymmetry without changing identity.
Finish with soft camera realism: light grain, mild shadow noise, natural micro-contrast, no over-sharpening.

DO NOT create: perfect poreless skin, overly smooth texture, plastic or waxy appearance, symmetrical "AI perfect" faces, over-brightened or glowing skin.

NEW SCENE: {scene_description}

TEXT TO DISPLAY:
{text_content}

LAYOUT: {text_position_hint}

CRITICAL TEXT PLACEMENT RULES:
- NEVER cover face or person with text
- NEVER cover main objects/products with text
- Text should be in empty/background areas only
- If unsure, place text at TOP or BOTTOM edges of image

MULTI-POSITION TEXT RULE:
- If TEXT TO DISPLAY contains " | " (pipe separator), it means TWO separate texts
- Format: "TOP_TEXT | BOTTOM_TEXT"
- Place the FIRST part (before |) at the TOP of the image
- Place the SECOND part (after |) at the BOTTOM of the image
- NEVER place both texts in the same location - they must be in DIFFERENT positions

IMPORTANT: Only ONE person in the image - never two people!
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
            # No persona needed - just style reference
            # For body slides: detect if scene mentions a brandable product,
            # and if so, replace with a specific real brand name.
            if slide_type == 'body':
                enhanced_scene = _enhance_scene_with_real_brand(scene_description)
            else:
                enhanced_scene = scene_description

            # Each photo variation now has its own unique scene from analysis
            # Just generate the exact scene described
            scene_instruction = f"""
NEW SCENE (generate THIS exact setting): {enhanced_scene}

CRITICAL - SHOW ONLY WHAT'S DESCRIBED:
- Generate EXACTLY what the scene description says - nothing more, nothing less
- If scene says "glass of water on kitchen counter" → show ONLY water glass on kitchen counter
- If scene says "journal and pen on bed" → show ONLY journal and pen on bed
- DO NOT add random skincare products, bottles, or items not mentioned in the scene
- Each slide should feature ONE MAIN ITEM that matches the tip being given

WRONG: Scene says "tart cherry juice" but image shows juice + skincare bottles + random products
RIGHT: Scene says "tart cherry juice" and image shows ONLY the juice as the hero item
"""

            prompt = f"""Generate a TikTok {slide_label} slide.

{text_style_instruction}
{variation_instruction}
[STYLE_REFERENCE] - Reference slide for visual composition, mood, and content type.
MIRROR the exact composition from the reference:
- Same framing (close-up, medium, wide)
- Same camera angle (straight, above, below, side)
- Same subject position in frame (center, left, right)
- Similar background vibe and setting
MIMIC the type of content shown in the reference - create SIMILAR scenes that match the original's vibe.

{scene_instruction}

TEXT TO DISPLAY:
{text_content}

LAYOUT: {text_position_hint}

CRITICAL TEXT PLACEMENT RULES:
- NEVER cover main objects/products with text
- Text should be in empty/background areas only
- If unsure, place text at TOP or BOTTOM edges of image
- Main subject must be completely unobstructed

MULTI-POSITION TEXT RULE:
- If TEXT TO DISPLAY contains " | " (pipe separator), it means TWO separate texts
- Format: "TOP_TEXT | BOTTOM_TEXT"
- Place the FIRST part (before |) at the TOP of the image
- Place the SECOND part (after |) at the BOTTOM of the image
- NEVER place both texts in the same location - they must be in DIFFERENT positions

IMPORTANT HUMAN BODY RULES:
- PREFER showing objects/products instead of human body parts
- If the scene REQUIRES human body parts (legs, arms, hands, feet), you MUST show the FULL BODY or at least the full upper/lower half - NEVER crop to show ONLY isolated limbs
- Example: If showing "legs up the wall", include the torso and head in frame - NOT just floating legs
- NEVER generate images with cropped/isolated body parts without body context

CRITICAL LAYOUT REQUIREMENT:
Even though the text may compare two things, generate a SINGLE lifestyle photo.
- NO star ratings, review scores, or rating graphics
- NO side-by-side comparisons or split screens
- NO grids, collages, or multi-panel layouts
- NO before/after visual comparisons
- Just ONE beautiful lifestyle scene with the text overlaid
- If text says "X is bad, Y is good" - show ONLY the good option in a natural setting

GENERATE AN AUTHENTIC LIFESTYLE SCENE - NOT a stock photo!
Think "real person's messy-but-aesthetic life" not "studio product shot":

STYLE: Candid, lifestyle photography with natural imperfections
- Real rooms with lived-in details (not perfectly staged)
- Natural window lighting with soft shadows (NOT studio lights)
- Slightly messy/casual vibes (a book left open, wrinkled sheets)
- Warm, inviting atmosphere

GOOD examples (authentic lifestyle scenes):
- Unmade bed with morning sunlight, coffee on nightstand, curtains blowing
- Kitchen counter with half-eaten breakfast, morning light, real dishes
- Bathroom vanity with various products scattered naturally, towel draped
- Cozy corner with blanket, book spine-down, warm lamp light
- Desk with actual work clutter, plant, warm afternoon light through window

BAD examples (DO NOT generate - too fake/stock):
- Close-up of a product on white/marble surface
- Perfectly arranged "flat lay" product shots
- Studio-lit product photography with no context
- Generic stock photo aesthetics (too clean, too posed)
- Bright uniform lighting with no shadows
- Marble countertop with perfectly placed items
- Images with phone UI elements (navigation bars, black frames, Share/Edit buttons)
- Images with black/dark bars at top or bottom (letterboxing)
- Images that don't fill the entire 9:16 frame edge-to-edge
- Steam rising from cups/mugs (obvious AI giveaway)
- Visible humidifier mist or candle smoke (looks fake)

AUTHENTICITY REQUIREMENTS:
- Include subtle imperfections (soft focus areas, natural shadows)
- Show real living spaces (not showroom-perfect)
- Lighting should be natural/ambient (NOT studio lighting)
- Context matters: products should be IN a scene, not the scene itself

The image should feel like you peeked into someone's real life.
If it looks like a stock photo or Amazon listing, it will be REJECTED.
{quality_constraints}"""

            contents = [
                prompt,
                "[STYLE_REFERENCE]",
                types.Part.from_bytes(
                    data=_load_image_bytes(reference_image_path),
                    mime_type=_get_image_mime_type(reference_image_path)
                )
            ]

    # Retry logic with validation and safety fallback
    last_error = None
    tried_sanitized = False  # Track if we've attempted with sanitized prompt
    current_scene = scene_description  # Track current scene description
    current_contents = contents  # Track current prompt contents

    for attempt in range(MAX_RETRIES):
        try:
            response = client.models.generate_content(
                model=IMAGE_MODEL,
                contents=current_contents,
                config=types.GenerateContentConfig(
                    response_modalities=['image', 'text'],
                    image_config=types.ImageConfig(
                        aspect_ratio="3:4",
                        image_size="4K"  # 4096px for better text quality
                    ),
                    safety_settings=SAFETY_SETTINGS  # Allow benign lifestyle content
                )
            )

            # Extract generated image - check for safety block
            if not response.parts:
                # Safety block detected - try sanitizing if we haven't yet
                if not tried_sanitized:
                    sanitized_scene, was_modified = _sanitize_scene_description(current_scene)
                    if was_modified:
                        logger.warning(f"Safety block detected, retrying with sanitized scene: '{current_scene[:50]}...' -> '{sanitized_scene[:50]}...'")
                        tried_sanitized = True
                        current_scene = sanitized_scene
                        # Rebuild prompt with sanitized scene (update the prompt string in contents)
                        current_contents = [c if not isinstance(c, str) or 'NEW SCENE:' not in c
                                           else c.replace(scene_description, sanitized_scene)
                                           for c in current_contents]
                        continue  # Retry with sanitized prompt (don't count as attempt)

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

            if is_safety_error and not tried_sanitized:
                sanitized_scene, was_modified = _sanitize_scene_description(current_scene)
                if was_modified:
                    logger.warning(f"Safety error detected, retrying with sanitized scene: {str(e)[:100]}")
                    tried_sanitized = True
                    current_scene = sanitized_scene
                    current_contents = [c if not isinstance(c, str) or 'NEW SCENE:' not in c
                                       else c.replace(scene_description, sanitized_scene)
                                       for c in current_contents]
                    time.sleep(2)  # Brief pause before retry
                    continue  # Retry with sanitized prompt

            # Check for rate limit error and extract retry delay
            if '429' in str(e) or 'RESOURCE_EXHAUSTED' in str(e):
                # Try to extract retry delay from error (e.g., "retry in 51s")
                match = re.search(r'retry in (\d+\.?\d*)s', error_str)
                if match:
                    wait_time = float(match.group(1)) + 5  # Add buffer
                else:
                    wait_time = 60  # Default 60s for rate limits
                logger.warning(f"Rate limited (429), waiting {wait_time:.0f}s before retry {attempt + 2}/{MAX_RETRIES}")
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

    client = _get_client()
    os.makedirs(output_dir, exist_ok=True)

    new_slides = analysis['new_slides']
    text_style = analysis.get('text_style', None)  # Extract text style from analysis

    # Build all tasks with photo × text variations
    all_tasks = []
    variations_structure = {}  # Track variations by slide key

    for slide in new_slides:
        idx = slide['slide_index']
        ref_idx = slide.get('reference_image_index', idx)
        slide_type = slide['slide_type']
        has_persona = slide.get('has_persona', False)

        # Skip CTA slides - don't generate or upload
        if slide_type == 'cta':
            continue

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

            for t_idx, text_content in enumerate(text_variations):
                photo_ver = p_idx + 1  # 1-indexed
                text_ver = t_idx + 1   # 1-indexed

                # Determine output filename with photo and text version
                output_path = os.path.join(output_dir, f'{slide_key}_p{photo_ver}_t{text_ver}.jpg')

                # For product slides, use the p_idx-th uploaded image
                product_img = None
                if slide_type == 'product' and product_image_paths:
                    product_img = product_image_paths[p_idx] if p_idx < len(product_image_paths) else product_image_paths[0]

                task = {
                    'task_id': f'{slide_key}_p{photo_ver}_t{text_ver}',
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
                    'has_persona': has_persona
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
                    task['version'],  # Pass version for variation diversity
                    clean_image_mode,  # Generate without text for PIL rendering
                    product_description  # For real product grounding in scenes
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
                # Parse task_id from filename (e.g., hook_p1_t1.png)
                filename = os.path.basename(img_path)
                parts = filename.replace('.jpg', '').replace('.png', '').split('_')

                # Extract slide key and text index
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

    tasks_submitted = 0

    # Track persona dependency group
    persona_group = f"{job_id}_persona"
    first_persona_task_id = None

    for slide in new_slides:
        idx = slide['slide_index']
        ref_idx = slide.get('reference_image_index', idx)
        slide_type = slide['slide_type']
        has_persona = slide.get('has_persona', False)

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

            for t_idx, text_content in enumerate(text_variations):
                photo_ver = p_idx + 1
                text_ver = t_idx + 1

                task_id = f"{job_id}_{slide_key}_p{photo_ver}_t{text_ver}"
                output_path = os.path.join(output_dir, f'{slide_key}_p{photo_ver}_t{text_ver}.jpg')

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
                    clean_image_mode=clean_image_mode,
                    product_description=product_description,
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
    timeout: int = 3600,  # 1 hour max
    poll_interval: float = 2.0
) -> dict:
    """
    Wait for all tasks in a job to complete.

    Args:
        job_id: Job identifier
        progress_callback: Optional callback(current, total, message)
        timeout: Maximum wait time in seconds
        poll_interval: Seconds between status checks

    Returns:
        dict with:
            - images: List of generated image paths
            - completed: Number completed
            - failed: Number failed
            - is_complete: Whether job fully completed

    Raises:
        GeminiServiceError: If job times out or has critical failures
    """
    from image_queue import get_global_queue

    queue = get_global_queue()
    start_time = time.time()

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

        # Check timeout
        elapsed = time.time() - start_time
        if elapsed > timeout:
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
    print(f'Analysis Model: {ANALYSIS_MODEL}')
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