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

# Model names
ANALYSIS_MODEL = 'gemini-3-pro-preview'
IMAGE_MODEL = 'gemini-3-pro-image-preview'
GROUNDING_MODEL = 'gemini-2.0-flash'  # Fast model for grounding searches

# Rate limiting config - SIMPLE: 19 requests per minute, sequential
MAX_CONCURRENT = 1    # Sequential - no concurrent requests (prevents burst overload)
RPM_LIMIT = 19        # 19 requests per minute (conservative for Gemini Preview stability)
RATE_WINDOW = 60.0    # Exactly 60 seconds per window
MAX_RETRIES = 5       # More retries for rate limit recovery
REQUEST_TIMEOUT = 120 # 120 sec timeout per API call

# Rate limiter using semaphore + delay
class RateLimiter:
    """
    Semaphore-based rate limiter that enforces RPM limits.
    Ensures delay BEFORE each request, not after.
    """
    def __init__(self, rpm: int = RPM_LIMIT, max_concurrent: int = MAX_CONCURRENT):
        self.semaphore = threading.Semaphore(max_concurrent)
        self.min_interval = RATE_WINDOW / rpm  # seconds between requests (65s/25 = 2.6s)
        self.last_request_time = 0.0
        self.lock = threading.Lock()
        logger.debug(f"RateLimiter initialized: rpm={rpm}, max_concurrent={max_concurrent}")

    def acquire(self):
        """Acquire permission to make a request. Blocks if rate limit exceeded."""
        self.semaphore.acquire()
        with self.lock:
            now = time.time()
            elapsed = now - self.last_request_time
            if elapsed < self.min_interval:
                wait_time = self.min_interval - elapsed
                logger.debug(f"Rate limiter: waiting {wait_time:.2f}s")
                time.sleep(wait_time)
            self.last_request_time = time.time()

    def release(self):
        """Release the semaphore after request completes."""
        self.semaphore.release()


# Global singleton rate limiter - shared across ALL jobs
_global_rate_limiter = None
_rate_limiter_lock = threading.Lock()


def get_rate_limiter():
    """Get or create the global rate limiter (singleton)."""
    global _global_rate_limiter
    with _rate_limiter_lock:
        if _global_rate_limiter is None:
            _global_rate_limiter = RateLimiter(rpm=RPM_LIMIT, max_concurrent=MAX_CONCURRENT)
            logger.info(f"Created global RateLimiter: rpm={RPM_LIMIT}, concurrent={MAX_CONCURRENT}")
        return _global_rate_limiter


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


def _get_scene_with_real_products(
    scene_description: str,
    product_category: str,
    product_description: str
) -> str:
    """
    Enhance scene description with real products from Google Search.

    Args:
        scene_description: Original scene description from analysis
        product_category: Category like "skincare", "sleep aids", "wellness"
        product_description: User's product description (to determine category)

    Returns:
        Enhanced scene description with real product names
    """
    # Detect category from scene and product description
    scene_lower = scene_description.lower()
    prod_lower = product_description.lower()

    # Determine scene type from description
    if any(word in scene_lower for word in ['bathroom', 'vanity', 'sink', 'mirror']):
        scene_type = "bathroom vanity"
    elif any(word in scene_lower for word in ['bed', 'bedroom', 'nightstand', 'pillow']):
        scene_type = "bedroom nightstand"
    elif any(word in scene_lower for word in ['kitchen', 'counter', 'morning']):
        scene_type = "morning routine"
    elif any(word in scene_lower for word in ['desk', 'office', 'work']):
        scene_type = "desk setup"
    else:
        scene_type = "lifestyle"

    # Determine product category
    if any(word in prod_lower for word in ['skin', 'face', 'serum', 'cream', 'cleanser', 'moistur']):
        category = "skincare"
    elif any(word in prod_lower for word in ['sleep', 'eye mask', 'pillow', 'night', 'rest']):
        category = "sleep and relaxation"
    elif any(word in prod_lower for word in ['hair', 'shampoo', 'conditioner']):
        category = "haircare"
    elif any(word in prod_lower for word in ['makeup', 'lipstick', 'mascara', 'foundation']):
        category = "makeup and beauty"
    elif any(word in prod_lower for word in ['supplement', 'vitamin', 'wellness']):
        category = "wellness supplements"
    else:
        category = "beauty and self-care"

    # Get real products via grounding
    real_products = _get_real_products_for_scene(category, scene_type)

    if not real_products:
        return scene_description

    # Enhance the scene description
    enhanced = f"""{scene_description}

REAL PRODUCTS TO INCLUDE IN SCENE (use 2-3 of these recognizable items):
{real_products}

These are REAL products that TikTok users will recognize. Include them naturally in the scene
(on the counter, shelf, or visible in background) to make it look authentic."""

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
    hook_text_var: int = 1,
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
TASK 1: UNDERSTAND THE ORIGINAL SLIDESHOW
═══════════════════════════════════════════════════════════════

