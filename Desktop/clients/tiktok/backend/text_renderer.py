"""
Text Renderer Module

Renders text on images using PIL with various effects:
- Shadow: White text with soft drop shadow
- Outline: White text with black stroke
- Box: Black text on white pill background

Emoji Support:
- Uses Apple Color Emoji font for iPhone-style emojis
- Emojis are rendered separately and composited onto the image
"""

import os
import re
import json
import subprocess
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from typing import Dict, Tuple, Optional, List

try:
    import aggdraw
    HAS_AGGDRAW = True
except ImportError:
    HAS_AGGDRAW = False

from presets import get_preset, get_font_path, get_font_size, TextPreset

# Path to the Node.js rounded text box script
ROUNDED_TEXT_BOX_SCRIPT = os.path.join(os.path.dirname(__file__), 'rounded_text_box.js')


# Apple Color Emoji font path (macOS)
APPLE_EMOJI_FONT = "/System/Library/Fonts/Apple Color Emoji.ttc"

# Regex pattern to detect emoji characters
# This covers most common emojis including skin tones and modifiers
EMOJI_PATTERN = re.compile(
    "["
    "\U0001F600-\U0001F64F"  # Emoticons
    "\U0001F300-\U0001F5FF"  # Misc Symbols and Pictographs
    "\U0001F680-\U0001F6FF"  # Transport and Map
    "\U0001F700-\U0001F77F"  # Alchemical Symbols
    "\U0001F780-\U0001F7FF"  # Geometric Shapes Extended
    "\U0001F800-\U0001F8FF"  # Supplemental Arrows-C
    "\U0001F900-\U0001F9FF"  # Supplemental Symbols and Pictographs
    "\U0001FA00-\U0001FA6F"  # Chess Symbols
    "\U0001FA70-\U0001FAFF"  # Symbols and Pictographs Extended-A
    "\U00002702-\U000027B0"  # Dingbats
    "\U0000FE00-\U0000FE0F"  # Variation Selectors
    "\U0001F1E0-\U0001F1FF"  # Flags
    "\U00002600-\U000026FF"  # Misc symbols (sun, moon, etc)
    "\U00002700-\U000027BF"  # Dingbats
    "\U0000231A-\U0000231B"  # Watch, hourglass
    "\U00002328"             # Keyboard
    "\U000023CF"             # Eject
    "\U000023E9-\U000023F3"  # Various symbols
    "\U000023F8-\U000023FA"  # Various symbols
    "\U000025AA-\U000025AB"  # Squares
    "\U000025B6"             # Play button
    "\U000025C0"             # Reverse button
    "\U000025FB-\U000025FE"  # Squares
    "\U00002614-\U00002615"  # Umbrella, hot beverage
    "\U00002648-\U00002653"  # Zodiac
    "\U0000267F"             # Wheelchair
    "\U00002693"             # Anchor
    "\U000026A1"             # High voltage
    "\U000026AA-\U000026AB"  # Circles
    "\U000026BD-\U000026BE"  # Soccer, baseball
    "\U000026C4-\U000026C5"  # Snowman, sun
    "\U000026CE"             # Ophiuchus
    "\U000026D4"             # No entry
    "\U000026EA"             # Church
    "\U000026F2-\U000026F3"  # Fountain, golf
    "\U000026F5"             # Sailboat
    "\U000026FA"             # Tent
    "\U000026FD"             # Fuel pump
    "\U00002934-\U00002935"  # Arrows
    "\U00002B05-\U00002B07"  # Arrows
    "\U00002B1B-\U00002B1C"  # Squares
    "\U00002B50"             # Star
    "\U00002B55"             # Circle
    "\U00003030"             # Wavy dash
    "\U0000303D"             # Part alternation mark
    "\U00003297"             # Circled Ideograph Congratulation
    "\U00003299"             # Circled Ideograph Secret
    "\U0001F004"             # Mahjong tile
    "\U0001F0CF"             # Playing card
    "\U0001F170-\U0001F171"  # Blood type
    "\U0001F17E-\U0001F17F"  # P button, etc
    "\U0001F18E"             # AB button
    "\U0001F191-\U0001F19A"  # Various squared symbols
    "\U0001F201-\U0001F202"  # Japanese buttons
    "\U0001F21A"             # Japanese "free of charge"
    "\U0001F22F"             # Japanese "reserved"
    "\U0001F232-\U0001F23A"  # Various Japanese symbols
    "\U0001F250-\U0001F251"  # Japanese buttons
    "\u200d"                  # Zero width joiner (for combined emojis)
    "\ufe0f"                  # Variation selector-16
    "]+"
)


