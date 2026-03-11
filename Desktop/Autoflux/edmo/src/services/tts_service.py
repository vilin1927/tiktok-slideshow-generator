import asyncio
import logging
import os
import random
import tempfile

import httpx

from src.config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Filler phrases — pre-generated and cached for INSTANT playback (~0ms)
# ---------------------------------------------------------------------------
FILLER_PHRASES = {
    "thinking": [
        "Let me think about that.",
        "Good question, one moment.",
        "Hmm, let me check.",
        "Let me look into that.",
        "Give me just a second.",
        "That's a great point, let me think.",
    ],
    "action": [
        "On it.",
        "Sure, pulling that up now.",
        "Absolutely, checking now.",
        "Let me find that for you.",
        "One moment while I check.",
    ],
    "summarize": [
        "Sure, let me recap.",
        "Alright, let me put that together.",
        "Let me summarize what I have so far.",
    ],
    "acknowledge": [
        "Got it.",
        "Sure thing.",
        "Understood.",
        "Of course.",
        "Absolutely.",
    ],
    "greeting_followup": [
        "Happy to help.",
        "I'm here if you need anything.",
        "Just say hey EDMO anytime.",
    ],
}

# Cache: phrase text -> audio bytes (generated once, reused forever)
_filler_cache: dict[str, bytes] = {}
_greeting_cache: bytes | None = None

# Hume Octave API
HUME_TTS_URL = "https://api.hume.ai/v0/tts/stream/file"

# Deepgram fallback
DEEPGRAM_TTS_URL = "https://api.deepgram.com/v1/speak"


# ---------------------------------------------------------------------------
# Main TTS function — Hume Octave (primary), Deepgram Aura-2 (fallback)
# ---------------------------------------------------------------------------
async def text_to_speech(text: str) -> bytes:
    """Convert text to speech audio (MP3). Uses Hume Octave or Deepgram.

    MP3 format required by Recall.ai Output Audio API.
    """
    if settings.hume_api_key:
        return await _hume_tts(text)
    if settings.deepgram_api_key:
        return await _deepgram_tts(text)
    raise ValueError("No TTS API key configured (set HUME_API_KEY or DEEPGRAM_API_KEY)")


async def _hume_tts(text: str) -> bytes:
    """Hume Octave TTS — instant_mode for lowest latency (~200ms TTFA).

    Returns MP3 (required by Recall.ai Output Audio API).
    """
    if len(text) > 4900:
        text = text[:4900] + "..."

    payload = {
        "utterances": [
            {
                "text": text,
                "voice": {
                    "name": settings.hume_voice_name,
                    "provider": "HUME_AI",
                },
                "speed": 1.0,
            }
        ],
        "format": {"type": "mp3"},
        "instant_mode": True,
    }
    headers = {
        "X-Hume-Api-Key": settings.hume_api_key,
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(HUME_TTS_URL, headers=headers, json=payload)
        response.raise_for_status()
        audio_bytes = response.content
        logger.info("Hume TTS: %d chars -> %d bytes", len(text), len(audio_bytes))
        return audio_bytes


async def _deepgram_tts(text: str) -> bytes:
    """Deepgram Aura-2 TTS — fallback if Hume not configured.

    Returns MP3 (Deepgram default output format).
    """
    if len(text) > 1900:
        text = text[:1900] + "..."

    params = "model=aura-2-andromeda-en"
    url = f"{DEEPGRAM_TTS_URL}?{params}"
    headers = {
        "Authorization": f"Token {settings.deepgram_api_key}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(url, headers=headers, json={"text": text})
        response.raise_for_status()
        return response.content


# ---------------------------------------------------------------------------
# Filler phrases — instant playback while AI thinks
# ---------------------------------------------------------------------------
def classify_filler(text: str) -> str:
    """Pick filler category based on what the user asked."""
    t = text.lower()
    if any(w in t for w in ["summarize", "recap", "what was discussed", "what happened", "key points", "overview"]):
        return "summarize"
    if any(w in t for w in ["send", "email", "create", "check", "pull up", "find", "look up", "search", "schedule"]):
        return "action"
    if any(w in t for w in ["yes", "yeah", "correct", "do that", "go ahead", "please do", "sure"]):
        return "acknowledge"
    return "thinking"


async def get_filler_audio(category: str = "thinking") -> tuple[bytes, str]:
    """Get a random filler phrase audio. Cached after first generation."""
    phrases = FILLER_PHRASES.get(category, FILLER_PHRASES["thinking"])
    phrase = random.choice(phrases)

    if phrase in _filler_cache:
        return _filler_cache[phrase], phrase

    try:
        audio = await text_to_speech(phrase)
        _filler_cache[phrase] = audio
        logger.info("Cached filler: '%s' (%d bytes)", phrase, len(audio))
        return audio, phrase
    except Exception as e:
        logger.error("Filler TTS failed for '%s': %s", phrase, e)
        raise


async def pre_cache_fillers():
    """Pre-generate ONE filler phrase per category at startup.

    Only caches 5 phrases (1 per category) with 2s delay between each
    to avoid rate limits. Remaining phrases are cached on-demand.
    Uses a file lock so only one worker pre-caches.
    """
    if not settings.hume_api_key and not settings.deepgram_api_key:
        logger.warning("No TTS key configured — skipping filler pre-cache")
        return

    # File lock: only one uvicorn worker should pre-cache
    lock_path = os.path.join(tempfile.gettempdir(), "mi_filler_cache.lock")
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
    except FileExistsError:
        logger.info("Another worker is pre-caching fillers — skipping")
        return

    try:
        cached = 0
        # Cache only the FIRST phrase from each category (5 total)
        for category, phrases in FILLER_PHRASES.items():
            phrase = phrases[0]
            if phrase not in _filler_cache:
                try:
                    audio = await text_to_speech(phrase)
                    _filler_cache[phrase] = audio
                    cached += 1
                    logger.info("Cached filler [%s]: '%s'", category, phrase)
                except Exception as e:
                    logger.warning("Failed to cache '%s': %s", phrase, e)
                # Rate limit protection: 2s between API calls
                await asyncio.sleep(2)

        # Also cache greeting
        try:
            from src.services.bot_brain import GREETING_TEXT
            global _greeting_cache
            if _greeting_cache is None:
                await asyncio.sleep(2)
                _greeting_cache = await text_to_speech(GREETING_TEXT)
                cached += 1
                logger.info("Greeting cached: %d bytes", len(_greeting_cache))
        except Exception as e:
            logger.warning("Greeting cache failed: %s", e)

        logger.info("Pre-cached %d phrases (5 fillers + greeting)", cached)
    finally:
        # Clean up lock file
        try:
            os.unlink(lock_path)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Greeting — cached for zero-latency on bot join
# ---------------------------------------------------------------------------
async def get_greeting_audio() -> bytes:
    """Get cached greeting audio. Generates once, reuses."""
    global _greeting_cache
    if _greeting_cache is None:
        from src.services.bot_brain import GREETING_TEXT

        _greeting_cache = await text_to_speech(GREETING_TEXT)
        logger.info("Greeting audio cached: %d bytes", len(_greeting_cache))
    return _greeting_cache
