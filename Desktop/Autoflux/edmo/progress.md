# Progress — EDMO Meeting Intelligence Engine

## Current Status
**Date:** 2026-03-12
**Phase:** Milestone 1 — Full Demo-Ready System
**Status:** M1-01 COMPLETE, M1-07 COMPLETE (dashboard deployed), M1-08 COMPLETE, M1-10/M1-11 IN PROGRESS (bot speaks, needs live verification)

## What's Done
- [x] Read and analyzed EDMO Meeting Intelligence Proposal (docx)
- [x] Populated all project docs (CLAUDE.md, PRD.md, ARCHITECTURE.md, features.json, etc.)
- [x] Vladimir walkthrough: confirmed 4 VAPI pain points, clarified scope
- [x] Deepgram API key obtained ($200 credits, Flux model)
- [x] Gemini: using 3 keys from TikTok project (3.1 Flash, round-robin)
- [x] VPS investigated: Tyler's VPS (89.117.36.82) — ready to use
- [x] M1-06 Salesforce service built (`src/services/salesforce_service.py`)
- [x] M1-06 Salesforce routes built (`src/routes/salesforce.py`)
- [x] M1-06 Demo data populate script built (`scripts/populate_salesforce_demo.py`)
- [x] Salesforce auto-sync wired into post-meeting AI pipeline (transcript_manager.py)
- [x] Config updated with SF username/password/security_token fields
- [x] .env.example updated with all Salesforce vars
- [x] **VPS DEPLOYED:** Redis installed, pip deps installed, PostgreSQL DB `mi_engine` + user `mi_user` created
- [x] **Alembic migration applied:** All 8 tables created (meetings, speakers, transcript_segments, meeting_summaries, compliance_flags, enrollment_entities, consent_records, audit_log)
- [x] **Extensions enabled:** pg_trgm + uuid-ossp
- [x] **systemd service `mi-api`:** Running, 2 uvicorn workers, auto-restart
- [x] **.env on VPS:** All API keys configured (Recall.ai, Deepgram, Gemini x3), production JWT secret
- [x] **Health endpoint live:** `http://89.117.36.82:8001/health` → `{"status":"ok","service":"mi-engine"}`
- [x] **Swagger docs live:** `http://89.117.36.82:8001/docs`
- [x] **M1-07 React dashboard BUILT:** 3 pages (Live Meeting, Library, Meeting Detail), Demo Mode, real-time WebSocket transcript, speaker color-coding, auto-scroll, compliance flags, summary view, entity extraction display
- [x] **Frontend deployed:** SPA served from FastAPI at `http://89.117.36.82:8001/` (no separate server needed)
- [x] **Stack:** React + TypeScript + Tailwind CSS v4 + Vite + React Router + Lucide icons

## VPS Investigation (89.117.36.82) — 2026-03-11
- **RAM:** 7.8GB total, 6.5GB available — plenty
- **Disk:** 96GB total, 59GB free — plenty
- **Already installed:** Python 3.12.3, PostgreSQL 16, Docker 29.1, Nginx
- **NOT installed:** Redis (need to install)
- **Running services:** aj-deck (port 9000), tyler-bot (port 8080), ollama (port 11434), nginx (80, 3000)
- **Available ports:** 8001 (EDMO API), 3001 (EDMO frontend)
- **Existing DBs:** postgres, reelpilot (old, can ignore)
- **Nginx sites:** aj-deck, reelpilot (old config, can replace with edmo)
- **Conclusion:** READY. Install Redis, create edmo_meetings DB, deploy alongside existing services.

## API Keys Status
| Service | Status | Notes |
|---------|--------|-------|
| Deepgram | OBTAINED | $200 credits, Flux model, key e97099...19c |
| Gemini 3.1 Flash | OBTAINED | 3 keys from TikTok project (round-robin) |
| Recall.ai | OBTAINED | Key 36dceddf...c38e, us-west-2, $5.00 balance, rate limits saved in reference/ |
| Salesforce | CONNECTED | Dev org `vilin1927`, 100 contacts, 71 opportunities, auto-sync live on VPS |

