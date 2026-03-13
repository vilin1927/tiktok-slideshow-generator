"""Meeting BaaS + Hume EVI POC routes.

Simple proof of concept:
- POST /api/poc/join — Create bot that joins Google Meet
- WebSocket /ws/evi/{client_id} — Bridge audio to/from EVI
"""

import asyncio
import base64
import json
import logging
from typing import Dict

import httpx
import websockets
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from src.config import settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/poc", tags=["EVI POC"])

# Active EVI sessions
_evi_sessions: Dict[str, any] = {}


@router.post("/join")
async def join_meeting(data: dict):
    """Create a Meeting BaaS speaking bot that joins the meeting."""
    meeting_url = data.get("meeting_url")
    if not meeting_url:
        return {"error": "meeting_url required"}

    if not settings.meetingbaas_api_key:
        return {"error": "MEETINGBAAS_API_KEY not configured"}

    # WebSocket URL where Meeting BaaS will send audio
    base_url = settings.bot_media_base_url or "http://89.117.36.82:8001"
    ws_url = base_url.replace("http://", "ws://").replace("https://", "wss://")

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                "https://speaking.meetingbaas.com/bots",
                json={
                    "meeting_url": meeting_url,
                    "meeting_baas_api_key": settings.meetingbaas_api_key,
                    "personas": ["baas_onboarder"],
                    "bot_name": "EVI Assistant",
                    "websocket_url": f"{ws_url}/api/poc/ws/evi",
                },
                headers={
                    "Content-Type": "application/json",
                    "x-meeting-baas-api-key": settings.meetingbaas_api_key,
                },
            )

            if response.status_code != 200:
                logger.error("Meeting BaaS error: %s %s", response.status_code, response.text)
                return {"error": f"Meeting BaaS returned {response.status_code}"}

            result = response.json()
            logger.info("Meeting BaaS bot created: %s", result)

            return {
                "success": True,
                "bot_id": result.get("bot_id"),
                "client_id": result.get("client_id"),
                "message": "Bot joining meeting...",
            }

    except Exception as e:
        logger.error("Failed to create bot: %s", e)
        return {"error": str(e)}


@router.post("/webhook")
async def meetingbaas_webhook(data: dict):
    """Receive status updates from Meeting BaaS."""
    event_type = data.get("event") or data.get("type")
    logger.info("Meeting BaaS webhook: %s - %s", event_type, data)
    return {"received": True}


@router.websocket("/ws/evi/{client_id}")
async def evi_websocket(websocket: WebSocket, client_id: str):
    """WebSocket endpoint that bridges Meeting BaaS audio to Hume EVI."""
    await websocket.accept()
    logger.info("Meeting BaaS connected: %s", client_id)

    if not settings.hume_api_key or not settings.hume_evi_config_id:
        logger.error("Hume API key or config ID not configured")
        await websocket.close(code=1008, reason="Hume not configured")
        return

    # Connect to Hume EVI
    evi_url = (
        f"wss://api.hume.ai/v0/evi/chat"
        f"?api_key={settings.hume_api_key}"
        f"&config_id={settings.hume_evi_config_id}"
    )

    try:
        async with websockets.connect(evi_url) as evi_ws:
            logger.info("Connected to Hume EVI for client %s", client_id)
            _evi_sessions[client_id] = evi_ws

            async def forward_to_evi():
                """Forward audio from Meeting BaaS to EVI."""
                try:
                    while True:
                        data = await websocket.receive()

                        if "bytes" in data:
                            # Binary audio data - forward to EVI
                            audio_bytes = data["bytes"]
                            audio_b64 = base64.b64encode(audio_bytes).decode()
                            await evi_ws.send(json.dumps({
                                "type": "audio_input",
                                "data": audio_b64,
                            }))

                        elif "text" in data:
                            # Control message from Meeting BaaS
                            try:
                                msg = json.loads(data["text"])
                                logger.info("Control message: %s", msg.get("type"))
                            except json.JSONDecodeError:
                                pass

                except WebSocketDisconnect:
                    logger.info("Meeting BaaS disconnected: %s", client_id)
                except Exception as e:
                    logger.error("Forward to EVI error: %s", e)

            async def forward_from_evi():
                """Forward audio from EVI to Meeting BaaS."""
                try:
                    async for message in evi_ws:
                        msg = json.loads(message)
                        msg_type = msg.get("type")

                        if msg_type == "audio_output":
                            # EVI audio response - forward to meeting
                            audio_bytes = base64.b64decode(msg.get("data", ""))
                            await websocket.send_bytes(audio_bytes)
                            logger.info("Sent %d bytes audio to meeting", len(audio_bytes))

                        elif msg_type == "assistant_message":
                            # EVI text response (for logging)
                            content = msg.get("message", {})
                            if isinstance(content, dict):
                                text = content.get("content", "")
                            else:
                                text = str(content)
                            logger.info("EVI said: %s", text[:100])

                        elif msg_type == "user_message":
                            # User transcript (for logging)
                            content = msg.get("message", {})
                            if isinstance(content, dict):
                                text = content.get("content", "")
                            else:
                                text = str(content)
                            logger.info("User said: %s", text[:100])

                except Exception as e:
                    logger.error("Forward from EVI error: %s", e)

            # Run both directions concurrently
            await asyncio.gather(
                forward_to_evi(),
                forward_from_evi(),
                return_exceptions=True,
            )

    except Exception as e:
        logger.error("EVI connection failed: %s", e)

    finally:
        _evi_sessions.pop(client_id, None)
        logger.info("Session ended: %s", client_id)
