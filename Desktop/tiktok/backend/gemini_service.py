"""
Gemini API Service Module
Handles slide analysis and image generation using Google Gemini API
"""
import os
import base64
import time
from typing import Optional
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Try new genai client first, fallback to older SDK
try:
    from google import genai
    from google.genai import types
    USE_NEW_SDK = True
except ImportError:
    import google.generativeai as genai
    USE_NEW_SDK = False

GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')

# Model names
ANALYSIS_MODEL = 'gemini-3-pro-preview'
IMAGE_MODEL = 'gemini-3-pro-image-preview'


class GeminiServiceError(Exception):
    """Custom exception for Gemini API errors"""
    pass


def _get_client():
    """Initialize and return Gemini client"""
    if not GEMINI_API_KEY:
        raise GeminiServiceError('GEMINI_API_KEY environment variable not set')

    if USE_NEW_SDK:
        return genai.Client(api_key=GEMINI_API_KEY)
    else:
        genai.configure(api_key=GEMINI_API_KEY)
        return genai


def _load_image_as_base64(image_path: str) -> str:
    """Load image file and convert to base64"""
    with open(image_path, 'rb') as f:
        return base64.b64encode(f.read()).decode('utf-8')


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


def analyze_slides(image_paths: list[str]) -> dict:
    """
    Analyze slideshow images and categorize them into hook/body/product
    with detailed style extraction for generation.

    Args:
        image_paths: List of paths to slide images

    Returns:
        dict with categorized slides and detailed style info
    """
    if not image_paths:
        raise GeminiServiceError('No images provided for analysis')

    client = _get_client()

    # Prepare images for analysis
    image_parts = []
    for path in image_paths:
        image_data = _load_image_as_base64(path)
        mime_type = _get_image_mime_type(path)
        image_parts.append({
            'path': path,
            'data': image_data,
            'mime_type': mime_type
        })

    # Detailed analysis prompt
    num_slides = len(image_paths)
    prompt = f"""Analyze this TikTok slideshow completely. There are {num_slides} slides.

PART A - OVERALL CONTEXT:
- What product/topic is this slideshow about?
- Who is the target audience?
- What is the hook/angle? (transformation, tips, routine, etc.)
- What emotion does it evoke?

PART B - FOR EACH SLIDE (analyze all {num_slides} slides):

For each slide provide:
1. SLIDE TYPE: (hook/body/product)
   - "hook" = first attention-grabbing slide
   - "body" = informational/benefit slides
   - "product" = CTA slide like "buy at amazon", "link in bio", or strongest product endorsement

2. TEXT STYLE (extract precisely):
   - Background: (color, opacity, shape - e.g. "white rounded rectangle 90% opacity")
   - Text color: (exact - e.g. "black #000000" or "white #FFFFFF")
   - Font weight: (bold/semibold/regular)
   - Font style: (serif/sans-serif, approximate font name if recognizable)
   - Text box shape: (rounded rectangle/pill/plain/none)
   - Border: (none / thin black / etc.)
   - Shadow: (none / subtle drop shadow / etc.)
   - Position: (top-center / center / bottom-center / etc.)
   - Text alignment: (center/left/right)

3. TEXT CONTENT:
   - Exact text visible on slide
   - Text type: (hook question, tip, benefit, product name, CTA, etc.)
   - Number of lines
   - Approximate character count per line

4. IMAGE STYLE:
   - Subject: (selfie/product/lifestyle/hands/flat-lay/etc.)
   - Composition: (close-up/medium/full)
   - Lighting: (soft natural/ring light/studio/bright/moody)
   - Colors: (warm/cool/neutral, list dominant colors)
   - Background: (solid color/blurred/room/outdoor/gradient)
   - Filters: (none/warm filter/cool filter/high contrast/soft/etc.)
   - Mood: (aesthetic vibe in 2-3 words)

PART C - SLIDESHOW STRUCTURE SUMMARY:
- Which slide number is the HOOK? (usually slide 1)
- Which slide numbers are BODY? (middle slides with tips/benefits)
- Which slide number is the PRODUCT/CTA? (the one with buy link, "link in bio", or strongest endorsement - NOT always the last slide!)

Return as JSON:
{{
    "overall_context": {{
        "product_topic": "...",
        "target_audience": "...",
        "hook_angle": "...",
        "emotion": "..."
    }},
    "slides": [
        {{
            "index": 0,
            "type": "hook|body|product",
            "text_style": {{
                "background": "...",
                "text_color": "...",
                "font_weight": "...",
                "font_style": "...",
                "text_box_shape": "...",
                "border": "...",
                "shadow": "...",
                "position": "...",
                "alignment": "..."
            }},
            "text_content": {{
                "exact_text": "...",
                "text_type": "...",
                "num_lines": 1,
                "chars_per_line": 20
            }},
            "image_style": {{
                "subject": "...",
                "composition": "...",
                "lighting": "...",
                "colors": ["..."],
                "background": "...",
                "filters": "...",
                "mood": "..."
            }}
        }}
    ],
    "structure": {{
        "hook_slide": 0,
        "body_slides": [1, 2, 3, 4],
        "product_slide": 5
    }}
}}"""

    try:
        if USE_NEW_SDK:
            # Build content with images
            contents = [prompt]
            for img in image_parts:
                contents.append(types.Part.from_bytes(
                    data=base64.b64decode(img['data']),
                    mime_type=img['mime_type']
                ))

            response = client.models.generate_content(
                model=ANALYSIS_MODEL,
                contents=contents
            )
            result_text = response.text
        else:
            # Old SDK approach
            model = client.GenerativeModel(ANALYSIS_MODEL)
            contents = [prompt]
            for img in image_parts:
                contents.append({
                    'mime_type': img['mime_type'],
                    'data': img['data']
                })
            response = model.generate_content(contents)
            result_text = response.text

        # Parse JSON response
        import json
        try:
            # Find JSON in response
            start = result_text.find('{')
            end = result_text.rfind('}') + 1
            if start >= 0 and end > start:
                analysis = json.loads(result_text[start:end])
            else:
                raise ValueError('No JSON found')
        except json.JSONDecodeError:
            # If parsing fails, create basic structure
            analysis = {'slides': [], 'structure': None}

        # Categorize slides using AI's analysis
        result = {
            'hook': [],
            'body': [],
            'product': [],
            'analysis': analysis
        }

        # Use AI-detected structure if available
        structure = analysis.get('structure')
        if structure:
            hook_idx = structure.get('hook_slide', 0)
            body_idxs = structure.get('body_slides', [])
            product_idx = structure.get('product_slide')

            if hook_idx is not None and hook_idx < len(image_paths):
                result['hook'] = [image_paths[hook_idx]]

            for idx in body_idxs:
                if idx < len(image_paths):
                    result['body'].append(image_paths[idx])

            if product_idx is not None and product_idx < len(image_paths):
                result['product'] = [image_paths[product_idx]]
        else:
            # Fallback: use slide-by-slide type detection
            slides_info = analysis.get('slides', [])
            for slide_info in slides_info:
                idx = slide_info.get('index', 0)
                slide_type = slide_info.get('type', 'body').lower()

                if idx < len(image_paths):
                    if slide_type == 'hook' and not result['hook']:
                        result['hook'] = [image_paths[idx]]
                    elif slide_type == 'product' and not result['product']:
                        result['product'] = [image_paths[idx]]
                    else:
                        result['body'].append(image_paths[idx])

            # Final fallback if still empty
            if not result['hook'] and image_paths:
                result['hook'] = [image_paths[0]]
            if not result['product'] and len(image_paths) > 1:
                result['product'] = [image_paths[-1]]
            if not result['body'] and len(image_paths) > 2:
                result['body'] = image_paths[1:-1]

        return result

    except Exception as e:
        raise GeminiServiceError(f'Analysis failed: {str(e)}')