Identify what TYPE of slideshow this is:

TYPE A - "Tips/Habits List" (most common)
- Hook: "X things I do to..." / "habits that changed my life"
- Body: List of tips (each slide = one tip)
- CTA: "share yours" / engagement question (optional)
- Example: "simple things I do to make my hair 10x better"

TYPE B - "Transformation/Journey"
- Hook: Before state or problem
- Body: Steps or changes made
- CTA: Results or encouragement
- Example: "things I fixed to look prettier"

TYPE C - "Routine/Day in Life"
- Hook: "my morning routine" / "romanticizing my..."
- Body: Sequential steps of routine
- CTA: Reflection or question
- Example: "romanticizing my night routine"

TYPE D - "Product Roundup/Favorites"
- Hook: "my holy grails" / "products I swear by"
- Body: Multiple products shown
- CTA: "what's yours?"
- Example: "high maintenance habits worth every penny"

TYPE E - "Affirmation/Motivation"
- Hook: Emotional statement
- Body: Lifestyle aspirations
- CTA: Encouragement
- Example: "the 'boring' generation that stays in"

TYPE F - "Other/Mixed"
- Doesn't fit above patterns
- Describe what it actually is

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
TASK 3: DETECT PERSONA USAGE
═══════════════════════════════════════════════════════════════

Check each slide: Does it show a PERSON (face, body, selfie)?

Track:
- persona_gender: "female" | "male" | "none" | "mixed"
- persona_slides: list of slide indices that show a person

⚠️ IMPORTANT FOR NEW SLIDESHOW GENERATION:
- Hook slide: CAN have persona (if original does)
- Body slides: MUST have has_persona: false (product/aesthetic shots only)
- Product slide: MUST have has_persona: false (focus on product)
- CTA slide: Can have persona OR be text-only

Viral TikTok slideshows typically show the creator in hook/CTA only.
Body slides should be aesthetic product/lifestyle shots WITHOUT faces.

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
TASK 5: FIND OPTIMAL PRODUCT INSERTION POINT
═══════════════════════════════════════════════════════════════

RULE: Insert product in EXACTLY ONE slide. Never multiple.

STRATEGY BY TYPE:

IF Type A (Tips List):
- Find a tip that RELATES to product category
- Replace that tip with product-as-tip
- If no tip relates, insert at middle-to-late position
- Product becomes "one of the tips"

IF Type B (Transformation):
- Insert product as "the solution" or "game changer" step
- Position: after problem slides, before results
- Product becomes "what helped me transform"

IF Type C (Routine):
- Insert product as "one step in my routine"
- Position: where it naturally fits the routine flow
- Product becomes "part of how I romanticize my life"

IF Type D (Product Roundup):
- Replace ONE existing product with user's product
- Keep same position and framing
- Product becomes "one of my favorites"

IF Type E (Affirmation) or Type F (Other):
- FALLBACK: Insert product at middle-late position
- Frame as "small thing that makes a difference"
- Keep it subtle - one casual mention that fits the vibe

POSITION RULES (all types):
- NEVER slide 0 (hook) or slide 1 (too early)
- NEVER the last slide if it's a CTA
- IDEAL: Middle-to-late position (slide 3 to {num_slides - 2})
- Product slide should feel like it BELONGS, not interrupts

═══════════════════════════════════════════════════════════════
TASK 5: CREATE A COMPLETELY NEW SLIDESHOW (NOT A COPY!)
═══════════════════════════════════════════════════════════════

