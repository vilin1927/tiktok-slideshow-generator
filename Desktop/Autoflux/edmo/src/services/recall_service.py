import base64
import logging

import httpx

from src.config import settings

logger = logging.getLogger(__name__)

RECALL_BASE = settings.recall_base_url
RECALL_HEADERS = {
    "Authorization": f"Token {settings.recall_api_key}",
    "Content-Type": "application/json",
}


async def create_bot(
    meeting_url: str,
    bot_name: str = "Meeting Assistant",
    meeting_id: str | None = None,
) -> dict:
    """Create a Recall.ai bot that joins a meeting.

    Architecture: Output Audio API (direct MP3 push, no webpage rendering).
    - automatic_audio_output: plays greeting MP3 when recording starts
    - realtime_endpoints: webhook receives live transcript from Recall.ai
    - push_audio_to_bot(): sends AI response audio during the call

    Previous Output Media approach failed because:
    1. AudioContext autoplay blocked in Recall.ai's headless Chrome renderer
    2. wss://meeting-data.bot.recall.ai transcript WebSocket sent no data
    Switched to Output Audio + webhooks — simpler, more reliable.
    """
    webhook_base = settings.bot_media_base_url.rstrip("/")

    payload = {
        "meeting_url": meeting_url,
        "bot_name": bot_name,
        "recording_config": {
            "transcript": {
                "provider": {
                    "recallai_streaming": {
                        "language_code": "en",
                        "mode": "prioritize_low_latency",
                    }
                }
            },
            "realtime_endpoints": [
                {
                    "type": "webhook",
                    "url": f"{webhook_base}/api/webhooks/recall/transcript",
                    "events": ["transcript.data", "transcript.partial_data"],
                },
            ],
        },
    }

    # Attach greeting MP3 for automatic playback on join
    try:
        from src.services.tts_service import get_greeting_audio

        greeting_mp3 = await get_greeting_audio()
        greeting_b64 = base64.b64encode(greeting_mp3).decode("ascii")
        payload["automatic_audio_output"] = {
            "in_call_recording": {
                "data": {
                    "kind": "mp3",
                    "b64_data": greeting_b64,
                },
                "replay_on_participant_join": {
                    "debounce_mode": "trailing",
                    "debounce_interval": 10,
                    "disable_after": 120,
                },
            }
        }
        logger.info("Greeting MP3 attached (%d bytes)", len(greeting_mp3))
    except Exception as e:
        logger.warning("No greeting audio (TTS unavailable): %s", e)

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            f"{RECALL_BASE}/api/v1/bot/", headers=RECALL_HEADERS, json=payload
        )
        response.raise_for_status()
        data = response.json()
        logger.info("Recall.ai bot created: %s", data.get("id"))
        return data


async def push_audio_to_bot(bot_id: str, mp3_bytes: bytes) -> bool:
    """Push MP3 audio to bot — bot speaks it in the meeting.

    Uses Recall.ai Output Audio endpoint (POST /api/v1/bot/{id}/output_audio/).
    Requires automatic_audio_output configured at bot creation.
    Rate limit: 300 req/min per workspace.
    Returns False if bot is no longer in call (graceful skip).
    """
    b64 = base64.b64encode(mp3_bytes).decode("ascii")
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            f"{RECALL_BASE}/api/v1/bot/{bot_id}/output_audio/",
            headers=RECALL_HEADERS,
            json={"kind": "mp3", "b64_data": b64},
        )
        if response.status_code == 400:
            error_body = response.text
            if "cannot_command" in error_body or "not_in_call" in error_body:
                logger.warning("Bot %s no longer in call, skipping audio push", bot_id)
                return False
            logger.error("Output audio 400 for bot %s: %s", bot_id, error_body)
            return False
        response.raise_for_status()
        logger.info("Audio pushed to bot %s (%d bytes)", bot_id, len(mp3_bytes))
        return True


async def get_bot(bot_id: str) -> dict:
    """Get bot status and details."""
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(f"{RECALL_BASE}/api/v1/bot/{bot_id}/", headers=RECALL_HEADERS)
        response.raise_for_status()
        return response.json()


async def get_bot_transcript(bot_id: str) -> list[dict]:
    """Get the full transcript from a completed bot.

    Uses the recording artifact approach (new API):
    1. Get bot → recordings[0].media_shortcuts.transcript.data.download_url
    2. Download transcript JSON from S3
    Falls back to speaker_timeline with participant names if transcript is null.
    """
    bot_data = await get_bot(bot_id)
    recordings = bot_data.get("recordings", [])
    if not recordings:
        logger.warning("Bot %s has no recordings", bot_id)
        return []

    recording = recordings[0]
    shortcuts = recording.get("media_shortcuts", {})

    # Try transcript artifact first
    transcript_shortcut = shortcuts.get("transcript")
    if transcript_shortcut and transcript_shortcut.get("data"):
        download_url = transcript_shortcut["data"].get("download_url")
        if download_url:
            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.get(download_url)
                response.raise_for_status()
                transcript_data = response.json()
                logger.info("Downloaded transcript for bot %s: %d entries", bot_id, len(transcript_data))
                return transcript_data

    # Fallback: get speaker_timeline (has speaker info but no text)
    participant_events = shortcuts.get("participant_events")
    if participant_events and participant_events.get("data"):
        timeline_url = participant_events["data"].get("speaker_timeline_download_url")
        if timeline_url:
            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.get(timeline_url)
                response.raise_for_status()
                timeline = response.json()
                logger.info("Got speaker timeline for bot %s: %d entries (no text)", bot_id, len(timeline))
                return timeline

    logger.warning("Bot %s has no transcript or speaker timeline data", bot_id)
    return []


async def leave_call(bot_id: str) -> dict:
    """Tell the bot to leave the meeting."""
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            f"{RECALL_BASE}/api/v1/bot/{bot_id}/leave_call/", headers=RECALL_HEADERS
        )
        response.raise_for_status()
        return response.json()


async def get_speaker_timeline(bot_id: str) -> dict:
    """Get speaker timeline for diarization."""
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(
            f"{RECALL_BASE}/api/v1/bot/{bot_id}/speaker_timeline/", headers=RECALL_HEADERS
        )
        response.raise_for_status()
        return response.json()


async def list_bots(limit: int = 20) -> list[dict]:
    """List recent bots."""
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(
            f"{RECALL_BASE}/api/v1/bot/",
            headers=RECALL_HEADERS,
            params={"limit": limit},
        )
        response.raise_for_status()
        return response.json()