def generate_styled_image(
    reference_image_path: str,
    product_context: str,
    slide_type: str = 'body',
    output_path: Optional[str] = None,
    style_info: Optional[dict] = None
) -> str:
    """
    Generate a new image matching the style of the reference image

    Args:
        reference_image_path: Path to the reference viral slide image
        product_context: Description of the product to feature
        slide_type: Type of slide (hook, body, product)
        output_path: Where to save the generated image
        style_info: Detailed style analysis from analyze_slides()

    Returns:
        Path to the generated image
    """
    client = _get_client()

    # Load reference image
    ref_data = _load_image_as_base64(reference_image_path)
    ref_mime = _get_image_mime_type(reference_image_path)

    # Build style instructions from analysis
    style_instructions = ""
    if style_info:
        text_style = style_info.get('text_style', {})
        image_style = style_info.get('image_style', {})
        text_content = style_info.get('text_content', {})

        style_instructions = f"""
EXACT TEXT STYLE TO REPLICATE:
- Text box: {text_style.get('background', 'white rounded rectangle')}
- Text color: {text_style.get('text_color', 'black')}
- Font: {text_style.get('font_weight', 'bold')} {text_style.get('font_style', 'sans-serif')}
- Position: {text_style.get('position', 'center')}
- Alignment: {text_style.get('alignment', 'center')}
- Border: {text_style.get('border', 'none')}
- Shadow: {text_style.get('shadow', 'none')}

TEXT FORMAT:
- Number of lines: {text_content.get('num_lines', 2)}
- Characters per line: ~{text_content.get('chars_per_line', 25)}
- Text type: {text_content.get('text_type', 'benefit statement')}

IMAGE STYLE:
- Subject type: {image_style.get('subject', 'lifestyle')}
- Composition: {image_style.get('composition', 'medium shot')}
- Lighting: {image_style.get('lighting', 'soft natural')}
- Colors: {', '.join(image_style.get('colors', ['neutral']))}
- Background: {image_style.get('background', 'blurred')}
- Filter/mood: {image_style.get('filters', 'none')}, {image_style.get('mood', 'aesthetic')}
"""

    # Create generation prompt based on slide type
    if slide_type == 'hook':
        prompt = f"""Generate a TikTok slideshow HOOK slide for: {product_context}

Look at the reference image and create a NEW image that matches its style EXACTLY.
{style_instructions}

The hook must:
- Grab attention immediately
- Create curiosity to swipe
- Match the exact text box style, font look, and positioning from reference
- Use similar image composition and aesthetic

Generate compelling hook text for the product and place it exactly like the reference."""

    elif slide_type == 'product':
        prompt = f"""Generate a TikTok slideshow PRODUCT/CTA slide for: {product_context}

Look at the reference image and create a NEW image that matches its style EXACTLY.
{style_instructions}

The product slide must:
- Showcase the product with a clear CTA
- Match the exact text overlay style from reference
- Use same text box shape, colors, and positioning
- Create urgency or desire to purchase

Generate CTA text like "Shop now", "Link in bio", or product name with benefit."""

    else:  # body
        prompt = f"""Generate a TikTok slideshow BODY slide for: {product_context}

Look at the reference image and create a NEW image that matches its style EXACTLY.
{style_instructions}

The body slide must:
- Provide a benefit, tip, or feature about the product
- Match the exact text box style, font, and positioning from reference
- Use same image aesthetic and composition style
- Be engaging and informative

Generate one clear benefit or tip and place text exactly like reference."""

    try:
        if USE_NEW_SDK:
            # New SDK with image input
            contents = [
                prompt,
                types.Part.from_bytes(
                    data=base64.b64decode(ref_data),
                    mime_type=ref_mime
                )
            ]

            response = client.models.generate_content(
                model=IMAGE_MODEL,
                contents=contents,
                config=types.GenerateContentConfig(
                    response_modalities=['image', 'text']
                )
            )

            # Extract generated image
            for part in response.parts:
                if hasattr(part, 'inline_data') and part.inline_data:
                    image_data = part.inline_data.data
                    if output_path:
                        with open(output_path, 'wb') as f:
                            f.write(image_data)
                        return output_path

            raise GeminiServiceError('No image generated in response')

        else:
            # Old SDK - may not support image generation the same way
            raise GeminiServiceError('Image generation requires newer Gemini SDK')

    except Exception as e:
        raise GeminiServiceError(f'Image generation failed: {str(e)}')


