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
# Sliding window: last N seconds of text per meeting for cross-webhook matching
_recent_segments: dict[str, list[tuple[float, str]]] = defaultdict(list)
# Buffer for current utterance (filled during speech, processed after pause)
_utterance_buffer: dict[str, list[tuple[str, str]]] = defaultdict(list)  # meeting_id -> [(speaker, text)]
# Track who is currently speaking
_current_speaker: dict[str, str] = {}  # meeting_id -> speaker_name
# Track last transcript time for pause detection
_last_transcript_time: dict[str, float] = {}  # meeting_id -> timestamp
# Pending response tasks (for cancellation on new speech)
_pending_response_tasks: dict[str, asyncio.Task] = {}  # meeting_id -> scheduled task

# Minimum seconds between bot responses
RESPONSE_COOLDOWN = 3.0
# How many seconds of recent text to concatenate for pattern matching
RECENT_WINDOW_SECONDS = 5.0
# Always respond mode: bot participates in ALL conversation, not just when addressed
ALWAYS_RESPOND = True
# Pause detection: wait for X seconds of silence before responding
# This replaces speech_off events (which Recall.ai doesn't support)
PAUSE_DETECTION_SECONDS = 1.5

# Wake word: "EDMO" — distinctive name, unlikely ASR false positives.
# Possible misrecognitions: "ed mo", "at mo", "edmo", "edmow", "ed more"
_EDMO_VARIANTS = r"(?:edmo|ed\s*mo|at\s*mo|edmow|ed\s*more|emo)"
_HEY_VARIANTS = r"(?:hey|hi|ok|yo)"
BOT_ADDRESS_REGEXES = [
    # Direct address: "hey edmo", "hi edmo", "ok edmo"
    re.compile(rf"\b{_HEY_VARIANTS}\s+{_EDMO_VARIANTS}\b"),
    # Just the name: "edmo, can you..."
    re.compile(rf"\b{_EDMO_VARIANTS}\s*[,?]\s"),
    re.compile(rf"\b{_EDMO_VARIANTS}\s+can\s+you\b"),
    re.compile(rf"\b{_EDMO_VARIANTS}\s+could\s+you\b"),
    re.compile(rf"\b{_EDMO_VARIANTS}\s+please\b"),
    re.compile(rf"\b{_EDMO_VARIANTS}\s+what\b"),
    re.compile(rf"\b{_EDMO_VARIANTS}\s+tell\b"),
]
BOT_ADDRESS_PATTERNS = [
    # Name variants
    "edmo", "ed mo", "hey edmo", "ok edmo", "hi edmo",
    "meeting assistant", "hey assistant",
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
    "Hi everyone, I'm EDMO, your meeting assistant. "
    "This call is being recorded and transcribed. "
    "I'm here to help throughout the conversation."
)

# Sentence boundary regex — splits on ./?/! followed by space or end
_SENTENCE_END = re.compile(r'(?<=[.!?])\s+')

# Sentinel to signal streaming is done
_STREAM_DONE = object()


import asyncio as _asyncio


def buffer_transcript_with_pause_detection(
    meeting_id: str,
    speaker: str,
    text: str,
    on_pause_callback
) -> None:
    """Buffer transcript and schedule response after pause.

    Pause detection flow:
    1. Transcript arrives → buffer it, cancel any pending response
    2. Schedule new response for PAUSE_DETECTION_SECONDS later
    3. If more transcript arrives → cancel and reschedule
    4. If pause timer fires → user finished speaking → trigger response
    """
    now = time.time()
    _last_transcript_time[meeting_id] = now
    _current_speaker[meeting_id] = speaker

    # Buffer the transcript
    _utterance_buffer[meeting_id].append((speaker, text))

    # Also add to context
    add_context(meeting_id, speaker, text)

    # Cancel any pending response task
    if meeting_id in _pending_response_tasks:
        _pending_response_tasks[meeting_id].cancel()
        logger.debug("Cancelled pending response for %s (new speech)", meeting_id)

    # Schedule new response after pause
    async def delayed_response():
        try:
            await _asyncio.sleep(PAUSE_DETECTION_SECONDS)
            # Check if this is still the latest transcript (no new speech arrived)
            if _last_transcript_time.get(meeting_id) == now:
                result = _check_and_prepare_response(meeting_id, speaker)
                if result[0]:  # should_respond
                    await on_pause_callback(result[1], result[2])  # text, speaker
        except _asyncio.CancelledError:
            pass  # New speech arrived, this response was cancelled
        finally:
            _pending_response_tasks.pop(meeting_id, None)

    # Create and store the task
    loop = _asyncio.get_event_loop()
    task = loop.create_task(delayed_response())
    _pending_response_tasks[meeting_id] = task