CRITICAL: Do NOT recreate or copy the original scenes!
The reference images are ONLY for visual style (font, colors, layout).
Create ENTIRELY NEW and FRESH content that fits the user's product.

THINK LIKE A CONTENT CREATOR:
- What tips would actually help someone in this category?
- What lifestyle moments relate to this product?
- What would make Gen-Z actually save this post?

TEXT RULES FOR ALL SLIDES:
- NEVER end sentences with "."
- Use "!" only if it fits the vibe, otherwise no punctuation
- Emojis are encouraged ✨⚡💫
- NEVER repeat the same text twice in one image (no duplicate lines!)
- Keep text SHORT - max 2 lines, each line under 6 words

HOOK SLIDE (slide 0):
- Create a NEW attention-grabbing hook for the product category
- Use same STYLE (provocative, listicle, relatable) but DIFFERENT content
- Be creative! Don't just replace one word in the original hook.

BODY SLIDES (tips/steps that are NOT product):
- INVENT completely NEW tips relevant to the product's category
- DO NOT copy or recreate the original tips!!!
- Think: What are 5-6 DIFFERENT tips a creator would give about this topic?
- Each tip should be UNIQUE and valuable on its own
- Mix up the scenes: morning moments, nighttime, self-care, lifestyle activities
- Example categories of tips to inspire variety:
  - Environment/setting tips ("keep your bedroom cool")
  - Habit tips ("no phone 30 mins before bed")
  - Product-adjacent tips ("silk pillowcase")
  - Mindset tips ("journal your thoughts")
  - Routine tips ("stretch for 5 mins")

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
TASK 6: GENERATE TEXT VARIATIONS
═══════════════════════════════════════════════════════════════

Generate MULTIPLE text alternatives for each slide type:
- Hook slide: Generate {hook_text_var} different hook text variations
- Body slides: Generate {body_text_var} different text variations per slide
- Product slide: Generate {product_text_var} different product pitch variations

VARIATION RULES:
- Each text variation should convey the SAME message but with DIFFERENT wording
- Vary emoji usage, sentence structure, and tone slightly
- All variations must feel authentic to the slideshow style
- Keep the same general length and vibe

EXAMPLES:
Hook variations:
  1. "simple things I do to sleep 10x better 😴"
  2. "my bedtime secrets for the best sleep ever ✨"
  3. "habits that completely changed my sleep game"

Body variations:
  1. "keep the room cold 🥶 under 68°F hits different"
  2. "cold room = better sleep! I keep mine at 65°F"
  3. "the secret? a freezing cold bedroom ❄️"

Product variations (must ALL include brand name and end with natural CTA!):
  1. "obsessed with my lumidew steam mask! i got mine from amazon ✨"
  2. "this lumidew mask is a game changer, found mine on amazon!"
  3. "lumidew steam masks before bed = best sleep! amazon has them"

OUTPUT: Use "text_variations" array instead of single "text_content":
{{
    "text_variations": ["option 1", "option 2", "option 3"]
}}

SCENE DIVERSITY REQUIREMENT:
Make sure each body slide shows a DIFFERENT type of scene:
- NOT all in bathroom
- NOT all applying products
- Mix indoor/outdoor, morning/night, active/relaxed moments

═══════════════════════════════════════════════════════════════
OUTPUT FORMAT
═══════════════════════════════════════════════════════════════

Return ONLY valid JSON:

{{
    "slideshow_type": "A" | "B" | "C" | "D" | "E" | "F",
    "slideshow_type_name": "Tips List | Transformation | Routine | Product Roundup | Affirmation | Other",
    
    "original_analysis": {{
        "topic": "what the original is about",
        "hook_angle": "how it grabs attention",
        "mood": "aesthetic vibe in 2-3 words",
        "persona_gender": "female | male | none | mixed",
        "persona_slides": [0, 2, 5]
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
        "total_slides": {num_slides},
        "hook_index": 0,
        "body_indices": [1, 2, 3, 5],
        "product_index": 4,
        "cta_index": 6 or null
    }},
    
    "new_slides": [
        {{
            "slide_index": 0,
            "slide_type": "hook",
            "reference_image_index": 0,
            "has_persona": true,
            "new_scene_description": "COMPLETELY NEW scene - different from original!",
            "text_variations": ["hook text option 1", "hook text option 2"],
            "text_position_hint": "where text goes, what NOT to cover"
        }},
        {{
            "slide_index": 4,
            "slide_type": "product",
            "reference_image_index": 4,
            "has_persona": false,
            "new_scene_description": "User's product in lifestyle context",
            "text_variations": [
                "steam eye mask before bed\\n\\ntotal game changer! lumidew masks are my fave ✨ got them on amazon",
                "use a steam mask\\n\\nobsessed with my lumidew mask from amazon! so relaxing before bed"
            ],
            "text_position_hint": "text at top, DO NOT cover product"
        }}
    ]
}}