def generate_hook_slide(
    reference_image_path: str,
    product_context: str,
    variation_count: int = 1,
    output_dir: str = 'temp/generated',
    style_info: Optional[dict] = None
) -> list[str]:
    """
    Generate hook slide variations

    Args:
        reference_image_path: Path to reference hook slide
        product_context: Product description
        variation_count: Number of variations to generate (1-5)
        output_dir: Directory for output images
        style_info: Style analysis for this slide from analyze_slides()

    Returns:
        List of paths to generated images
    """
    os.makedirs(output_dir, exist_ok=True)
    generated = []

    for i in range(min(variation_count, 5)):
        output_path = os.path.join(output_dir, f'hook_v{i+1}.png')
        try:
            result = generate_styled_image(
                reference_image_path,
                product_context,
                slide_type='hook',
                output_path=output_path,
                style_info=style_info
            )
            generated.append(result)
            time.sleep(1)  # Rate limiting
        except GeminiServiceError as e:
            print(f'Warning: Hook variation {i+1} failed: {e}')

    return generated


def generate_body_slides(
    reference_image_paths: list[str],
    product_context: str,
    variation_count: int = 1,
    output_dir: str = 'temp/generated',
    style_infos: Optional[list[dict]] = None
) -> list[str]:
    """
    Generate body slide variations for each reference body slide

    Args:
        reference_image_paths: Paths to reference body slides
        product_context: Product description
        variation_count: Number of variations per slide (1-5)
        output_dir: Directory for output images
        style_infos: List of style analysis dicts for each body slide

    Returns:
        List of paths to generated images
    """
    os.makedirs(output_dir, exist_ok=True)
    generated = []

    for slide_idx, ref_path in enumerate(reference_image_paths):
        # Get style info for this specific slide if available
        style_info = None
        if style_infos and slide_idx < len(style_infos):
            style_info = style_infos[slide_idx]

        for var_idx in range(min(variation_count, 5)):
            output_path = os.path.join(output_dir, f'body_{slide_idx+1}_v{var_idx+1}.png')
            try:
                result = generate_styled_image(
                    ref_path,
                    product_context,
                    slide_type='body',
                    output_path=output_path,
                    style_info=style_info
                )
                generated.append(result)
                time.sleep(1)  # Rate limiting
            except GeminiServiceError as e:
                print(f'Warning: Body slide {slide_idx+1} variation {var_idx+1} failed: {e}')

    return generated