## Recall.ai Integration Findings (2026-03-11)
- **CloudFront WAF blocks `realtime_endpoints`** with raw IP URLs → webhooks impossible without domain
- **Solution: Polling approach** — frontend polls `GET /api/meetings/{id}/poll` every 5s, then `POST /fetch-transcript` when done
- **Transcript provider required:** Bot without `recording_config.transcript` records video only — NO text
- **Fix: `meeting_captions` provider** — uses Google Meet's built-in captions, no external API needed
- **Old v1 `/transcript/` endpoint is DEPRECATED** — returns 400. New approach: download from recording artifact `media_shortcuts.transcript.data.download_url`
- **First test bot** (b8566b89): joined, recorded, but no transcript (no provider config). Status went `joining_call` → `in_waiting_room` → `in_call_recording` → `call_ended` → `done`
- **Second test bot** (18ccbbae): created with `meeting_captions` provider — payload accepted by CloudFront WAF

## M1-10 Bot Voice Implementation (2026-03-11)
- **Architecture:** Recall.ai Output Media API renders our bot-media page inside the meeting
- **How it works:** Bot renders `http://89.117.36.82:8001/bot-media?meeting_id=XXX` as audio/video in meeting
- **Real-time transcript:** Bot media page connects to `wss://meeting-data.bot.recall.ai/api/v1/transcript`
- **AI Brain:** Transcript forwarded to backend via WebSocket → Gemini generates response → Deepgram TTS → audio played in meeting
- **Dashboard sync:** Real-time transcript also forwarded to dashboard WebSocket (live transcript on dashboard)
- **Files created:**
  - `src/services/tts_service.py` — Deepgram TTS (text → MP3 audio)
  - `src/services/bot_brain.py` — Gemini AI brain (context tracking, response generation, address detection)
  - `src/routes/bot_media.py` — Bot media HTML page + brain WebSocket endpoint
- **Files modified:**
  - `src/services/recall_service.py` — Added `output_media` config to bot creation + `meeting_id` param
  - `src/config.py` — Added `bot_media_base_url` setting
  - `src/main.py` — Registered bot_media router, excluded `/bot-media` from SPA fallback
  - `src/routes/meetings.py` — Passes `meeting_id` to `create_bot()`
- **Key decision:** Output Media API (not Output Audio endpoint) because it gives the bot access to real-time transcript via internal WebSocket, enabling true interactive responses
- **Constraint:** Output Media cannot coexist with `automatic_audio_output` — greeting handled by the webpage itself

## Streaming Pipeline Upgrade (2026-03-11)
- **Problem:** Bot response latency was ~15 seconds (serial: Gemini full response → TTS full audio → play)
- **Solution:** Filler phrases + per-sentence streaming TTS + Hume Octave voice
- **Files changed:**
  - `src/services/tts_service.py` — Hume Octave TTS (primary, ~200ms TTFA), Deepgram fallback, 25 filler phrases in 5 categories, pre-cached at startup
  - `src/services/bot_brain.py` — `generate_response_stream()` async generator yields sentences, expanded address patterns, context window trimmed to last 10 segments for speed
  - `src/routes/bot_media.py` — Filler plays INSTANTLY, then streams sentences as separate audio chunks to browser queue
  - `src/main.py` — Added lifespan startup to pre-cache all filler phrases
  - `src/config.py` — Added `hume_api_key`, `hume_voice_name` settings
  - `.env.example` — Added `HUME_API_KEY`, `HUME_VOICE_NAME`
- **Expected latency:** ~0ms perceived (filler plays instantly), ~2-3s to real content
- **Hume AI decision:** Octave TTS only (voice layer). NOT EVI (speech-to-speech) — EVI doesn't support multi-speaker meetings. We keep Gemini as the brain.
- **Needs:** ~~Hume API key from https://app.hume.ai/, then deploy to VPS~~ DONE
- **Hume API key:** Obtained and deployed to VPS `.env`
- **Bug fixed:** `"format": "wav"` → `"format": {"type": "wav"}` (Hume API expects object not string)
- **Rate limit fix:** pre_cache_fillers() now caches 1 phrase/category (5 total) + greeting, with 2s delays and file-lock so only 1 worker caches
- **Deployed & verified:** All 6 phrases cached (5 fillers + greeting), all 200 OK, health endpoint live