def has_emoji(text: str) -> bool:
    """Check if text contains emoji characters."""
    return bool(EMOJI_PATTERN.search(text))


def split_text_and_emojis(text: str) -> List[Tuple[str, bool]]:
    """
    Split text into segments of regular text and emoji sequences.

    Returns:
        List of (segment, is_emoji) tuples
    """
    segments = []
    last_end = 0

    for match in EMOJI_PATTERN.finditer(text):
        # Add text before emoji
        if match.start() > last_end:
            text_part = text[last_end:match.start()]
            if text_part:
                segments.append((text_part, False))

        # Add emoji
        segments.append((match.group(), True))
        last_end = match.end()

    # Add remaining text
    if last_end < len(text):
        text_part = text[last_end:]
        if text_part:
            segments.append((text_part, False))

    return segments if segments else [(text, False)]


def normalize_punctuation(text: str) -> str:
    """
    Normalize text punctuation for authentic TikTok style.

    Rules:
    - Remove trailing periods (dots) - not authentic
    - Keep ! if present
    - Keep emojis at end
    - Remove double spaces
    """
    # Remove trailing whitespace
    text = text.rstrip()

    # Remove trailing period(s) unless followed by emoji
    while text.endswith('.'):
        # Check if there's an emoji right before the dot
        if len(text) > 1 and has_emoji(text[-2:-1]):
            break
        text = text[:-1].rstrip()

    # Clean up any double spaces
    text = ' '.join(text.split())

    return text


def load_emoji_font(size: int = 160) -> Optional[ImageFont.FreeTypeFont]:
    """
    Load Apple Color Emoji font for iPhone-style emoji rendering.

    Note: Apple Color Emoji is a bitmap font that only works at size 160.
    The emoji will be scaled to match the desired text size.

    Returns:
        PIL ImageFont or None if not available
    """
    if os.path.exists(APPLE_EMOJI_FONT):
        try:
            # Apple Color Emoji only works at size 160 (bitmap font)
            return ImageFont.truetype(APPLE_EMOJI_FONT, 160)
        except Exception:
            pass
    return None


# Native emoji font size (Apple Color Emoji bitmap size)
EMOJI_NATIVE_SIZE = 160


def hex_to_rgb(hex_color: str) -> Tuple[int, int, int]:
    """Convert hex color to RGB tuple."""
    hex_color = hex_color.lstrip('#')
    return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))


def load_font(font_file: str, size: int) -> ImageFont.FreeTypeFont:
    """
    Load a font with fallback.

    Args:
        font_file: Font filename
        size: Font size in pixels

    Returns:
        PIL ImageFont
    """
    font_path = get_font_path(font_file)

    if os.path.exists(font_path):
        return ImageFont.truetype(font_path, size)
    else:
        # Fallback to default font
        try:
            return ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", size)
        except:
            return ImageFont.load_default()


def get_text_bbox(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont
) -> Tuple[int, int, int, int]:
    """
    Get text bounding box.

    Returns:
        (left, top, right, bottom) coordinates
    """
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox


def get_text_width_with_emojis(
    text: str,
    font: ImageFont.FreeTypeFont,
    emoji_font: Optional[ImageFont.FreeTypeFont],
    draw: ImageDraw.ImageDraw
) -> int:
    """
    Calculate text width accounting for different emoji font sizing.

    Returns:
        Total width in pixels
    """
    if not has_emoji(text) or not emoji_font:
        bbox = draw.textbbox((0, 0), text, font=font)
        return bbox[2] - bbox[0]

    total_width = 0
    segments = split_text_and_emojis(text)

    for segment, is_emoji in segments:
        if is_emoji and emoji_font:
            bbox = draw.textbbox((0, 0), segment, font=emoji_font)
        else:
            bbox = draw.textbbox((0, 0), segment, font=font)
        total_width += bbox[2] - bbox[0]

    return total_width


