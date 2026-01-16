# PERF-001: Uncapped Job Queue

**Priority**: P1 - High
**Status**: OPEN
**Category**: Performance

## Description

No global cap on total pending tasks. A malicious user or runaway process could queue unlimited batch jobs, exhausting system resources.

## Current Behavior

```python
# gemini_service_v2.py
MAX_CONCURRENT = 10   # Up to 10 concurrent requests
RPM_LIMIT = 25        # 25 requests per minute
```

Rate limiting exists for Gemini API calls, but no limit on total queued jobs.

## Risk

- Memory exhaustion from too many pending jobs
- Disk space exhaustion from temp files
- API quota exhaustion
- DoS vulnerability

## Files Affected

- `backend/batch_routes.py`
- `backend/tasks.py`
- `backend/database.py`

## Suggested Fix

1. Add job queue limits:

```python
MAX_PENDING_JOBS = 100  # Global limit
MAX_JOBS_PER_HOUR = 50  # Per-IP limit

def can_create_job(client_ip: str) -> tuple[bool, str]:
    # Check global pending count
    pending = get_jobs_count(status='pending')
    if pending >= MAX_PENDING_JOBS:
        return False, "Job queue full. Please try again later."

    # Check per-IP rate
    recent = get_jobs_by_ip(client_ip, hours=1)
    if len(recent) >= MAX_JOBS_PER_HOUR:
        return False, "Rate limit exceeded. Max 50 jobs per hour."

    return True, ""
```

2. Add IP tracking to jobs table
3. Implement cleanup of old pending jobs

## Acceptance Criteria

- [ ] Global job queue limit enforced
- [ ] Per-IP rate limiting implemented
- [ ] Proper error messages returned when limits hit
- [ ] Old stale jobs cleaned up automatically