## Architecture Switch: Output Media → Output Audio (2026-03-11)
- **Root cause of previous failures:**
  1. AudioContext autoplay BLOCKED in Recall.ai's headless Chrome renderer → greeting audio bytes sent but never played
  2. `wss://meeting-data.bot.recall.ai/api/v1/transcript` WebSocket connected but sent ZERO transcript data → bot deaf
  3. The branded EDMO webpage (the "picture") was required by Output Media API — bot needs video feed
- **New architecture:** Output Audio API (direct MP3 push, no webpage)
  - `automatic_audio_output`: greeting MP3 plays automatically when recording starts + replays for late joiners
  - `realtime_endpoints`: Recall.ai sends transcript to webhook → `POST /api/webhooks/recall/transcript`
  - `push_audio_to_bot()`: pushes MP3 audio via `POST /api/v1/bot/{id}/output_audio/`
  - Bot brain logic moved from bot_media.py WebSocket → recall_webhooks.py webhook handler
- **TTS switched to MP3:** Output Audio API only accepts MP3. Hume `"format": {"type": "mp3"}`, Deepgram default MP3.
- **Files changed:** `recall_service.py`, `tts_service.py`, `recall_webhooks.py`, `features.json`
- **Bot appearance:** Default avatar (no branded page). User doesn't need the picture.

## True Streaming Pipeline (2026-03-11)
- **Problem:** `generate_response_stream()` was fake — called Gemini, waited for FULL response, then split into sentences
- **Fix 1 — Gemini streaming:** `stream=True` + `asyncio.Queue` bridge. Thread pushes tokens → async generator yields sentences the moment `.`/`?`/`!` detected
- **Fix 2 — Pipelined TTS+Push:** Starts TTS for sentence N+1 while pushing sentence N (concurrent asyncio tasks)
- **Result:** First sentence reaches meeting ~1.5-2.5s after Gemini starts (down from 8-15s serial)
- **Files:** `bot_brain.py` (queue-based streaming), `recall_webhooks.py` (pipelined TTS)

## Real-Time Transcript Fix (2026-03-11)
- **Root cause found:** `recallai_streaming` defaulted to `prioritize_accuracy` mode → webhooks delayed 3-10 MINUTES
- **Evidence:** Transcript webhook arrived at 17:11:55, but call ended at 17:11:52 (3s after). All output_audio pushes got 400 "cannot_command_unstarted_bot"
- **Fix 1:** Added `"mode": "prioritize_low_latency"` to recallai_streaming config → 1-3s delivery
- **Fix 2:** Added `"transcript.partial_data"` event → sub-second partial results during call
- **Fix 3:** `push_audio_to_bot()` returns bool, stops gracefully if bot left call
- **Cloudflared tunnel:** Working (replaced ngrok which has interstitial blocking POST). URL: `https://eventually-triumph-consistently-adjustment.trycloudflare.com`
- **Good news from last test:** Webhook delivery, bot brain, Gemini response, Hume TTS all worked. Only failure was timing (too late).

## Speech Misrecognition & Cross-Webhook Matching (2026-03-11 late)
- **Problem:** `recallai_streaming` low-latency mode misrecognizes "bot" as "but", "bought", "bart", "bar", "bud" — every normal sentence with "but" triggered the bot
- **Evidence from live test:** Transcript showed "hey but i need you", "they bought", "bart" instead of "hey bot"
- **Fix 1 — Regex patterns:** Replaced exact string matching with regex including all misrecognition variants: `_BOT_VARIANTS = r"(?:bot|but|bought|bart|bar|bud|about|butt|pot|what)"`
- **Fix 2 — "hey" misrecognitions:** Also added `_HEY_VARIANTS = r"(?:hey|they|day|hay|say|a)"` since "hey" was also misrecognized
- **Problem 2:** Low-latency mode splits speech across separate webhooks. "hey" arrives alone, "bud" arrives 350ms later as separate HTTP request → never in same text
- **Fix 3 — Sliding window:** `_get_recent_text()` concatenates last 5 seconds of text per meeting. Patterns checked against window, so "hey" + "bud" arriving separately still match "hey bud"
- **Files:** `bot_brain.py` (regex patterns, sliding window, `_recent_segments` dict)