def wrap_text(
    text: str,
    font: ImageFont.FreeTypeFont,
    max_width: int,
    emoji_font: Optional[ImageFont.FreeTypeFont] = None
) -> list:
    """
    Wrap text to fit within max_width.

    Args:
        text: Text to wrap
        font: Font to use for measurement
        max_width: Maximum width in pixels
        emoji_font: Optional emoji font for proper emoji sizing

    Returns:
        List of text lines
    """
    words = text.split()
    lines = []
    current_line = []

    # Create temporary draw for measurement
    temp_img = Image.new('RGB', (1, 1))
    draw = ImageDraw.Draw(temp_img)

    for word in words:
        current_line.append(word)
        test_line = ' '.join(current_line)
        line_width = get_text_width_with_emojis(test_line, font, emoji_font, draw)

        if line_width > max_width and len(current_line) > 1:
            # Remove last word and add line
            current_line.pop()
            lines.append(' '.join(current_line))
            current_line = [word]

    if current_line:
        lines.append(' '.join(current_line))

    return lines


def render_emoji_scaled(
    emoji: str,
    emoji_font: ImageFont.FreeTypeFont,
    target_size: int
) -> Image.Image:
    """
    Render a single emoji as an RGBA image at the target size.

    Apple Color Emoji renders at 160px, so we scale to target size.

    Args:
        emoji: Emoji character(s)
        emoji_font: Apple Color Emoji font
        target_size: Desired emoji size in pixels

    Returns:
        RGBA Image with the emoji
    """
    # Render at native size (160px)
    temp = Image.new('RGBA', (EMOJI_NATIVE_SIZE * 2, EMOJI_NATIVE_SIZE * 2), (0, 0, 0, 0))
    temp_draw = ImageDraw.Draw(temp)
    temp_draw.text((0, 0), emoji, font=emoji_font, embedded_color=True)

    # Get actual bounding box
    bbox = temp.getbbox()
    if bbox:
        # Crop to content
        emoji_img = temp.crop(bbox)
        # Scale to target size
        scale = target_size / EMOJI_NATIVE_SIZE
        new_size = (max(1, int(emoji_img.width * scale)), max(1, int(emoji_img.height * scale)))
        emoji_img = emoji_img.resize(new_size, Image.Resampling.LANCZOS)
        return emoji_img

    return Image.new('RGBA', (target_size, target_size), (0, 0, 0, 0))


def render_text_with_emojis(
    draw: ImageDraw.ImageDraw,
    text: str,
    position: Tuple[int, int],
    font: ImageFont.FreeTypeFont,
    emoji_font: Optional[ImageFont.FreeTypeFont],
    fill: Tuple[int, int, int, int],
    stroke_width: int = 0,
    stroke_fill: Optional[Tuple[int, int, int, int]] = None,
    target_image: Optional[Image.Image] = None,
    target_emoji_size: int = 50
) -> int:
    """
    Render text with mixed regular and emoji fonts.

    For emojis, renders them as color images using Apple Color Emoji
    and composites them onto the target image.

    Args:
        draw: ImageDraw object
        text: Text to render
        position: (x, y) position
        font: Regular text font
        emoji_font: Emoji font (Apple Color Emoji)
        fill: RGBA fill color
        stroke_width: Optional stroke width
        stroke_fill: Optional stroke color
        target_image: Target RGBA image for compositing emojis
        target_emoji_size: Target emoji size in pixels

    Returns:
        Final x position after rendering
    """
    x, y = position

    if not has_emoji(text) or not emoji_font:
        # No emojis, simple render
        if stroke_width > 0 and stroke_fill:
            draw.text((x, y), text, font=font, fill=fill,
                     stroke_width=stroke_width, stroke_fill=stroke_fill)
        else:
            draw.text((x, y), text, font=font, fill=fill)
        bbox = draw.textbbox((0, 0), text, font=font)
        return x + (bbox[2] - bbox[0])

    # Split and render segments
    segments = split_text_and_emojis(text)

    for segment, is_emoji in segments:
        if is_emoji and emoji_font and target_image:
            # Render emoji as color image and composite
            emoji_img = render_emoji_scaled(segment, emoji_font, target_emoji_size)
            # Center emoji vertically with text
            emoji_y = y + (target_emoji_size - emoji_img.height) // 2
            target_image.paste(emoji_img, (int(x), int(emoji_y)), emoji_img)
            x += emoji_img.width
        elif is_emoji and emoji_font:
            # Fallback: just draw without color (if no target_image)
            draw.text((x, y), segment, font=emoji_font, fill=fill, embedded_color=True)
            bbox = draw.textbbox((0, 0), segment, font=emoji_font)
            x += bbox[2] - bbox[0]
        else:
            # Regular text
            if stroke_width > 0 and stroke_fill:
                draw.text((x, y), segment, font=font, fill=fill,
                         stroke_width=stroke_width, stroke_fill=stroke_fill)
            else:
                draw.text((x, y), segment, font=font, fill=fill)
            bbox = draw.textbbox((0, 0), segment, font=font)
            x += bbox[2] - bbox[0]

    return x