def generate_product_slide(
    product_image_path: str,
    reference_style_image: str,
    product_context: str,
    variation_count: int = 1,
    output_dir: str = 'temp/generated',
    style_info: Optional[dict] = None
) -> list[str]:
    """
    Generate product slide by combining user's product image with reference style

    Args:
        product_image_path: User's product image
        reference_style_image: Reference slide for text style
        product_context: Product description for text content
        variation_count: Number of variations (1-5)
        output_dir: Directory for output images
        style_info: Style analysis for product slide from analyze_slides()

    Returns:
        List of paths to generated images
    """
    os.makedirs(output_dir, exist_ok=True)
    generated = []

    client = _get_client()

    # Load both images
    product_data = _load_image_as_base64(product_image_path)
    product_mime = _get_image_mime_type(product_image_path)
    ref_data = _load_image_as_base64(reference_style_image)
    ref_mime = _get_image_mime_type(reference_style_image)

    # Build style instructions from analysis
    style_instructions = ""
    if style_info:
        text_style = style_info.get('text_style', {})
        text_content = style_info.get('text_content', {})

        style_instructions = f"""
EXACT TEXT STYLE TO REPLICATE:
- Text box: {text_style.get('background', 'white rounded rectangle')}
- Text color: {text_style.get('text_color', 'black')}
- Font: {text_style.get('font_weight', 'bold')} {text_style.get('font_style', 'sans-serif')}
- Position: {text_style.get('position', 'center')}
- Alignment: {text_style.get('alignment', 'center')}
- Border: {text_style.get('border', 'none')}
- Shadow: {text_style.get('shadow', 'none')}

TEXT FORMAT:
- Number of lines: {text_content.get('num_lines', 2)}
- Characters per line: ~{text_content.get('chars_per_line', 25)}
- Original CTA type: {text_content.get('text_type', 'product CTA')}
"""

    for i in range(min(variation_count, 5)):
        output_path = os.path.join(output_dir, f'product_v{i+1}.png')

        prompt = f"""I'm providing two images:
1. A product photo (first image) - USE THIS AS THE BASE
2. A reference viral TikTok slide showing text style (second image) - COPY THE TEXT STYLE ONLY

Your task: Add text overlay to the product photo matching EXACTLY the style from reference.
{style_instructions}

Product to promote: {product_context}

IMPORTANT:
- Keep the product photo as the main image
- Only add text overlay in the exact style of reference
- Match text box shape, color, font weight, and positioning
- Generate compelling CTA text (e.g., "Shop now", "Link in bio", product name + benefit)"""

        try:
            if USE_NEW_SDK:
                contents = [
                    prompt,
                    types.Part.from_bytes(
                        data=base64.b64decode(product_data),
                        mime_type=product_mime
                    ),
                    types.Part.from_bytes(
                        data=base64.b64decode(ref_data),
                        mime_type=ref_mime
                    )
                ]

                response = client.models.generate_content(
                    model=IMAGE_MODEL,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        response_modalities=['image', 'text']
                    )
                )

                for part in response.parts:
                    if hasattr(part, 'inline_data') and part.inline_data:
                        with open(output_path, 'wb') as f:
                            f.write(part.inline_data.data)
                        generated.append(output_path)
                        break

            time.sleep(1)  # Rate limiting

        except Exception as e:
            print(f'Warning: Product variation {i+1} failed: {e}')

    return generated


# For testing
if __name__ == '__main__':
    print('Gemini Service Module')
    print(f'Using new SDK: {USE_NEW_SDK}')
    print(f'API Key configured: {bool(GEMINI_API_KEY)}')