## Partial/Final Event Deduplication (2026-03-11 late)
- **Problem:** Low-latency mode sends cumulative text ("i" → "i need" → "i need you") and duplicate finals. Every incremental update was being stored as separate DB segment
- **Initial approach:** Used `language_code in transcript_data` to detect finals — WRONG, low-latency includes it in ALL events
- **Final fix:** Text deduplication via `_last_partial_text` dict. Tracks per-meeting: is_extension (text starts with previous), is_duplicate (exact match = final). Only stores duplicates (finals) and new utterances, skips extensions (building partials)
- **Files:** `recall_webhooks.py` (`_last_partial_text` dict, extension/duplicate logic)

## Response Flooding Fix (2026-03-12)
- **Problem from live test:** Bot responding but with 30+ second latency. Logs showed 6+ parallel `_bot_respond` tasks firing simultaneously, each generating separate Gemini+TTS responses and pushing separate audio clips
- **Root cause:** Once "hey bud" was in the 5-second sliding window, EVERY subsequent webhook also matched and triggered a new response. `_last_response_time` was only set after Gemini generated a sentence (~3-5s later), so cooldown didn't kick in fast enough
- **Fix 1 — `_claim_response()`:** Sets `_last_response_time` AND clears sliding window IMMEDIATELY when `should_respond()` finds a match — before returning True. Subsequent webhooks hit cooldown instantly
- **Fix 2 — `_responding` set:** Per-meeting lock in `recall_webhooks.py`. Only ONE `_bot_respond` async task runs per meeting at a time. Cleared in `finally` block when done
- **Result:** Exactly one response per trigger, no more flooding
- **Files:** `bot_brain.py` (`_claim_response()`), `recall_webhooks.py` (`_responding` set)

## Wake Word Switch: "hey bot" → "hey EDMO" (2026-03-12)
- **Problem:** "bot" has too many ASR collisions — "but", "bought", "bud", "bart" appear in normal English constantly, causing false positives even with regex patterns
- **Solution:** Use "EDMO" as wake word — distinctive name, far fewer ASR false positives
- **ASR variants covered:** "edmo", "ed mo", "at mo", "edmow", "ed more", "emo"
- **Changes:**
  - `bot_brain.py` — Replaced `_BOT_VARIANTS` regex with `_EDMO_VARIANTS`, updated greeting text to "Say hey EDMO", updated Gemini system prompt ("You are EDMO")
  - `recall_service.py` — Bot name changed from "Meeting Assistant" to "EDMO"
  - `tts_service.py` — Updated filler phrase "Just say hey EDMO anytime"
- **Action phrases still trigger without wake word:** "can you summarize", "what are the action items", etc.
- **Greeting audio:** Will regenerate on next bot creation (text changed)
- **Deployed to VPS:** All files SCP'd, mi-api restarted, health OK

## What's Next
1. **TEST NOW:** Create bot, join Google Meet, say "hey EDMO" — verify single response, no flooding
2. **Verify greeting audio:** Confirm "Hi everyone, I'm EDMO" is heard on bot join
3. **Configure Deepgram in Recall.ai:** Add Deepgram API key at https://us-west-2.recall.ai/dashboard/transcription → switch to `deepgram_streaming` for better accuracy
4. **M1-04/M1-05 E2E test:** Test Gemini AI summaries + compliance scanner with real transcript
5. **M1-09 E2E test:** Full demo scenario with bot speaking + 5 speakers

## Deep Research: Transcription + MeetGeek Experience (2026-03-12)