def render_shadow_text(
    image: Image.Image,
    text: str,
    position: Tuple[int, int],
    font: ImageFont.FreeTypeFont,
    text_color: str,
    shadow_color: str,
    shadow_opacity: float,
    shadow_offset: Tuple[int, int],
    shadow_blur: int,
    emoji_font: Optional[ImageFont.FreeTypeFont] = None,
    font_size: int = 50
) -> Image.Image:
    """
    Render text with drop shadow effect.

    Args:
        image: Base image
        text: Text to render
        position: (x, y) position for text
        font: Font to use
        text_color: Text color (hex)
        shadow_color: Shadow color (hex)
        shadow_opacity: Shadow opacity (0-1)
        shadow_offset: Shadow offset (x, y)
        shadow_blur: Shadow blur radius
        emoji_font: Optional emoji font for iPhone-style emojis
        font_size: Font size for emoji scaling

    Returns:
        Image with text rendered
    """
    # Create a copy
    result = image.copy().convert('RGBA')

    # Create shadow layer (no emojis in shadow - they would look weird blurred)
    shadow_layer = Image.new('RGBA', result.size, (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow_layer)

    # Draw shadow (text only, no color emojis)
    shadow_pos = (position[0] + shadow_offset[0], position[1] + shadow_offset[1])
    shadow_rgb = hex_to_rgb(shadow_color)
    shadow_rgba = shadow_rgb + (int(255 * shadow_opacity),)
    render_text_with_emojis(shadow_draw, text, shadow_pos, font, None, shadow_rgba,
                           target_emoji_size=font_size)

    # Blur shadow
    shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(shadow_blur))

    # Composite shadow
    result = Image.alpha_composite(result, shadow_layer)

    # Draw text layer
    text_layer = Image.new('RGBA', result.size, (0, 0, 0, 0))
    text_draw = ImageDraw.Draw(text_layer)
    text_rgb = hex_to_rgb(text_color)
    render_text_with_emojis(text_draw, text, position, font, emoji_font, text_rgb + (255,),
                           target_image=text_layer, target_emoji_size=font_size)

    # Composite text
    result = Image.alpha_composite(result, text_layer)

    return result.convert('RGB')


def render_outline_text(
    image: Image.Image,
    text: str,
    position: Tuple[int, int],
    font: ImageFont.FreeTypeFont,
    text_color: str,
    outline_color: str,
    outline_width: int,
    emoji_font: Optional[ImageFont.FreeTypeFont] = None,
    font_size: int = 50
) -> Image.Image:
    """
    Render text with outline/stroke effect.

    Args:
        image: Base image
        text: Text to render
        position: (x, y) position for text
        font: Font to use
        text_color: Text color (hex)
        outline_color: Outline color (hex)
        outline_width: Outline width in pixels
        emoji_font: Optional emoji font for iPhone-style emojis
        font_size: Font size for emoji scaling

    Returns:
        Image with text rendered
    """
    result = image.copy().convert('RGBA')
    draw = ImageDraw.Draw(result)

    text_rgb = hex_to_rgb(text_color)
    outline_rgb = hex_to_rgb(outline_color)

    # Use emoji-aware rendering
    render_text_with_emojis(
        draw, text, position, font, emoji_font,
        text_rgb + (255,),
        stroke_width=outline_width,
        stroke_fill=outline_rgb + (255,),
        target_image=result,
        target_emoji_size=font_size
    )

    return result.convert('RGB')


