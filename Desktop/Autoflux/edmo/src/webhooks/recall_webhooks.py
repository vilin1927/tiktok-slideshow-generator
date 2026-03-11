import asyncio
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database import get_db
from src.models import Meeting, Speaker
from src.routes.websocket import broadcast_to_meeting
from src.services import bot_brain, recall_service, transcript_manager, tts_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/webhooks/recall", tags=["webhooks"])


def _extract_bot_id(body: dict) -> str | None:
    """Extract bot_id from Recall.ai webhook payload (supports multiple formats)."""
    # realtime_endpoints format: data.bot.id
    event_data = body.get("data", {})
    bot_id = event_data.get("bot", {}).get("id")
    if bot_id:
        return bot_id
    # Legacy / direct format
    return body.get("bot_id") or event_data.get("bot_id")


@router.post("/transcript")
async def handle_transcript_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """Handle real-time transcript data from Recall.ai.

    Receives transcript via realtime_endpoints webhook, stores in DB,
    broadcasts to dashboard, and triggers bot brain response if addressed.
    """
    body = await request.json()
    bot_id = _extract_bot_id(body)

    if not bot_id:
        logger.warning("Transcript webhook missing bot_id: %s", body)
        return {"status": "ignored"}

    # Find the meeting
    result = await db.execute(select(Meeting).where(Meeting.recall_bot_id == bot_id))
    meeting = result.scalar_one_or_none()
    if not meeting:
        logger.warning("No meeting found for bot %s", bot_id)
        return {"status": "not_found"}

    # Extract transcript data — handle both realtime_endpoints and legacy formats
    event_data = body.get("data", {})
    transcript_data = event_data.get("data", {})
    if not transcript_data:
        # Legacy format fallback
        transcript_data = event_data.get("transcript", event_data)

    participant = transcript_data.get("participant", {})
    speaker_name = participant.get("name", "Unknown")
    words = transcript_data.get("words", [])

    if not words:
        return {"status": "no_words"}

    # Combine words into text
    text = " ".join(w.get("text", "") for w in words if w.get("text"))
    if not text.strip():
        return {"status": "empty"}

    # Timestamps
    start_time = None
    end_time = None
    if words:
        first_ts = words[0].get("start_timestamp", {})
        last_ts = words[-1].get("end_timestamp", {})
        start_time = first_ts.get("relative")
        end_time = last_ts.get("relative")

    # Store transcript segment
    segment = await transcript_manager.add_transcript_segment(
        db=db,
        meeting_id=meeting.id,
        speaker_name=speaker_name,
        text=text.strip(),
        start_time=start_time,
        end_time=end_time,
    )
    await db.commit()

    # Broadcast to WebSocket clients (dashboard live transcript)
    await broadcast_to_meeting(
        str(meeting.id),
        {
            "type": "transcript",
            "speaker": speaker_name,
            "text": text.strip(),
            "start_time": start_time,
            "end_time": end_time,
            "segment_id": str(segment.id),
        },
    )

    logger.info("Transcript: [%s] %s", speaker_name, text.strip()[:80])

    # Bot brain: check if bot should respond
    meeting_id_str = str(meeting.id)
    if bot_brain.should_respond(meeting_id_str, text.strip()):
        asyncio.create_task(
            _bot_respond(meeting.recall_bot_id, meeting_id_str, text.strip(), speaker_name)
        )
    else:
        bot_brain.add_context(meeting_id_str, speaker_name, text.strip())

    return {"status": "ok"}