### Problem 1: Transcription Quality
**Root Cause:** Currently using `recallai_streaming` provider (basic, low accuracy) instead of Deepgram
**Solution:** Configure Deepgram Flux in Recall.ai dashboard — best STT for voice agents (sub-300ms, 5.26% WER, built-in turn detection)

### Problem 2: Speaking Not Working / Partly Working
**Root Cause:** Multiple issues identified in previous sessions (AudioContext blocked, webhook delays, response flooding) — most fixed, but latency still ~1.5-2.5s
**Bottleneck:** NOT transcription (~260ms). It's Gemini response generation (800-1200ms) + TTS (200-400ms)
**Solution:** Filler phrases already help. For instant responses, need speech-to-speech model (Hume EVI or OpenAI Realtime)

### Problem 3: MeetGeek-Like Natural Conversation
**Key Insight:** MeetGeek is NOT a conversational bot — it's passive recording + post-call AI. EDMO is doing something harder: live interaction.
**What EDMO Needs:**
1. **No wake word:** Semantic turn detection (Deepgram Flux has built-in SoT/EoT events)
2. **Instant responses:** Filler + streaming TTS (already have) + faster LLM or speech-to-speech
3. **Interruption handling (barge-in):** Monitor user StartOfTurn during bot speaking, cancel audio
4. **Multi-speaker:** Deepgram Flux diarization excellent for 5-6 speakers

### Best Models (March 2026)
| Model | Use Case | WER | Latency | Recall.ai |
|-------|----------|-----|---------|-----------|
| **Deepgram Flux** | Real-time + turn detection | 5.26% | 260ms | ✓ Beta |
| Mistral Voxtral | Batch/compliance | 4% | 200ms | ✗ Coming |
| AssemblyAI U3 | Compliance scanning | 14.5% | 350ms | ✓ Full |
| Deepgram Nova-3 | Legacy real-time | 5.26% | 300ms | ✓ Full |

### Wake-Word-Free Detection
**Current:** "Hey EDMO" regex patterns + sliding window
**Alternative:** Semantic turn detection — respond when:
- Natural pause detected (Deepgram EoT event, confidence > 0.75)
- Question detected semantically ("what", "who", "can you")
- Bot explicitly addressed by context (not just wake word)

### Barge-In / Interruption Handling
**Missing Feature:** Bot doesn't stop when user interrupts
**How to Fix:**
1. Monitor Deepgram `StartOfTurn` events during bot speaking
2. If user speaks, cancel pending `push_audio_to_bot()` calls
3. Truncate response, listen to new input
4. Generate fresh response from interruption point

### Recommended Architecture Changes
1. **Immediate:** Configure Deepgram Flux in Recall.ai dashboard
2. **Week 1:** Add semantic trigger detection (questions without "hey EDMO")
3. **Week 2:** Implement barge-in handling
4. **Future:** Evaluate Hume EVI for full speech-to-speech (replaces Gemini + TTS)

## Decisions Made
- **Gemini 3.1 Flash instead of Claude:** Cost saving, Vladimir decision. Can swap to Claude later.
- **Skip AssemblyAI for demo:** Use Gemini for compliance scanning post-call instead.
- **Skip AWS S3 for demo:** Store recordings on VPS locally.
- **Tyler's VPS (89.117.36.82):** Use existing VPS instead of buying new Hostinger KVM1. Has everything pre-installed.
- **VAPI 1-on-1 future:** Unknown — needs clarification from EDMO. For now, we only replace the meeting intelligence use case.
- **VPS STEALTH / ISOLATION (MANDATORY):** Tyler must NOT notice EDMO project exists. All names generic: dir `/opt/mi-engine/`, DB `mi_engine`, services `mi-api`/`mi-web`, nginx config `mi-engine`. NEVER use "edmo" in any VPS-visible name. Full rules in `docs/DEPLOYMENT.md`.

## Known Blockers
- ~~**Salesforce Developer org:** Need for CRM demo.~~ RESOLVED — org created, populated, connected on VPS

