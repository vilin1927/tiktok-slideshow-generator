import asyncio
import logging
import re
import time
from collections import defaultdict
from typing import AsyncGenerator

from src.services.gemini_service import _configure_client

logger = logging.getLogger(__name__)

# Per-meeting conversation context
_meeting_contexts: dict[str, list[dict]] = defaultdict(list)
_last_response_time: dict[str, float] = {}

# Minimum seconds between bot responses
RESPONSE_COOLDOWN = 5.0

# Bot addressing patterns — expanded for better detection
# NOTE: recallai_streaming low-latency mode misrecognizes "bot" as
# "but", "bought", "bart", "bar", "bud", "about" — all variants included.
_BOT_VARIANTS = r"(?:bot|but|bought|bart|bar|bud|about|butt|pot|what)"
_HEY_VARIANTS = r"(?:hey|they|day|hay|say|a)"
BOT_ADDRESS_REGEXES = [
    # Direct address: "hey bot", "hey but", "they bought", "hey bart" etc.
    re.compile(rf"\b{_HEY_VARIANTS}\s+{_BOT_VARIANTS}\b"),
    re.compile(rf"\bhi\s+{_BOT_VARIANTS}\b"),
    re.compile(rf"\bok\s+{_BOT_VARIANTS}\b"),
    re.compile(rf"\byo\s+{_BOT_VARIANTS}\b"),
    re.compile(rf"\b{_BOT_VARIANTS}\s*[,?]\s"),
    re.compile(rf"\b{_BOT_VARIANTS}\s+can\s+you\b"),
    re.compile(rf"\b{_BOT_VARIANTS}\s+could\s+you\b"),
    re.compile(rf"\b{_BOT_VARIANTS}\s+please\b"),
]
BOT_ADDRESS_PATTERNS = [
    # Named address
    "meeting assistant", "hey assistant", "hey edmo", "ok edmo",
    # Questions directed at bot
    "can you summarize", "can you recap", "what was discussed",
    "what did we talk about", "any action items", "what are the action items",
    "what are the key points", "give me a recap", "what happened so far",
    "summarize so far", "summarize the meeting", "quick summary",
    # Action requests
    "can you check", "can you find", "can you send",
    "can you look up", "can you pull up", "can you search",
    # Enrollment-specific
    "what's the enrollment status", "check the deadline",
    "any compliance issues", "any flags",
]

GREETING_TEXT = (
    "Hi everyone, I'm the EDMO Meeting Assistant. "
    "This call is being recorded and transcribed. "
    "Say hey bot if you need me."
)

# Sentence boundary regex — splits on ./?/! followed by space or end
_SENTENCE_END = re.compile(r'(?<=[.!?])\s+')

# Sentinel to signal streaming is done
_STREAM_DONE = object()


def should_respond(meeting_id: str, transcript_text: str) -> bool:
    """Check if the bot should respond to the current transcript segment."""
    text_lower = transcript_text.lower().strip()

    # Check cooldown
    last = _last_response_time.get(meeting_id, 0)
    if time.time() - last < RESPONSE_COOLDOWN:
        return False

    # Regex patterns (handle "bot" misrecognitions: but, bought, bart, etc.)
    for regex in BOT_ADDRESS_REGEXES:
        if regex.search(text_lower):
            logger.info("Address match (regex): %s in '%s'", regex.pattern, text_lower[:60])
            return True

    # Exact substring patterns
    for pattern in BOT_ADDRESS_PATTERNS:
        if pattern in text_lower:
            logger.info("Address match (exact): '%s' in '%s'", pattern, text_lower[:60])
            return True

    return False