def render_box_text(
    image: Image.Image,
    text: str,
    position: Tuple[int, int],
    font: ImageFont.FreeTypeFont,
    text_color: str,
    box_color: str,
    box_padding: int,
    box_radius: int,
    emoji_font: Optional[ImageFont.FreeTypeFont] = None,
    font_size: int = 50
) -> Image.Image:
    """
    Render text with pill/box background.

    The box is properly centered around the text, accounting for font metrics.

    Args:
        image: Base image
        text: Text to render
        position: (x, y) position - this is where text will be drawn
        font: Font to use
        text_color: Text color (hex)
        box_color: Box background color (hex)
        box_padding: Padding inside box
        box_radius: Corner radius for rounded box
        emoji_font: Optional emoji font for iPhone-style emojis
        font_size: Font size for emoji scaling

    Returns:
        Image with text rendered
    """
    result = image.copy().convert('RGBA')
    draw = ImageDraw.Draw(result)

    # Get text bounding box at origin to understand font metrics
    bbox = draw.textbbox((0, 0), text, font=font)
    # bbox returns (left, top, right, bottom) relative to origin
    # top can be negative due to font ascent
    text_left_offset = bbox[0]  # Usually 0 or small positive
    text_top_offset = bbox[1]   # Often negative (above baseline)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]

    # Account for emojis in width calculation
    emoji_width = get_text_width_with_emojis(text, font, emoji_font, draw)
    if emoji_width > text_width:
        text_width = emoji_width

    # Calculate box dimensions with proper padding
    box_width = text_width + 2 * box_padding
    box_height = text_height + 2 * box_padding

    # Position box so text is centered inside it
    # The text will be drawn at position, so box needs to account for font offset
    text_x, text_y = position
    box_x = text_x - box_padding - text_left_offset
    box_y = text_y - box_padding + text_top_offset  # Add top_offset because it's negative

    # Draw rounded rectangle (pill shape)
    box_rgb = hex_to_rgb(box_color)
    draw.rounded_rectangle(
        [box_x, box_y, box_x + box_width, box_y + box_height],
        radius=box_radius,
        fill=box_rgb + (255,)
    )

    # Draw text at original position
    text_rgb = hex_to_rgb(text_color)
    render_text_with_emojis(draw, text, position, font, emoji_font, text_rgb + (255,),
                           target_image=result, target_emoji_size=font_size)

    return result.convert('RGB')


def get_remotion_rounded_box(line_measurements: List[Dict], border_radius: int = 20,
                              horizontal_padding: int = 40, text_align: str = 'center') -> Dict:
    """
    Call Node.js Remotion script to generate rounded text box path.

    Args:
        line_measurements: List of {width, height} for each line
        border_radius: Corner radius
        horizontal_padding: Left/right padding
        text_align: 'left', 'center', or 'right'

    Returns:
        Dict with 'path', 'width', 'height', 'boundingBox'
    """
    input_data = {
        'lines': line_measurements,
        'borderRadius': border_radius,
        'horizontalPadding': horizontal_padding,
        'textAlign': text_align
    }

    try:
        result = subprocess.run(
            ['node', ROUNDED_TEXT_BOX_SCRIPT, json.dumps(input_data)],
            capture_output=True,
            text=True,
            cwd=os.path.dirname(ROUNDED_TEXT_BOX_SCRIPT)
        )

        if result.returncode != 0:
            raise RuntimeError(f"Node.js error: {result.stderr}")

        return json.loads(result.stdout)
    except Exception as e:
        raise RuntimeError(f"Failed to generate rounded box: {e}")


def draw_svg_path_aggdraw(image: Image.Image, path_d: str, fill_color: Tuple[int, int, int],
                          offset_x: int, offset_y: int) -> Image.Image:
    """
    Draw SVG path on image using aggdraw.

    Args:
        image: Target image
        path_d: SVG path 'd' attribute
        fill_color: RGB fill color
        offset_x, offset_y: Position offset

    Returns:
        Image with path drawn
    """
    if not HAS_AGGDRAW:
        raise ImportError("aggdraw not available")

    result = image.copy().convert('RGBA')

    # Create aggdraw context
    ctx = aggdraw.Draw(result)

    # Create brush with fill color
    brush = aggdraw.Brush(fill_color, 255)

    # Create path - aggdraw uses a symbol-based path
    # We need to transform the SVG path and offset it
    path = aggdraw.Path()

    # Parse and execute path commands
    import re
    commands = re.findall(r'([MLCZ])\s*([\d\s\.\-e,]*)', path_d)

    for cmd, args in commands:
        if not args.strip():
            if cmd == 'Z':
                path.close()
            continue

        # Parse numbers
        nums = [float(n) for n in re.findall(r'-?[\d.]+(?:e[+-]?\d+)?', args)]

        if cmd == 'M':
            path.moveto(nums[0] + offset_x, nums[1] + offset_y)
        elif cmd == 'L':
            path.lineto(nums[0] + offset_x, nums[1] + offset_y)
        elif cmd == 'C':
            # Bezier curve: C cp1x,cp1y cp2x,cp2y x,y
            path.curveto(
                nums[0] + offset_x, nums[1] + offset_y,
                nums[2] + offset_x, nums[3] + offset_y,
                nums[4] + offset_x, nums[5] + offset_y
            )
        elif cmd == 'Z':
            path.close()

    # Draw the path
    ctx.path(path, brush)
    ctx.flush()

    return result