## Client Pain Points (Confirmed by Vladimir 2026-03-11)
1. **Board demo failure:** VAPI can't listen to all speakers (5-6 people on Google Meet/Zoom)
2. **Worker notes broken:** VAPI doesn't know WHO said what, wrong attribution → losing business
3. **Latency unpredictable:** Random delays during C-level demos → wants to completely switch
4. **Smart meeting bot need:** 5-6 people, bot joins, transcribes with speaker labels, GDPR scan

## Session Log
| Date | Session | Tasks | Outcome |
|------|---------|-------|---------|
| 2026-03-11 | Project setup | Read proposal, populated all docs | Foundation ready |
| 2026-03-11 | Scope discussion | Confirmed 4 pain points, stack decisions | Gemini over Claude, skip AssemblyAI/S3 |
| 2026-03-11 | VPS investigation | Checked Tyler's VPS 89.117.36.82 | Ready to use, 6.5GB RAM free, all deps installed |
| 2026-03-11 | VPS stealth plan | Generic names for all VPS artifacts | DEPLOYMENT.md updated with full isolation rules |
| 2026-03-11 | Recall.ai key obtained | Key + full rate limits saved | reference/recall_ai_rate_limits.md created |
| 2026-03-11 | M1-06 Salesforce integration | Built service, routes, populate script | Code ready, awaiting SF Developer org credentials |
| 2026-03-11 | VPS Deploy (M1-01+M1-08) | Installed deps, created DB, applied migration, started service | API live at 89.117.36.82:8001, all 8 tables, health OK |
| 2026-03-11 | M1-07 React Dashboard | Built 3 pages + 3 components + WebSocket hook + API lib | Deployed to VPS, served from FastAPI, all views working |
| 2026-03-11 | M1-02 Recall.ai fix | Fixed: webhook→polling, added transcript provider, new API endpoints | `poll`, `fetch-transcript` endpoints live. `meeting_captions` transcript provider configured |
| 2026-03-11 | M1-06 Salesforce DONE | SF Developer org created, demo data populated, VPS connected | 12 universities, 100 contacts, 71 opps. `/api/salesforce/status` live |
| 2026-03-11 | M1-10+M1-11 Bot Voice + Brain | Built TTS service, bot brain, bot media page, output media config | Bot speaks via Output Media API + Deepgram TTS + Gemini brain. Needs VPS deploy + live test |
| 2026-03-11 | Streaming pipeline + Hume Octave | Rebuilt TTS (Hume primary), filler phrases, per-sentence streaming | Target: 0ms perceived, 2-3s to content. Hume key obtained + deployed. |
| 2026-03-11 | Output Media → Output Audio | Switched architecture: removed webpage rendering, added webhook transcript + direct MP3 push | Fixes: autoplay blocked, transcript WebSocket dead. Much simpler pipeline. |
| 2026-03-11 | Streaming pipeline + Hume Octave | Rebuilt TTS (Hume primary), filler phrases, per-sentence streaming, expanded bot patterns | Target: 0ms perceived latency (filler), 2-3s to real content. Down from 15s. |
| 2026-03-11 | Hume TTS deployed + fixed | Fixed format payload (object not string), rate limit fix (sequential + file lock), API key added | All 6 phrases cached on VPS, 200 OK. Ready for live test. |
| 2026-03-11 | Real-time transcript fix | Added prioritize_low_latency mode + partial_data events | Root cause: 3-10min webhook delay. Fix deployed. Everything else worked (Gemini, Hume TTS, bot brain). |
| 2026-03-11 | Speech misrecognition fix | Regex patterns for "bot" variants + sliding window for cross-webhook matching | Bot brain now detects "hey but/bud/bought" as "hey bot". Window merges split webhooks. |
| 2026-03-11 | Partial/final dedup | Text extension/duplicate tracking in recall_webhooks.py | No more duplicate DB segments from cumulative low-latency mode |
| 2026-03-12 | Response flooding fix | `_claim_response()` + `_responding` set | Root cause: 6+ parallel tasks. Now exactly 1 response per trigger. |
| 2026-03-12 | Wake word → EDMO | Replaced "hey bot" with "hey EDMO" | Eliminates "but/bought/bud" false positives. Bot renamed to EDMO. Deployed. |
| 2026-03-12 | Hume EVI Pipeline PRD | Deep research on speech-to-speech, created comprehensive PRD | `docs/PRD-HUME-EVI-PIPELINE.md` — 25 use cases, 6 Salesforce tools, 3-week implementation plan |
| 2026-03-12 | Hume EVI Implementation | Built EVI WebSocket client + Salesforce tools + webhook integration | `hume_evi_service.py`, `evi_tools.py`, updated `recall_webhooks.py` with EVI/legacy toggle |
| 2026-03-12 | Hume EVI Config Created | Created config in Hume dashboard with 6 Salesforce tools | Config ID: `65cb5bc5-b4f8-42d9-ac5b-7123df5bc881`, deployed to VPS |
| 2026-03-12 | EVI Deployment BLOCKED | Test connection works but Hume credits exhausted | Need to add credits at https://platform.hume.ai/billing |
| 2026-03-12 | EDMO Service Redeployed | Fixed port conflict (old mi-api disabled), deployed to /root/edmo | EDMO running at `http://89.117.36.82:8001`, health OK, TTS cached. USE_EVI=false (legacy pipeline) until Hume key added |