def _check_and_prepare_response(meeting_id: str, speaker: str) -> tuple[bool, str, str]:
    """Check if we should respond after pause detected.

    Returns (should_respond, full_text, speaker).
    """
    # Check cooldown
    last = _last_response_time.get(meeting_id, 0)
    if time.time() - last < RESPONSE_COOLDOWN:
        logger.debug("Pause detected but cooldown active for %s", meeting_id)
        _utterance_buffer[meeting_id].clear()
        return False, "", ""

    # Get buffered utterance
    buffer = _utterance_buffer.get(meeting_id, [])
    if not buffer:
        return False, "", ""

    # Combine all text from this utterance
    full_text = " ".join(text for _, text in buffer)
    last_speaker = buffer[-1][0] if buffer else speaker

    # Clear buffer
    _utterance_buffer[meeting_id].clear()
    _current_speaker.pop(meeting_id, None)

    # Check if we should respond
    if len(full_text.strip()) < 10:
        logger.debug("Utterance too short to respond: '%s'", full_text)
        return False, "", ""

    # Claim response (set cooldown)
    _last_response_time[meeting_id] = time.time()
    logger.info("Pause detected (%.1fs), responding to: '%s'", PAUSE_DETECTION_SECONDS, full_text[:80])

    return True, full_text, last_speaker


def cancel_pending_response(meeting_id: str):
    """Cancel any pending response for a meeting."""
    if meeting_id in _pending_response_tasks:
        _pending_response_tasks[meeting_id].cancel()
        _pending_response_tasks.pop(meeting_id, None)


def _get_recent_text(meeting_id: str, new_text: str) -> str:
    """Concatenate text from last RECENT_WINDOW_SECONDS including new_text.

    Recall.ai low-latency mode splits speech into individual word-level
    webhooks: "hey" arrives as one request, "bot" arrives 300ms later as
    another. This window merges them so patterns like "hey bot" can match.
    """
    now = time.time()
    _recent_segments[meeting_id].append((now, new_text))
    # Remove segments older than window
    _recent_segments[meeting_id] = [
        (t, txt) for t, txt in _recent_segments[meeting_id]
        if now - t < RECENT_WINDOW_SECONDS
    ]
    return " ".join(txt for _, txt in _recent_segments[meeting_id])


def _claim_response(meeting_id: str):
    """Lock cooldown and clear sliding window IMMEDIATELY on match.

    This prevents the same trigger from firing multiple parallel responses.
    Must be called inside should_respond() BEFORE returning True.
    """
    _last_response_time[meeting_id] = time.time()
    _recent_segments[meeting_id].clear()


def should_respond(meeting_id: str, transcript_text: str) -> bool:
    """Check if the bot should respond to the current transcript segment.

    If ALWAYS_RESPOND=True, bot responds to ALL conversation (after cooldown).
    Otherwise, uses wake word detection ("hey edmo", etc.).
    """
    # Check cooldown first
    last = _last_response_time.get(meeting_id, 0)
    if time.time() - last < RESPONSE_COOLDOWN:
        return False

    # ALWAYS_RESPOND mode: respond to any speech (no wake word needed)
    if ALWAYS_RESPOND:
        # Need at least some meaningful text (not just "um" or "uh")
        text_stripped = transcript_text.strip()
        if len(text_stripped) > 10:  # At least 10 chars of speech
            logger.info("Always-respond mode: triggering on '%s'", text_stripped[:60])
            _claim_response(meeting_id)
            return True
        return False

    # Wake word mode: check for "hey edmo" patterns
    # Build sliding window text (last 5 seconds including this segment)
    window_text = _get_recent_text(meeting_id, transcript_text).lower()
    # Also check just this segment alone
    text_lower = transcript_text.lower().strip()

    # Regex patterns (handle "bot" misrecognitions: but, bought, bart, etc.)
    for regex in BOT_ADDRESS_REGEXES:
        if regex.search(window_text):
            logger.info("Address match (regex/window): %s in '%s'", regex.pattern, window_text[:80])
            _claim_response(meeting_id)
            return True
        if regex.search(text_lower):
            logger.info("Address match (regex): %s in '%s'", regex.pattern, text_lower[:60])
            _claim_response(meeting_id)
            return True

    # Exact substring patterns
    for pattern in BOT_ADDRESS_PATTERNS:
        if pattern in window_text:
            logger.info("Address match (exact/window): '%s' in '%s'", pattern, window_text[:80])
            _claim_response(meeting_id)
            return True
        if pattern in text_lower:
            logger.info("Address match (exact): '%s' in '%s'", pattern, text_lower[:60])
            _claim_response(meeting_id)
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
    prompt = f"""You are EDMO, an AI meeting assistant in a live call. Your name is EDMO. SHORT spoken reply only (2-3 sentences). No markdown, no bullets, no asterisks — this will be spoken aloud.

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
