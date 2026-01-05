"""
Gemini Service V2 - Redesigned Pipeline
Single analysis call + parallel image generation
With persona consistency and smart product insertion
"""
import os
import json
import base64
import time
import threading
from typing import Optional, Callable
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from dotenv import load_dotenv

load_dotenv()

from google import genai
from google.genai import types

GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')

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

    def acquire(self):
        """Acquire permission to make a request. Blocks if rate limit exceeded."""
        self.semaphore.acquire()
        with self.lock:
            now = time.time()
            elapsed = now - self.last_request_time
            if elapsed < self.min_interval:
                time.sleep(self.min_interval - elapsed)
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
    if not GEMINI_API_KEY:
        raise GeminiServiceError('GEMINI_API_KEY environment variable not set')
    return genai.Client(
        api_key=GEMINI_API_KEY,
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


def analyze_and_plan(
    slide_paths: list[str],
    product_image_path: str,
    product_description: str,
    output_dir: str
) -> dict:
    """
    Single API call to analyze ALL slides and create new story plan.
    
    Detects slideshow type, identifies target audience, finds optimal
    product insertion point, and generates complete slide plan.
    """
    client = _get_client()
    num_slides = len(slide_paths)

    prompt = f"""You are analyzing a viral TikTok slideshow to recreate it with a product insertion.

USER'S PRODUCT: {product_description}

There are {num_slides} slides in this slideshow. Analyze them ALL.

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

This is critical for consistency - all persona slides must show similar person.

═══════════════════════════════════════════════════════════════
TASK 4: FIND OPTIMAL PRODUCT INSERTION POINT
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
TASK 5: CREATE NEW SLIDESHOW PLAN
═══════════════════════════════════════════════════════════════

For EVERY slide, specify:

TEXT RULES FOR ALL SLIDES:
- NEVER end sentences with "."
- Use "!" only if it fits the vibe, otherwise no punctuation
- Emojis are encouraged ✨⚡💫

HOOK SLIDE (slide 0):
- Adapt hook to relate to user's product category
- Keep the SAME hook style/angle as original
- Example: "hair tips" → "sleep tips" if product is steam eye mask

BODY SLIDES (tips/steps that are NOT product):
- Create REAL valuable content (not about product)
- Must fit the category and audience
- These tips should SURROUND the product naturally

PRODUCT SLIDE (exactly ONE):
- Frame as tip/recommendation, NOT advertisement
- Header: Action-based tip (e.g., "steam eye mask before bed")
- Body text: casual, conversational
- TEXT RULES:
  - NEVER end sentences with "."
  - Use "!" only if it fits the vibe, otherwise no punctuation
  - Emojis are encouraged ✨⚡💫
- Example: "total game changer for my sleep! I keep lumidew masks on my nightstand, they warm up on their own and feel like a cozy spa moment ✨ got them on amazon"

CTA SLIDE (only if original has one):
- Keep engagement style
- Adapt question to new category
- If original doesn't have CTA, don't add one

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
            "new_scene_description": "describe the image to generate",
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
        response = client.models.generate_content(
            model=ANALYSIS_MODEL,
            contents=contents
        )
        result_text = response.text

        # Parse JSON
        start = result_text.find('{')
        end = result_text.rfind('}') + 1
        if start >= 0 and end > start:
            analysis = json.loads(result_text[start:end])
        else:
            raise GeminiServiceError('No valid JSON in response')

        # Validate structure
        if 'new_slides' not in analysis:
            raise GeminiServiceError('Missing new_slides in analysis')
        if len(analysis['new_slides']) != num_slides:
            raise GeminiServiceError(f"Expected {num_slides} slides, got {len(analysis['new_slides'])}")
        
        # Validate exactly one product slide
        product_slides = [s for s in analysis['new_slides'] if s.get('slide_type') == 'product']
        if len(product_slides) != 1:
            raise GeminiServiceError(f"Expected exactly 1 product slide, got {len(product_slides)}")

        # Save analysis.json
        os.makedirs(output_dir, exist_ok=True)
        analysis_path = os.path.join(output_dir, 'analysis.json')
        with open(analysis_path, 'w') as f:
            json.dump(analysis, f, indent=2)

        return analysis

    except json.JSONDecodeError as e:
        raise GeminiServiceError(f'Failed to parse analysis JSON: {e}')
    except Exception as e:
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
    has_persona: bool = False
) -> str:
    """
    Generate a single image with clear image labeling.
    
    Image roles:
    - STYLE_REFERENCE: Copy text style, composition, mood, lighting
    - PERSONA_REFERENCE: Use this person's appearance for consistency
    - PRODUCT_PHOTO: User's product image (base for product slides)
    """
    
    if slide_type == 'product':
        # PRODUCT SLIDE: User's product photo + style reference
        prompt = f"""Generate a TikTok slide featuring a product AS A CASUAL TIP.

[PRODUCT_PHOTO] - User's product image. THIS IS THE BASE IMAGE - keep it as the main visual.

[STYLE_REFERENCE] - Reference slide. Copy text style EXACTLY as shown in reference:
- EXACT same font
- EXACT same color
- EXACT same style

TEXT TO ADD:
{text_content}

LAYOUT: {text_position_hint}
Product must remain FULLY VISIBLE.

GOAL: Look like "just another tip" - NOT an advertisement."""

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

[STYLE_REFERENCE] - Reference CTA slide. Copy EXACTLY as shown:
- EXACT same font
- EXACT same color
- EXACT same style
- EXACT same background

TEXT TO DISPLAY:
{text_content}

LAYOUT: {text_position_hint}"""

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

        if has_persona and persona_reference_path:
            # With persona - need consistency
            prompt = f"""Generate a TikTok {slide_label} slide.

[STYLE_REFERENCE] - Reference slide. Copy EXACTLY as shown:
- EXACT same font
- EXACT same color
- EXACT same style

[PERSONA_REFERENCE] - Person to use. Generate the EXACT SAME person in new scene:
- SAME person, SAME appearance
- This must look like the same creator

NEW SCENE: {scene_description}

TEXT TO DISPLAY:
{text_content}

LAYOUT: {text_position_hint}
Never cover face with text

IMPORTANT: Only ONE person in the image - never two people!"""

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
        else:
            # No persona - just style reference
            prompt = f"""Generate a TikTok {slide_label} slide.

[STYLE_REFERENCE] - Reference slide. Copy EXACTLY as shown:
- EXACT same font
- EXACT same color
- EXACT same style

NEW SCENE: {scene_description}

TEXT TO DISPLAY:
{text_content}

LAYOUT: {text_position_hint}

IMPORTANT: Only ONE person in the image - never two people!"""

            contents = [
                prompt,
                "[STYLE_REFERENCE]",
                types.Part.from_bytes(
                    data=_load_image_bytes(reference_image_path),
                    mime_type=_get_image_mime_type(reference_image_path)
                )
            ]

    # Retry logic
    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            response = client.models.generate_content(
                model=IMAGE_MODEL,
                contents=contents,
                config=types.GenerateContentConfig(
                    response_modalities=['image', 'text'],
                    image_config=types.ImageConfig(aspect_ratio="9:16")
                )
            )

            # Extract generated image
            for part in response.parts:
                if hasattr(part, 'inline_data') and part.inline_data:
                    with open(output_path, 'wb') as f:
                        f.write(part.inline_data.data)
                    return output_path

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
    product_variations: int = 1
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
        product_variations: Number of variations for product slide (default 1)

    Returns:
        dict with:
            - images: flat list of all generated image paths
            - variations: structured dict by slide type
    """
    client = _get_client()
    os.makedirs(output_dir, exist_ok=True)

    new_slides = analysis['new_slides']

    # Build all tasks with variations
    all_tasks = []
    variations_structure = {}  # Track variations by slide key

    for slide in new_slides:
        idx = slide['slide_index']
        ref_idx = slide.get('reference_image_index', idx)
        slide_type = slide['slide_type']
        has_persona = slide.get('has_persona', False)

        # Determine number of variations based on slide type
        if slide_type == 'hook':
            num_variations = hook_variations
            slide_key = 'hook'
        elif slide_type == 'product':
            num_variations = product_variations
            slide_key = 'product'
        elif slide_type == 'cta':
            num_variations = 1  # CTA always 1
            slide_key = 'cta'
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
            if slide_type == 'cta':
                output_path = os.path.join(output_dir, 'cta.png')
            else:
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
                    task['has_persona']
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
    product_variations: int = 1
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
        product_variations: Number of variations for product slide (default 1)

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
    if progress_callback:
        progress_callback('analyzing', 'Analyzing slideshow and planning new story...', 30)

    # Step 1: Analyze and plan
    analysis = analyze_and_plan(
        slide_paths,
        product_image_path,
        product_description,
        output_dir
    )

    if progress_callback:
        progress_callback('generating', 'Generating images...', 40)

    # Step 2: Generate all images with variations
    def image_progress(current, total, message):
        if progress_callback:
            percent = 40 + int(50 * current / total)
            progress_callback('generating', message, percent)

    generation_result = generate_all_images(
        analysis,
        slide_paths,
        product_image_path,
        output_dir,
        progress_callback=image_progress,
        hook_variations=hook_variations,
        body_variations=body_variations,
        product_variations=product_variations
    )

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
    print(f'API Key configured: {bool(GEMINI_API_KEY)}')
    print()
    print('Changes from V1:')
    print('- Smart product insertion (6 slideshow types)')
    print('- ICP/audience mapping for product positioning')
    print('- Persona consistency across slides')
    print('- Clear image labeling (STYLE_REFERENCE, PERSONA_REFERENCE, PRODUCT_PHOTO)')
    print('- Exact text content generation (not just descriptions)')
    print('- Optional CTA detection')