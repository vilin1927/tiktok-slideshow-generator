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

from google import genai
from google.genai import types

# Model names
ANALYSIS_MODEL = 'gemini-3-pro-preview'
IMAGE_MODEL = 'gemini-3-pro-image-preview'

# Rate limiting config
MAX_CONCURRENT = 10
RPM_LIMIT = 60
MAX_RETRIES = 3
REQUEST_TIMEOUT = 120  # seconds per API call

# Rate limiter using semaphore + delay
class RateLimiter:
    """
    Semaphore-based rate limiter that enforces RPM limits.
    Ensures delay BEFORE each request, not after.
    """
    def __init__(self, rpm: int = RPM_LIMIT, max_concurrent: int = MAX_CONCURRENT):
        self.semaphore = threading.Semaphore(max_concurrent)
        self.min_interval = 60.0 / rpm  # seconds between requests
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
    brand_name = keywords.get('brand_name', '')
    purchase_location = keywords.get('purchase_location', 'amazon')

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
    Manually inject missing keywords into product slide text.
    Last resort if AI doesn't include them.
    """
    keywords = analysis.get('required_keywords', {})
    brand = keywords.get('brand_name', '')
    location = keywords.get('purchase_location', 'amazon')

    for slide in analysis.get('new_slides', []):
        if slide.get('slide_type') == 'product':
            text = slide.get('text_content', '')
            original_text = text

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

            if text != original_text:
                slide['text_content'] = text
                logger.info(f"Injected keywords into product slide")

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


def analyze_and_plan(
    slide_paths: list[str],
    product_image_path: str,
    product_description: str,
    output_dir: str,
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

OUTPUT in "required_keywords":
- brand_name: [EXACT brand from description - MUST exist in text above]
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
- Header: Action-based tip (e.g., "steam eye mask before bed")
- Body text: casual, conversational

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

- Example with keywords naturally included:
  "total game changer for my sleep! I keep [brand_name] masks on my nightstand,
  they warm up on their own and feel like a cozy spa moment ✨ got them on [purchase_location]"

CTA SLIDE (only if original has one):
- Keep engagement style
- Adapt question to new category
- If original doesn't have CTA, don't add one

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
            "text_content": "exact text to display on slide",
            "text_position_hint": "where text goes, what NOT to cover"
        }},
        {{
            "slide_index": 4,
            "slide_type": "product",
            "reference_image_index": 4,
            "has_persona": false,
            "new_scene_description": "User's product in lifestyle context",
            "text_content": "Header: steam eye mask before bed\\n\\ntotal game changer for my sleep! I keep lumidew masks on my nightstand, they warm up on their own and feel like a cozy spa moment ✨ got them on amazon",
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
3. Product slide text_content must have Header + Body format
4. All other slides are hook, body, or cta
5. text_content must be the ACTUAL text to display
6. has_persona must be true/false for each slide
7. cta_index is null if original has no CTA slide
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

    # Add user's product image last
    contents.append("[USER'S PRODUCT IMAGE]")
    contents.append(types.Part.from_bytes(
        data=_load_image_bytes(product_image_path),
        mime_type=_get_image_mime_type(product_image_path)
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
    version: int = 1
) -> str:
    """
    Generate a single image with clear image labeling.

    Image roles:
    - STYLE_REFERENCE: Visual reference for composition, mood, lighting
    - PERSONA_REFERENCE: Use this person's appearance for consistency
    - PRODUCT_PHOTO: User's product image (base for product slides)

    Text style is passed explicitly via text_style dict for accurate font matching.
    """

    # Build text style instruction from analysis
    if text_style:
        font_style = text_style.get('font_style', 'modern-clean')
        style_description = _get_style_description(font_style)

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
- Background: {text_style.get('background_box', 'none')}

OVERALL VIBE: {text_style.get('visual_vibe', 'clean minimal')}
Position: {text_style.get('position_style', 'varies by slide')}

TEXT SIZE AESTHETIC (CRITICAL):
- Text should be SUBTLE and understated - think whispered, not shouted
- Headers: Small enough that the image dominates, text complements
- Body text: Compact, almost like Instagram story captions
- The image is the hero, text is the supporting actor
- Match the aesthetic of minimalist TikTok slideshows where text is an accent, not the focus
- Reference style: Text you'd need to tap to read closely, not text that screams at you
- Think "elegant magazine caption" not "billboard advertisement"

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
            prompt = f"""Generate a TikTok {slide_label} slide.

{text_style_instruction}
{variation_instruction}
[STYLE_REFERENCE] - Reference slide for visual composition and mood.

NEW SCENE: {scene_description}

TEXT TO DISPLAY:
{text_content}

LAYOUT: {text_position_hint}

CRITICAL TEXT PLACEMENT RULES:
- NEVER cover main objects/products with text
- Text should be in empty/background areas only
- If unsure, place text at TOP or BOTTOM edges of image
- Main subject must be completely unobstructed

IMPORTANT: Do NOT include any human faces, hands, body parts, or people in this image.
This MUST be a completely faceless composition showing ONLY:
- Products
- Objects
- Flat lays
- Aesthetic backgrounds
- Text overlays