async def _bot_respond(bot_id: str, meeting_id: str, text: str, speaker: str):
    """Generate AI response and push audio to bot via Output Audio API.

    Streaming pipeline:
    1. Push filler phrase INSTANTLY (pre-cached MP3, ~0ms)
    2. Gemini streams response → sentences yielded in real-time
    3. Each sentence → TTS → push to bot (stop if bot left call)
    """
    try:
        # Step 1: Filler phrase — instant while AI thinks
        try:
            filler_category = tts_service.classify_filler(text)
            filler_audio, filler_text = await tts_service.get_filler_audio(filler_category)
            ok = await recall_service.push_audio_to_bot(bot_id, filler_audio)
            if not ok:
                logger.warning("Bot %s left call, aborting response", bot_id)
                return
            logger.info("Filler pushed for %s: '%s'", meeting_id, filler_text)
        except Exception as e:
            logger.warning("Filler failed (continuing): %s", e)

        # Step 2-3: Stream Gemini → TTS → push (stop if bot leaves)
        sentence_count = 0
        async for sentence in bot_brain.generate_response_stream(meeting_id, text, speaker):
            try:
                audio_bytes = await tts_service.text_to_speech(sentence)
                ok = await recall_service.push_audio_to_bot(bot_id, audio_bytes)
                if not ok:
                    logger.warning("Bot %s left call mid-response, stopping", bot_id)
                    break
                sentence_count += 1
            except Exception as e:
                logger.error("TTS/push failed for sentence in %s: %s", meeting_id, e)

        if sentence_count == 0:
            logger.warning("No sentences generated for %s", meeting_id)
        else:
            logger.info("Pushed %d sentences for meeting %s", sentence_count, meeting_id)
    except Exception as e:
        logger.error("Bot respond failed for %s: %s", meeting_id, e)


@router.post("/events")
async def handle_events_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """Handle participant events from Recall.ai (join, leave, speech)."""
    body = await request.json()
    event = body.get("event", "")
    bot_id = _extract_bot_id(body)

    if not bot_id:
        return {"status": "ignored"}

    result = await db.execute(select(Meeting).where(Meeting.recall_bot_id == bot_id))
    meeting = result.scalar_one_or_none()
    if not meeting:
        return {"status": "not_found"}

    data = body.get("data", body)
    participant = data.get("participant", {})
    participant_name = participant.get("name", "Unknown")

    if "join" in event:
        # Participant joined — create/update speaker record
        await transcript_manager.get_or_create_speaker(db, meeting.id, participant_name)
        await db.commit()

        await broadcast_to_meeting(
            str(meeting.id),
            {"type": "participant_join", "name": participant_name},
        )
        logger.info("Participant joined: %s (meeting %s)", participant_name, meeting.id)

    elif "leave" in event:
        await broadcast_to_meeting(
            str(meeting.id),
            {"type": "participant_leave", "name": participant_name},
        )
        logger.info("Participant left: %s (meeting %s)", participant_name, meeting.id)

    elif "speech_on" in event:
        await broadcast_to_meeting(
            str(meeting.id),
            {"type": "speaking", "name": participant_name, "speaking": True},
        )

    elif "speech_off" in event:
        await broadcast_to_meeting(
            str(meeting.id),
            {"type": "speaking", "name": participant_name, "speaking": False},
        )

    return {"status": "ok"}


@router.post("/status")
async def handle_status_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """Handle bot status changes from Recall.ai."""
    body = await request.json()
    bot_id = _extract_bot_id(body)
    status_code = body.get("data", {}).get("status", {}).get("code", "")

    if not bot_id:
        return {"status": "ignored"}

    result = await db.execute(select(Meeting).where(Meeting.recall_bot_id == bot_id))
    meeting = result.scalar_one_or_none()
    if not meeting:
        return {"status": "not_found"}

    meeting.recall_bot_status = status_code
    logger.info("Bot %s status: %s", bot_id, status_code)

    if status_code == "bot.in_call_recording":
        meeting.status = "active"
        meeting.started_at = datetime.now(timezone.utc)
    elif status_code == "bot.call_ended":
        meeting.status = "processing"
        meeting.ended_at = datetime.now(timezone.utc)
        if meeting.started_at:
            meeting.duration_seconds = int(
                (meeting.ended_at - meeting.started_at).total_seconds()
            )
    elif status_code == "bot.done":
        # Trigger AI processing
        await db.commit()
        await transcript_manager.process_meeting_ai(db, meeting.id)
        return {"status": "processing_complete"}
    elif status_code == "bot.fatal":
        meeting.status = "failed"

    await db.commit()

    await broadcast_to_meeting(
        str(meeting.id),
        {"type": "status", "status": meeting.status, "bot_status": status_code},
    )

    return {"status": "ok"}