IMPORTANT - reference_image_index explained:
- This tells us which ORIGINAL slide to use as STYLE reference
- STYLE = font, colors, text box design, layout
- STYLE ≠ scene content! The scene should be COMPLETELY DIFFERENT!

CRITICAL RULES:
1. Exactly {num_slides} slides in new_slides array
2. Exactly ONE slide with slide_type="product"
3. Product slide text_variations: short tip line + casual recommendation (separated by \\n\\n)
4. All other slides are hook, body, or cta
5. text_variations must be an ARRAY of text options (count based on slide type)
6. has_persona must be true/false for each slide
7. cta_index is null if original has no CTA slide
8. Hook needs {hook_text_var} text variations, body needs {body_text_var}, product needs {product_text_var}
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
            contents=contents
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
        if len(analysis['new_slides']) != num_slides:
            log.error(f"Slide count mismatch: expected {num_slides}, got {len(analysis['new_slides'])}")
            raise GeminiServiceError(f"Expected {num_slides} slides, got {len(analysis['new_slides'])}")

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

DO NOT GENERATE:
- Surreal, abstract, or "obviously AI" aesthetics
- Distorted objects, text, or proportions
- Unnatural color combinations or lighting
- Blurry or low-quality appearance
- Cluttered or chaotic compositions
- Floating objects or impossible physics
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

        # Add variation instruction for versions > 1
        variation_instruction = ""
        if version > 1:
            variation_instruction = f"""
VARIATION #{version}: Create a DIFFERENT visual interpretation:
- Use a different camera angle or perspective
- Change the pose or body position
- Adjust the lighting or mood slightly
- Keep the same text and message, but make the image visually distinct
"""

        if has_persona and persona_reference_path:
            # With persona - need consistency
            prompt = f"""Generate a TikTok {slide_label} slide.

{text_style_instruction}
{variation_instruction}
[STYLE_REFERENCE] - Reference slide for visual composition and mood.

[PERSONA_REFERENCE] - Person to use. Generate the EXACT SAME PERSON in a new scene:
- SAME face, hair color, skin tone, facial features
- SAME body type and general appearance
- DIFFERENT clothing appropriate for this scene context
- The outfit should match the situation (casual at home, dressed for going out, workout clothes for gym, etc.)
- This must look like the same creator, just in different clothes

NEW SCENE: {scene_description}

TEXT TO DISPLAY:
{text_content}

LAYOUT: {text_position_hint}

CRITICAL TEXT PLACEMENT RULES:
- NEVER cover face or person with text
- NEVER cover main objects/products with text
- Text should be in empty/background areas only
- If unsure, place text at TOP or BOTTOM edges of image

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
(Do NOT copy the person - create a NEW person)

CREATE A NEW PERSONA:
- Attractive, relatable TikTok content creator
- Natural, authentic appearance
- Clothing appropriate for this scene context
- Will be used as reference for other slides

NEW SCENE: {scene_description}

TEXT TO DISPLAY:
{text_content}

LAYOUT: {text_position_hint}

CRITICAL TEXT PLACEMENT RULES:
- NEVER cover face or person with text
- NEVER cover main objects/products with text
- Text should be in empty/background areas only
- If unsure, place text at TOP or BOTTOM edges of image

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
            # Enhance scene description with real products from Google Search
            enhanced_scene = scene_description
            if product_description and slide_type == 'body':
                enhanced_scene = _get_scene_with_real_products(
                    scene_description,
                    "",  # Category will be auto-detected
                    product_description
                )

            prompt = f"""Generate a TikTok {slide_label} slide.

