# CODE-002: Magic Numbers

**Priority**: P2 - Medium
**Status**: OPEN
**Category**: Code Quality

## Description

Hardcoded numeric values without explanation make code harder to understand and maintain.

## Current Behavior

```python
# gemini_service_v2.py:174
if file_size < 50000:  # Less than 50KB is suspicious

# gemini_service_v2.py
MAX_CONCURRENT = 10
RPM_LIMIT = 25

# admin_routes.py
SESSION_DURATION = 3600  # 1 hour
```

Some have comments, but should be named constants at module level.

## Files Affected

- `backend/gemini_service_v2.py`
- `backend/tasks.py`
- `backend/tiktok_scraper.py`

## Suggested Fix

Create a constants module or define at file top:

```python
# constants.py or at top of relevant file

# Image validation thresholds
MIN_VALID_IMAGE_SIZE_BYTES = 50_000  # 50KB - smaller likely corrupted
MAX_IMAGE_SIZE_BYTES = 10_000_000    # 10MB

# API rate limits
GEMINI_MAX_CONCURRENT_REQUESTS = 10
GEMINI_REQUESTS_PER_MINUTE = 25

# Session settings
ADMIN_SESSION_DURATION_SECONDS = 3600  # 1 hour
```

## Acceptance Criteria

- [ ] All magic numbers extracted to named constants
- [ ] Constants have descriptive names
- [ ] Comments explain the reasoning where not obvious