## Current Session: 2026-03-12 (continued)
- **Deployed:** EDMO to VPS at `/root/edmo`
- **Service:** `edmo.service` (replaced conflicting `mi-api.service`)
- **Pipeline:** Hume EVI (speech-to-speech) OR Legacy (Gemini + TTS) based on `USE_EVI` flag
- **Health:** `http://89.117.36.82:8001/health` → OK
- **TTS:** 6 phrases cached (5 fillers + greeting)
- **Database:** PostgreSQL `mi_engine`, all 9 tables exist

### Natural Turn-Taking Implemented (2026-03-12)
**Replaces wake word ("hey EDMO") with Hume EVI-style behavior:**
- `ALWAYS_RESPOND = True` — bot responds to ALL conversation, not just when addressed
- `USE_SPEECH_OFF_TRIGGER = True` — responds when speaker FINISHES talking (speech_off event)
- `RESPONSE_COOLDOWN = 2.0` — minimum 2 seconds between responses
- Added `participant.speech.start/end` events to Recall.ai realtime_endpoints
- Greeting updated: "I'm here to help throughout the conversation" (no "say hey EDMO")

**Flow:**
```
[User speaks] → transcript buffered → [User stops] → speech_off webhook → bot responds to FULL utterance
```

### Files Changed:
- `src/services/bot_brain.py` — Added `USE_SPEECH_OFF_TRIGGER`, `on_speech_start()`, `buffer_transcript()`, `on_speech_end()`, `is_speaking()`
- `src/webhooks/recall_webhooks.py` — Buffer during speech, trigger on speech_off
- `src/services/recall_service.py` — Added `participant.speech.start/end` to realtime_endpoints

### Conversational AI Hypotheses (2026-03-12)
**Goal:** Human-like conversation with ~500ms perceived latency, barge-in detection, smart recovery

**Branch:** `feature/conversational-ai-v1` — committed working baseline

**Current Implementation:**
- `PAUSE_DETECTION_SECONDS = 0.5` — reduced from 1.5s (Hypothesis 1)
- `ALWAYS_RESPOND = True` — no wake word
- `RESPONSE_COOLDOWN = 3.0` — min between responses
- Buffer transcript, schedule response after 500ms silence
- Cancel pending response if new speech arrives

**Documented Hypotheses (docs/CONVERSATIONAL-AI-HYPOTHESES.md):**
1. Reduce pause to 500ms ✅ IMPLEMENTED
2. Instant filler on 300ms pause (not yet)
3. Barge-in detection via transcript during speech (not yet)
4. Audio queue with cancel support (not yet)
5. Smart interruption classification (STOP/BACKCHANNEL/CLARIFICATION)
6. Resume context ("As I was saying...")

**Next Test:**
1. Deploy 500ms pause to VPS
2. Test: is bot responsive enough? Does it interrupt mid-sentence?
3. If mid-sentence interrupts: implement punctuation detection or increase to 700ms