If you generate a human face or body part, the image will be REJECTED.
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
            if attempt < MAX_RETRIES - 1:
                wait_time = (2 ** attempt) + 1
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
    product_image_path: str,
    output_dir: str,
    progress_callback: Optional[ImageProgressCallback] = None,
    hook_variations: int = 1,
    body_variations: int = 1,
    request_id: str = None
) -> dict:
    """
    Generate all images with persona consistency and variations support.

    Strategy:
    1. Generate first persona variation FIRST (creates the persona)
    2. Use that generated image as PERSONA_REFERENCE for all other persona slides/variations
    3. Run all remaining variations in parallel

    Args:
        analysis: Output from analyze_and_plan()
        slide_paths: List of original slide image paths
        product_image_path: Path to user's product image
        output_dir: Directory to save generated images
        progress_callback: Optional callback with signature (current, total, message)
        hook_variations: Number of variations for hook slide (default 1)
        body_variations: Number of variations per body slide (default 1)
        request_id: Optional request ID for logging

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

    # Build all tasks with variations
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

        # Determine number of variations based on slide type
        if slide_type == 'hook':
            num_variations = hook_variations
            slide_key = 'hook'
        elif slide_type == 'product':
            num_variations = 1  # Product always 1 variation
            slide_key = 'product'
        else:  # body
            num_variations = body_variations
            body_num = sum(1 for s in new_slides[:idx] if s['slide_type'] == 'body') + 1
            slide_key = f'body_{body_num}'

        # Initialize variations list for this slide
        if slide_key not in variations_structure:
            variations_structure[slide_key] = []

        # Create task for each variation
        for v in range(num_variations):
            version = v + 1  # 1-indexed

            # Determine output filename with version
            output_path = os.path.join(output_dir, f'{slide_key}_v{version}.png')

            task = {
                'task_id': f'{slide_key}_v{version}',
                'slide_index': idx,
                'slide_type': slide_type,
                'slide_key': slide_key,
                'version': version,
                'reference_image_path': slide_paths[ref_idx] if ref_idx < len(slide_paths) else slide_paths[0],
                'scene_description': slide.get('new_scene_description', ''),
                'text_content': slide.get('text_content', ''),
                'text_position_hint': slide.get('text_position_hint', ''),
                'output_path': output_path,
                'product_image_path': product_image_path if slide_type == 'product' else None,
                'has_persona': has_persona
            }
            all_tasks.append(task)

    total = len(all_tasks)

    # Separate persona tasks from non-persona tasks
    persona_tasks = [t for t in all_tasks if t['has_persona']]
    non_persona_tasks = [t for t in all_tasks if not t['has_persona']]

    log.info(f"Generation tasks: {total} total ({len(persona_tasks)} persona, {len(non_persona_tasks)} non-persona)")

    # Initialize rate limiter
    rate_limiter = RateLimiter(rpm=RPM_LIMIT, max_concurrent=MAX_CONCURRENT)

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
                    task['version']  # Pass version for variation diversity
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
    product_image_path: str,
    product_description: str,
    output_dir: str,
    progress_callback: Optional[PipelineProgressCallback] = None,
    hook_variations: int = 1,
    body_variations: int = 1,
    request_id: str = None
) -> dict:
    """
    Run the complete generation pipeline.

    Args:
        slide_paths: List of paths to scraped TikTok slide images
        product_image_path: Path to user's product image
        product_description: Text description of the product
        output_dir: Directory to save analysis and generated images
        progress_callback: Optional callback with signature (status, message, percent)
            - status: Current phase ('analyzing' | 'generating')
            - message: Human-readable progress message
            - percent: Progress percentage (0-100)
        hook_variations: Number of variations for hook slide (default 1)
        body_variations: Number of variations per body slide (default 1)
        request_id: Optional request ID for logging

    Returns:
        dict with keys:
            - analysis: Full analysis JSON from Gemini
            - generated_images: List of generated image paths (flat)
            - variations: Structured dict of variations by slide type
            - analysis_path: Path to saved analysis.json

    Steps:
        1. Analyze slideshow type, audience, find optimal product insertion
        2. Generate all images with persona consistency and variations
    """
    log = get_request_logger('gemini', request_id) if request_id else logger
    start_time = time.time()

    total_variations = hook_variations + (len(slide_paths) - 2) * body_variations + 1
    log.info(f"Starting pipeline: {len(slide_paths)} slides, ~{total_variations} total images")
    log.debug(f"Variations: hook={hook_variations}, body={body_variations}")

    if progress_callback:
        progress_callback('analyzing', 'Analyzing slideshow and planning new story...', 30)

    # Pre-extract brand from description for validation
    pre_extraction = _extract_brand_from_description(product_description)
    log.debug(f"Pre-extracted brand candidates: {pre_extraction}")

    # Step 1: Analyze and plan
    log.info("Step 1/2: Analyzing slideshow")
    analysis = analyze_and_plan(
        slide_paths,
        product_image_path,
        product_description,
        output_dir,
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
        product_image_path,
        output_dir,
        progress_callback=image_progress,
        hook_variations=hook_variations,
        body_variations=body_variations,
        request_id=request_id
    )

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