{text_style_instruction}
{variation_instruction}
[STYLE_REFERENCE] - Reference slide for visual composition and mood.

NEW SCENE: {enhanced_scene}

TEXT TO DISPLAY:
{text_content}

LAYOUT: {text_position_hint}

CRITICAL TEXT PLACEMENT RULES:
- NEVER cover main objects/products with text
- Text should be in empty/background areas only
- If unsure, place text at TOP or BOTTOM edges of image
- Main subject must be completely unobstructed

IMPORTANT: Do NOT include any human faces, hands, body parts, or people in this image.

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

    # Retry logic with validation
    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            response = client.models.generate_content(
                model=IMAGE_MODEL,
                contents=contents,
                config=types.GenerateContentConfig(
                    response_modalities=['image', 'text'],
                    image_config=types.ImageConfig(
                        aspect_ratio="3:4",
                        image_size="4K"  # 4096px for better text quality
                    )
                )
            )

            # Extract generated image
            if not response.parts:
                raise GeminiServiceError('Empty response from Gemini - content may have been blocked')
            for part in response.parts:
                if hasattr(part, 'inline_data') and part.inline_data:
                    with open(output_path, 'wb') as f:
                        f.write(part.inline_data.data)

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
            error_str = str(e)

            # Check for rate limit error and extract retry delay
            if '429' in error_str or 'RESOURCE_EXHAUSTED' in error_str:
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

        # Get text variations from analysis (or fallback to single text_content)
        text_variations = slide.get('text_variations', [])
        if not text_variations:
            # Fallback: use text_content if text_variations not provided
            text_content = slide.get('text_content', '')
            text_variations = [text_content] if text_content else ['']

        # Determine photo variations and slide key
        if slide_type == 'hook':
            photo_vars = hook_photo_var
            slide_key = 'hook'
        elif slide_type == 'product':
            # Product photo variations = number of uploaded product images
            photo_vars = len(product_image_paths)
            slide_key = 'product'
        else:  # body
            photo_vars = body_photo_var
            body_num = sum(1 for s in new_slides[:idx] if s['slide_type'] == 'body') + 1
            slide_key = f'body_{body_num}'

        # Initialize variations list for this slide
        if slide_key not in variations_structure:
            variations_structure[slide_key] = []

        # Create photo × text matrix of tasks
        for p_idx in range(photo_vars):
            for t_idx, text_content in enumerate(text_variations):
                photo_ver = p_idx + 1  # 1-indexed
                text_ver = t_idx + 1   # 1-indexed

                # Determine output filename with photo and text version
                output_path = os.path.join(output_dir, f'{slide_key}_p{photo_ver}_t{text_ver}.png')

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
                    'scene_description': slide.get('new_scene_description', ''),
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
                img_cropped.save(dst_path, 'PNG')

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
    product_total = len(product_image_paths) * product_text_var
    total_estimate = hook_total + body_total + product_total

    log.info(f"Starting pipeline: {len(slide_paths)} slides, ~{total_estimate} total images, preset={preset_id}")
    log.debug(f"Photo vars: hook={hook_photo_var}, body={body_photo_var}, product={len(product_image_paths)}")
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

    # Step 1: Analyze and plan (with text variation counts)
    log.info("Step 1/2: Analyzing slideshow")
    analysis = analyze_and_plan(
        slide_paths,
        product_image_paths,
        product_description,
        output_dir,
        hook_text_var=hook_text_var,
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
                parts = filename.replace('.png', '').split('_')

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


# For testing
if __name__ == '__main__':
    print('Gemini Service V2 - Redesigned Pipeline')
    print(f'Analysis Model: {ANALYSIS_MODEL}')
    print(f'Image Model: {IMAGE_MODEL}')
    print(f'API Key configured: {bool(os.getenv("GEMINI_API_KEY"))}')
    print()
    print('Changes from V1:')
    print('- Smart product insertion (6 slideshow types)')
    print('- ICP/audience mapping for product positioning')
    print('- Persona consistency across slides')
    print('- Clear image labeling (STYLE_REFERENCE, PERSONA_REFERENCE, PRODUCT_PHOTO)')
    print('- Exact text content generation (not just descriptions)')
    print('- Optional CTA detection')