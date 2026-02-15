"""
Text Variation Service

Uses Claude API to generate hook/CTA text variations for Instagram reels.
Falls back to Gemini if Claude is unavailable (no credits, import error, etc.).
"""
import os
import json

from logging_config import get_logger

logger = get_logger('text_variation_service')


def _build_prompt(base_text: str, needed: int, text_type: str) -> str:
    """Build the variation generation prompt."""
    if text_type == 'hook':
        return f"""Rewrite this Instagram reel hook text in {needed} different ways.

Original: "{base_text}"

Rules:
- Keep the SAME meaning and tone
- Keep similar length (short, punchy)
- Use social media style (Gen Z/millennial)
- Each variation should feel natural, not forced
- No hashtags, no emojis unless the original has them
- Return ONLY a JSON array of strings, nothing else

Example output format: ["variation 1", "variation 2"]"""
    else:
        return f"""Rewrite this Instagram reel CTA (call-to-action) text in {needed} different ways.

Original: "{base_text}"

Rules:
- Keep the SAME call-to-action intent
- Keep it short and direct
- Each should feel different but serve the same purpose
- Keep emojis if the original has them (like ⬇️)
- Return ONLY a JSON array of strings, nothing else

Example output format: ["variation 1", "variation 2"]"""


def _parse_json_response(raw_text: str) -> list:
    """Parse JSON array from LLM response, stripping markdown fences."""
    text = raw_text.strip()
    if text.startswith('```'):
        text = text.split('\n', 1)[1] if '\n' in text else text[3:]
        if text.endswith('```'):
            text = text[:-3]
        text = text.strip()
    return json.loads(text)


def _try_claude(prompt: str, needed: int) -> list[str] | None:
    """Try generating variations with Claude. Returns list or None on failure."""
    try:
        import anthropic
    except ImportError:
        logger.info("anthropic package not installed, skipping Claude")
        return None

    api_key = os.getenv('ANTHROPIC_API_KEY', '').strip()
    if not api_key:
        logger.info("ANTHROPIC_API_KEY not set, skipping Claude")
        return None

    try:
        client = anthropic.Anthropic(api_key=api_key)
        logger.info(f"Calling Claude API for {needed} text variations...")

        response = client.messages.create(
            model="claude-sonnet-4-5-20250929",
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}]
        )

        generated = _parse_json_response(response.content[0].text)
        if isinstance(generated, list) and len(generated) > 0:
            logger.info(f"Claude generated {len(generated)} variations")
            return generated[:needed]

        logger.warning(f"Claude returned unexpected format: {type(generated)}")
        return None

    except Exception as e:
        logger.warning(f"Claude failed ({type(e).__name__}): {e}")
        return None


def _try_gemini(prompt: str, needed: int) -> list[str] | None:
    """Try generating variations with Gemini. Returns list or None on failure."""
    try:
        from google import genai
    except ImportError:
        logger.info("google-genai package not installed, skipping Gemini")
        return None

    api_key = os.getenv('GEMINI_API_KEY', '').strip().strip("'")
    if not api_key:
        logger.info("GEMINI_API_KEY not set, skipping Gemini")
        return None

    try:
        client = genai.Client(api_key=api_key)
        logger.info(f"Calling Gemini API for {needed} text variations (fallback)...")

        response = client.models.generate_content(
            model='gemini-2.0-flash',
            contents=[prompt]
        )

        generated = _parse_json_response(response.text)
        if isinstance(generated, list) and len(generated) > 0:
            logger.info(f"Gemini generated {len(generated)} variations")
            return generated[:needed]

        logger.warning(f"Gemini returned unexpected format: {type(generated)}")
        return None

    except Exception as e:
        logger.warning(f"Gemini failed ({type(e).__name__}): {e}")
        return None


def generate_text_variations(
    base_text: str,
    num_variations: int,
    text_type: str = 'hook'
) -> list[str]:
    """
    Generate text variations using Claude API, falling back to Gemini.

    Args:
        base_text: Original text to create variations of
        num_variations: Number of variations to generate (1-10)
        text_type: 'hook' or 'cta'

    Returns:
        List of text strings (includes original as first item)
    """
    if num_variations <= 1:
        return [base_text]

    # Always include original as first
    variations = [base_text]
    needed = num_variations - 1

    prompt = _build_prompt(base_text, needed, text_type)

    # Try Claude first, then Gemini as fallback
    generated = _try_claude(prompt, needed)
    if not generated:
        generated = _try_gemini(prompt, needed)

    if generated:
        # Filter out any that are identical to the original
        unique = [v for v in generated if v.strip().lower() != base_text.strip().lower()]
        if unique:
            variations.extend(unique[:needed])
        else:
            logger.warning("All generated variations were identical to original, using them anyway")
            variations.extend(generated[:needed])

    # Pad with original if we didn't get enough
    while len(variations) < num_variations:
        variations.append(base_text)
        logger.warning(f"Padded variation {len(variations)} with original text (no API succeeded)")

    return variations[:num_variations]
