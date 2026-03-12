# Conversational AI Hypotheses — Natural Turn-Taking & Barge-In

## Goal
Make EDMO bot feel like a natural human conversation participant:
- **500ms perceived latency** (instant filler, then real response)
- **No overlapping speech** (bot doesn't talk over itself)
- **Barge-in detection** (bot stops when user interrupts)
- **Smart recovery** (bot asks "Did you say something?" and can resume)

---

## Current State (2026-03-12)

| Component | Status | Latency |
|-----------|--------|---------|
| Transcript webhook delivery | Working | ~300ms |
| Pause detection | 1.5s timer | 1500ms |
| Filler phrase | Pre-cached | ~50ms |
| Gemini response | Streaming | 800-1500ms |
| Deepgram TTS | Per-sentence | 200-400ms |
| Recall.ai audio push | Working | ~100ms |
| **Total perceived** | | **~2-3 seconds** |

**Problems:**
1. 1.5s pause detection adds unnecessary delay
2. No barge-in — bot keeps talking even if user interrupts
3. Sentences can overlap if TTS/push takes longer than expected
4. No state tracking (bot doesn't know if it's speaking)

---

## Hypothesis 1: Reduce Pause Detection to 500ms

**Theory:** 500ms pause is enough to detect end of sentence in natural speech.

**Test:**
```python
PAUSE_DETECTION_SECONDS = 0.5  # Was 1.5
```

**Risk:** May trigger mid-sentence on natural pauses ("I want to... um... ask you")

**Mitigation:** Combine with punctuation detection — only trigger if text ends with `.?!` OR 500ms pause.

---

## Hypothesis 2: Instant Filler on First Pause

**Theory:** Play filler immediately on first 300ms pause, then wait for full sentence.

**Flow:**
```
[User speaks] → [300ms pause] → PLAY FILLER ("Got it...")
[User continues] → [more speech] → buffer continues
[User stops] → [500ms pause] → PLAY REAL RESPONSE
```

**Implementation:**
- Two-stage timer: 300ms for filler, 500ms for response
- If user resumes after filler, just wait (filler already played)

---

## Hypothesis 3: Barge-In Detection via Transcript During Speech

**Theory:** If new transcript arrives while bot is speaking, user is interrupting.

**State Machine:**
```
IDLE → [user speaks] → LISTENING
LISTENING → [pause detected] → BOT_SPEAKING
BOT_SPEAKING → [new transcript] → INTERRUPTED
INTERRUPTED → [check context] → RECOVERY
RECOVERY → [user says "continue"] → BOT_SPEAKING (resume)
RECOVERY → [user says something else] → LISTENING (new topic)
```

**Implementation:**
```python
_bot_state: dict[str, str] = {}  # meeting_id -> "idle" | "listening" | "speaking" | "interrupted"
_interrupted_context: dict[str, str] = {}  # meeting_id -> what bot was saying when interrupted

async def on_transcript_during_bot_speaking(meeting_id, text):
    if _bot_state.get(meeting_id) == "speaking":
        # User interrupted!
        cancel_pending_audio(meeting_id)
        _bot_state[meeting_id] = "interrupted"
        # Save what we were saying
        _interrupted_context[meeting_id] = get_pending_response(meeting_id)
        # Ask if they need something
        await push_audio("Did you want to say something?")
```

---

## Hypothesis 4: Audio Queue with Cancel Support

**Theory:** Queue sentences, but allow cancellation on barge-in.

**Current problem:** We push audio to Recall.ai immediately. Once pushed, we can't stop it.

**Solution:** Track what's been pushed vs what's pending:
```python
_audio_queue: dict[str, list[bytes]] = {}  # meeting_id -> [audio chunks pending]
_audio_playing: dict[str, bool] = {}  # meeting_id -> currently playing?

async def push_audio_with_cancel_support(meeting_id, audio_bytes):
    _audio_queue[meeting_id].append(audio_bytes)
    if not _audio_playing.get(meeting_id):
        await _drain_audio_queue(meeting_id)

async def cancel_pending_audio(meeting_id):
    _audio_queue[meeting_id].clear()
    # Note: Can't stop audio already pushed to Recall.ai
    # But we can stop pushing MORE audio
```

---

## Hypothesis 5: Smart Interruption Classification

**Theory:** Not all interruptions are "stop talking". Classify them:

| Pattern | Classification | Bot Response |
|---------|---------------|--------------|
| "stop", "wait", "hold on" | STOP | Silence, wait for user |
| "no no no", "actually" | CORRECTION | "Oh, go ahead" |
| "yes", "uh huh", "right" | BACKCHANNEL | Continue speaking |
| "what?", "sorry?" | CLARIFICATION | Repeat last sentence |
| Other speech | TAKEOVER | "Did you want to say something?" |

**Implementation:**
```python
STOP_PATTERNS = ["stop", "wait", "hold on", "pause", "one second"]
BACKCHANNEL_PATTERNS = ["yes", "yeah", "uh huh", "right", "okay", "got it", "mm hmm"]
CLARIFICATION_PATTERNS = ["what", "sorry", "repeat", "say that again", "huh"]

def classify_interruption(text: str) -> str:
    text_lower = text.lower()
    if any(p in text_lower for p in STOP_PATTERNS):
        return "STOP"
    if any(p in text_lower for p in BACKCHANNEL_PATTERNS):
        return "BACKCHANNEL"  # Ignore, keep talking
    if any(p in text_lower for p in CLARIFICATION_PATTERNS):
        return "CLARIFICATION"
    return "TAKEOVER"
```

---

## Hypothesis 6: Resume Context After Interruption

**Theory:** When user says "continue" or "go on", bot should resume from where it stopped.

**Implementation:**
```python
_interrupted_context: dict[str, dict] = {}
# {
#   "meeting_id": {
#     "pending_sentences": ["sentence 2", "sentence 3"],
#     "last_topic": "enrollment deadlines",
#     "interrupted_at": timestamp
#   }
# }

CONTINUE_PATTERNS = ["continue", "go on", "go ahead", "keep going", "you were saying", "never mind"]

async def handle_after_interruption(meeting_id, text):
    if any(p in text.lower() for p in CONTINUE_PATTERNS):
        ctx = _interrupted_context.get(meeting_id)
        if ctx and ctx["pending_sentences"]:
            await push_audio("Sure, as I was saying...")
            for sentence in ctx["pending_sentences"]:
                await push_sentence_audio(meeting_id, sentence)
            _interrupted_context.pop(meeting_id)
            return True
    return False
```

---

## Test Plan

### Test 1: Latency Measurement
1. Record timestamp when user stops speaking (last transcript)
2. Record timestamp when bot audio starts playing
3. Target: <500ms for filler, <2s for first real sentence

### Test 2: Barge-In Detection
1. Bot is speaking a long response (3+ sentences)
2. User says "stop" mid-sentence
3. Verify: Bot stops pushing audio within 500ms
4. Verify: Bot says "Did you want to say something?"

### Test 3: Backchannel Handling
1. Bot is speaking
2. User says "uh huh" or "yes"
3. Verify: Bot continues speaking (doesn't stop)

### Test 4: Resume Flow
1. User asks question → Bot starts answering
2. User interrupts with "hold on"
3. Bot stops, asks "Yes?"
4. User says "never mind, continue"
5. Bot says "As I was saying..." and resumes

### Test 5: Overlap Prevention
1. Bot speaks 5 sentences in a row
2. Verify: No audio overlap (each sentence finishes before next starts)
3. Measure gap between sentences (<200ms ideal)

---

## Implementation Priority

| Priority | Feature | Complexity | Impact |
|----------|---------|------------|--------|
| 1 | Reduce pause detection to 500ms | Low | High |
| 2 | Barge-in detection (basic) | Medium | High |
| 3 | Audio queue with cancellation | Medium | High |
| 4 | Interruption classification | Medium | Medium |
| 5 | Resume context | High | Medium |
| 6 | Instant filler (300ms) | Low | Medium |

---

## Settings to Tune

```python
# Timing
PAUSE_DETECTION_SECONDS = 0.5      # When to trigger response (was 1.5)
FILLER_TRIGGER_SECONDS = 0.3       # When to play filler (new)
RESPONSE_COOLDOWN = 3.0            # Min time between responses

# Barge-in
BARGE_IN_ENABLED = True
BARGE_IN_MIN_WORDS = 2             # Ignore 1-word backchannels
BARGE_IN_GRACE_PERIOD = 0.5        # Ignore speech right after bot starts

# Audio
SENTENCE_GAP_MS = 100              # Gap between sentences
MAX_PENDING_SENTENCES = 5          # Don't queue too many
```

---

## Notes

- Recall.ai Output Audio API has no "stop playing" endpoint — once audio is pushed, it plays
- Workaround: Stop pushing NEW audio, let current chunk finish
- Hume EVI handles all this natively — consider upgrading to EVI pipeline for production
- Current Gemini+TTS pipeline will always have higher latency than speech-to-speech models