async def generate_response_stream(
    meeting_id: str, new_segment: str, speaker: str
) -> AsyncGenerator[str, None]:
    """Generate AI response with TRUE Gemini streaming.

    Uses stream=True so tokens arrive incrementally. A background thread
    pushes chunks to an asyncio.Queue, and this async generator detects
    sentence boundaries in real-time, yielding each sentence the moment
    it's complete.

    Timeline comparison:
      Old: [===== Gemini full =====] → split → yield s1, s2, s3
      New: [== s1 ==] yield → [== s2 ==] yield → [== s3 ==] yield
    """
    _meeting_contexts[meeting_id].append({
        "speaker": speaker,
        "text": new_segment,
    })

    # Keep last 30 segments for context
    if len(_meeting_contexts[meeting_id]) > 30:
        _meeting_contexts[meeting_id] = _meeting_contexts[meeting_id][-30:]

    # Build compact history (last 10 for speed — shorter prompt = faster Gemini)
    history = "\n".join(
        f"{seg['speaker']}: {seg['text']}"
        for seg in _meeting_contexts[meeting_id][-10:]
    )

    model = _configure_client()
    prompt = f"""You are the EDMO Meeting Assistant in a live call. SHORT spoken reply only (2-3 sentences). No markdown, no bullets, no asterisks — this will be spoken aloud.

Conversation:
{history}

Reply to [{speaker}]:"""

    # Queue bridges sync Gemini thread → async generator
    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_event_loop()

    def _stream_to_queue():
        """Run in thread: iterate Gemini stream, push chunks to async queue."""
        try:
            response = model.generate_content(prompt, stream=True)
            for chunk in response:
                if chunk.text:
                    loop.call_soon_threadsafe(queue.put_nowait, chunk.text)
        except Exception as e:
            loop.call_soon_threadsafe(queue.put_nowait, e)
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, _STREAM_DONE)

    try:
        # Start Gemini streaming in background thread
        stream_task = asyncio.get_event_loop().run_in_executor(None, _stream_to_queue)

        buffer = ""
        sentence_count = 0

        # Process chunks as they arrive
        while True:
            item = await queue.get()

            if item is _STREAM_DONE:
                break

            if isinstance(item, Exception):
                logger.error("Gemini stream error for %s: %s", meeting_id, item)
                break

            # Clean formatting artifacts
            clean = item.replace("*", "").replace("#", "").replace("`", "")
            buffer += clean

            # Check for sentence boundaries in buffer
            while True:
                match = _SENTENCE_END.search(buffer)
                if not match:
                    break

                # Extract complete sentence (include the ./?/!)
                end_pos = match.start()
                sentence = buffer[:end_pos + 1].strip()
                buffer = buffer[match.end():]

                if sentence and len(sentence) > 2:
                    sentence_count += 1
                    _last_response_time[meeting_id] = time.time()
                    logger.info(
                        "Stream sentence %d for %s: %s",
                        sentence_count, meeting_id, sentence[:60],
                    )
                    yield sentence

        # Yield any remaining text in buffer
        remaining = buffer.strip()
        if remaining and len(remaining) > 2:
            remaining = remaining.replace("*", "").replace("#", "").replace("`", "")
            sentence_count += 1
            _last_response_time[meeting_id] = time.time()
            logger.info(
                "Final sentence %d for %s: %s",
                sentence_count, meeting_id, remaining[:60],
            )
            yield remaining

        # Wait for thread to finish
        await stream_task

        if sentence_count == 0:
            logger.warning("No sentences generated for %s", meeting_id)

    except Exception as e:
        logger.error("Bot brain failed for %s: %s", meeting_id, e)


# Keep synchronous version for backward compatibility
async def generate_response(
    meeting_id: str, new_segment: str, speaker: str
) -> str | None:
    """Generate a full bot response (non-streaming). Legacy interface."""
    sentences = []
    async for sentence in generate_response_stream(meeting_id, new_segment, speaker):
        sentences.append(sentence)
    return " ".join(sentences) if sentences else None


def add_context(meeting_id: str, speaker: str, text: str):
    """Add a transcript segment to meeting context without generating a response."""
    _meeting_contexts[meeting_id].append({"speaker": speaker, "text": text})
    if len(_meeting_contexts[meeting_id]) > 30:
        _meeting_contexts[meeting_id] = _meeting_contexts[meeting_id][-30:]


def clear_context(meeting_id: str):
    """Clear context when meeting ends."""
    _meeting_contexts.pop(meeting_id, None)
    _last_response_time.pop(meeting_id, None)
