# PERF-002: Thread Safety in Progress Tracking

**Priority**: P2 - Medium
**Status**: OPEN
**Category**: Performance / Reliability

## Description

Global mutable state for progress tracking is not thread-safe.

## Current Behavior

```python
# app.py:37
progress_status = {}  # Global mutable state
```

Concurrent requests could race when updating this dictionary.

## Risk

- Race conditions causing incorrect progress display
- Potential data corruption under high load
- Memory leaks if entries aren't cleaned up

## Files Affected

- `backend/app.py:37`

## Suggested Fix

Option 1: Use threading.Lock
```python
import threading

progress_status = {}
progress_lock = threading.Lock()

def update_progress(session_id, data):
    with progress_lock:
        progress_status[session_id] = data
```

Option 2: Use Redis for shared state (recommended for multi-process)
```python
import redis
r = redis.Redis()

def update_progress(session_id, data):
    r.hset(f"progress:{session_id}", mapping=data)
    r.expire(f"progress:{session_id}", 3600)  # Auto-cleanup
```

Option 3: Use Flask-Caching
```python
from flask_caching import Cache
cache = Cache(app, config={'CACHE_TYPE': 'redis'})
```

## Acceptance Criteria

- [ ] Progress tracking is thread-safe
- [ ] Old progress entries are cleaned up
- [ ] Works correctly with multiple Celery workers
