# CODE-001: Broad Exception Handling

**Priority**: P2 - Medium
**Status**: OPEN
**Category**: Code Quality

## Description

Generic exception catching may mask specific errors and make debugging difficult.

## Current Behavior

```python
# gemini_service_v2.py:1201-1220
except Exception as e:
    last_error = e
```

Similar patterns exist throughout the codebase.

## Risk

- Specific errors are hidden
- Debugging becomes difficult
- May catch exceptions that shouldn't be caught (KeyboardInterrupt, SystemExit)

## Files Affected

- `backend/gemini_service_v2.py`
- `backend/tasks.py`
- `backend/tiktok_scraper.py`

## Suggested Fix

1. Catch specific exceptions:
```python
from requests.exceptions import RequestException, Timeout
from google.api_core.exceptions import GoogleAPIError

try:
    result = call_gemini_api()
except Timeout as e:
    logger.warning(f"API timeout: {e}")
    # Retry logic
except GoogleAPIError as e:
    logger.error(f"Gemini API error: {e}")
    raise
except Exception as e:
    logger.exception(f"Unexpected error: {e}")
    raise
```

2. Always log full exception with traceback:
```python
import traceback
logger.error(f"Error: {e}\n{traceback.format_exc()}")
```

## Acceptance Criteria

- [ ] Identify and catch specific exception types
- [ ] Log full tracebacks for debugging
- [ ] Don't catch KeyboardInterrupt/SystemExit