def render_multiline_box_text(
    image: Image.Image,
    lines: List[str],
    zone_x: int,
    zone_w: int,
    start_y: int,
    line_height: float,
    font: ImageFont.FreeTypeFont,
    emoji_font: Optional[ImageFont.FreeTypeFont],
    font_size: int,
    temp_draw: ImageDraw.ImageDraw,
    text_color: str,
    box_color: str,
    box_padding_h: int,
    box_padding_v: int,
    box_radius: int
) -> Image.Image:
    """
    Render multiple lines of text using Remotion's rounded-text-box algorithm.

    TikTok/Instagram style:
    - Each line gets its own width-hugging box section
    - Smooth curved transitions between lines of different widths
    - Creates the iconic "staircase" rounded shape

    Args:
        box_padding_h: Horizontal padding (left/right) - from Figma
        box_padding_v: Vertical padding (top/bottom) - from Figma
    """
    result = image.copy().convert('RGBA')

    box_rgb = hex_to_rgb(box_color)
    text_rgb = hex_to_rgb(text_color)

    # Calculate text height
    bbox = temp_draw.textbbox((0, 0), "Hg", font=font)
    text_height = bbox[3] - bbox[1]

    # Line height including padding
    line_box_height = text_height + 2 * box_padding_v

    # Measure each line
    line_measurements = []
    line_widths = []
    for line in lines:
        line_width = get_text_width_with_emojis(line, font, emoji_font, temp_draw)
        line_widths.append(line_width)
        line_measurements.append({
            'width': line_width,
            'height': line_box_height
        })

    # Get the Remotion rounded box path
    try:
        box_data = get_remotion_rounded_box(
            line_measurements,
            border_radius=box_radius,
            horizontal_padding=box_padding_h,
            text_align='center'
        )

        # Calculate position to center the box in zone
        box_width = box_data['width']
        box_height = box_data['height']
        box_x = zone_x + (zone_w - box_width) // 2
        box_y = start_y

        # Draw the path using aggdraw
        if HAS_AGGDRAW:
            result = draw_svg_path_aggdraw(result, box_data['path'], box_rgb, box_x, box_y)
        else:
            # Fallback: draw simple rounded rectangle
            draw = ImageDraw.Draw(result)
            draw.rounded_rectangle(
                [box_x, box_y, box_x + box_width, box_y + box_height],
                radius=box_radius,
                fill=box_rgb + (255,)
            )

    except Exception as e:
        # Fallback to simple unified box if Remotion fails
        print(f"Remotion fallback: {e}")
        max_line_width = max(line_widths)
        box_width = max_line_width + 2 * box_padding_h
        box_height = len(lines) * line_box_height
        box_x = zone_x + (zone_w - box_width) // 2
        box_y = start_y

        draw = ImageDraw.Draw(result)
        draw.rounded_rectangle(
            [box_x, box_y, box_x + box_width, box_y + box_height],
            radius=box_radius,
            fill=box_rgb + (255,)
        )

    # Draw text on top of the box
    draw = ImageDraw.Draw(result)
    max_line_width = max(line_widths)

    # Get font metrics for proper vertical centering
    # textbbox returns (left, top, right, bottom) relative to origin
    sample_bbox = temp_draw.textbbox((0, 0), "Hg", font=font)
    text_top_offset = sample_bbox[1]  # How far below origin the text starts

    current_y = box_y + box_padding_v - text_top_offset  # Adjust for font metrics

    for i, line in enumerate(lines):
        line_width = line_widths[i]
        # Center text horizontally based on max line width
        text_x = box_x + box_padding_h + (max_line_width - line_width) // 2
        text_y = current_y

        render_text_with_emojis(
            draw, line, (text_x, text_y), font, emoji_font,
            text_rgb + (255,), target_image=result, target_emoji_size=font_size
        )

        current_y += line_box_height

    return result.convert('RGB')


def render_text(
    image_path: str,
    text: str,
    zone: Dict,
    preset_id: str,
    output_path: Optional[str] = None
) -> Image.Image:
    """
    Render text on image using specified preset.

    Main entry point for text rendering.

    Features:
    - iPhone-style emoji rendering via Apple Color Emoji font
    - Automatic punctuation normalization (no trailing dots)
    - Safe zone aware text placement

    Args:
        image_path: Path to base image
        text: Text to render
        zone: Safe zone dict with bounds {x, y, w, h} and text_color_suggestion
        preset_id: Preset ID (e.g., 'classic_shadow')
        output_path: Optional path to save result

    Returns:
        PIL Image with text rendered
    """
    # Load image
    image = Image.open(image_path)
    img_width, img_height = image.size

    # Get preset
    preset = get_preset(preset_id)
    if preset is None:
        raise ValueError(f"Unknown preset: {preset_id}")

    # Normalize punctuation (remove trailing dots - not authentic for TikTok)
    text = normalize_punctuation(text)

    # Calculate font size
    font_size = get_font_size(len(text), img_height)
    font = load_font(preset.font.file, font_size)

    # Load emoji font for iPhone-style emojis
    # Note: Apple Color Emoji only loads at 160px, we scale to font_size during rendering
    emoji_font = load_emoji_font()

    # Get zone bounds
    bounds = zone['bounds']
    zone_x = bounds['x']
    zone_y = bounds['y']
    zone_w = bounds['w']
    zone_h = bounds['h']

    # Wrap text to fit zone (with emoji-aware width calculation)
    max_text_width = zone_w - 40  # Padding
    lines = wrap_text(text, font, max_text_width, emoji_font)

    # Calculate total text height
    temp_img = Image.new('RGB', (1, 1))
    temp_draw = ImageDraw.Draw(temp_img)
    line_height = font_size * 1.3  # 1.3x line spacing
    total_height = len(lines) * line_height

    # Calculate starting position (center in zone)
    start_x = zone_x + (zone_w - max_text_width) // 2
    start_y = zone_y + (zone_h - total_height) // 2

    # Get effect config
    effect = preset.effect

    # Determine text color based on zone brightness suggestion
    # For shadow/outline: use zone's suggestion (adapts to background)
    # For box: always use preset color (text on white box = black)
    text_color = effect.text_color  # Default from preset

    if effect.type in ('shadow', 'outline'):
        # Use the zone's text color suggestion based on background brightness
        text_color_suggestion = zone.get('text_color_suggestion', 'white')
        if text_color_suggestion == 'black':
            text_color = '#000000'  # Dark text for light backgrounds
        else:
            text_color = '#FFFFFF'  # Light text for dark backgrounds

    # For box style, each line gets its own box (Figma design)
    if effect.type == 'box':
        result = render_multiline_box_text(
            image, lines, zone_x, zone_w, start_y, line_height,
            font, emoji_font, font_size, temp_draw,
            effect.text_color, effect.box_color,
            effect.box_padding,      # Horizontal padding (40px from Figma)
            effect.box_padding_v,    # Vertical padding (20px from Figma)
            effect.box_radius
        )
    else:
        # Render each line separately for shadow/outline styles
        result = image
        for i, line in enumerate(lines):
            y = int(start_y + i * line_height)

            # Center line horizontally (with emoji-aware width calculation)
            line_width = get_text_width_with_emojis(line, font, emoji_font, temp_draw)
            x = int(zone_x + (zone_w - line_width) // 2)

            if effect.type == 'shadow':
                result = render_shadow_text(
                    result, line, (x, y), font,
                    text_color,  # Use dynamic text color based on background
                    effect.shadow_color,
                    effect.shadow_opacity,
                    effect.shadow_offset,
                    effect.shadow_blur,
                    emoji_font,
                    font_size
                )
            elif effect.type == 'outline':
                result = render_outline_text(
                    result, line, (x, y), font,
                    text_color,  # Use dynamic text color based on background
                    effect.outline_color,
                    effect.outline_width,
                    emoji_font,
                    font_size
                )

    # Save if output path provided
    if output_path:
        result.save(output_path, quality=95)

    return result
