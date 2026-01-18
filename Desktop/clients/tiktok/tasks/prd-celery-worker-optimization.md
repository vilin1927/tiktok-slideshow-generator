# PRD: Celery Worker Optimization

**Date:** January 18, 2026
**Status:** Draft
**Author:** Claude

---

## 1. Introduction/Overview

The TikTok Slideshow Generator uses Celery workers to process image generation jobs. Currently, the system runs 5 concurrent workers competing for a single Gemini API quota (20 RPM), causing:
- Out of Memory (OOM) crashes
- Excessive 429 rate limit errors
- Orphaned "processing" jobs
- ~1.5GB memory usage for workers alone

This optimization reduces workers from 5 to 1, adds memory safeguards, and implements cleanup mechanisms. Since the Gemini API bottleneck is 20 RPM regardless of worker count, a single worker with the existing GlobalImageExecutor (20 threads) achieves the same throughput with 5x less memory.

---

## 2. Goals

1. **Eliminate OOM crashes** - Reduce memory from ~1.5GB to ~300MB for workers
2. **Eliminate rate limit competition** - Single worker gets full 20 RPM quota
3. **Add crash recovery** - Auto-restart on memory threshold, health checks
4. **Clean up orphaned jobs** - Script to reset/delete stuck jobs
5. **Add swap safety net** - 2GB swap as emergency buffer

---

## 3. User Stories

1. **As an operator**, I want the system to run without OOM crashes so jobs complete reliably.
2. **As an operator**, I want stuck jobs to be automatically cleaned up so the queue doesn't accumulate garbage.
3. **As an operator**, I want the worker to self-heal if it encounters memory issues.
4. **As a user**, I want my batch jobs to complete without mysterious failures.

---

## 4. Functional Requirements

### 4.1 Reduce Celery Concurrency
- [ ] **FR-1:** Change Celery worker concurrency from 5 to 1
- [ ] **FR-2:** Update systemd service file with new concurrency setting
- [ ] **FR-3:** Verify GlobalImageExecutor still handles 20 parallel API calls within single worker

### 4.2 Zombie Process Cleanup
- [ ] **FR-4:** Kill all existing zombie Celery processes from previous runs
- [ ] **FR-5:** Add ExecStartPre to systemd service to kill stale processes before starting
- [ ] **FR-6:** Document cleanup procedure for manual intervention

### 4.3 Memory Safeguards
- [ ] **FR-7:** Configure `CELERY_WORKER_MAX_MEMORY_PER_CHILD = 500000` (500MB limit)
- [ ] **FR-8:** Configure `CELERY_WORKER_MAX_TASKS_PER_CHILD = 50` (restart after 50 tasks)
- [ ] **FR-9:** Add systemd `MemoryMax=800M` hard limit
- [ ] **FR-10:** Add systemd `MemoryHigh=600M` soft limit (throttling warning)
- [ ] **FR-11:** Configure systemd `WatchdogSec=300` (restart if unresponsive 5 min)

### 4.4 Swap Space
- [ ] **FR-12:** Create 2GB swap file at `/swapfile`
- [ ] **FR-13:** Configure swap with appropriate permissions (600)
- [ ] **FR-14:** Add swap to `/etc/fstab` for persistence across reboots
- [ ] **FR-15:** Set swappiness to low value (10) to prefer RAM

### 4.5 Orphaned Job Cleanup
- [ ] **FR-16:** Create cleanup script `cleanup_stuck_jobs.py`
- [ ] **FR-17:** Script should identify jobs stuck in "processing" for >30 minutes
- [ ] **FR-18:** Script should either reset to "pending" or mark as "failed" based on flag
- [ ] **FR-19:** Delete current stuck jobs (77cfa3cd, b49d06f6, and batch table entries)
- [ ] **FR-20:** Add optional cron job for periodic cleanup

---

## 5. Non-Goals (Out of Scope)

- Upgrading VPS RAM (separate budget decision)
- Multiple API keys for parallel quotas
- Horizontal scaling to multiple servers
- Changing the GlobalImageExecutor architecture
- UI changes for job status display

---

## 6. Technical Considerations

### Current Architecture
```
VPS (3.8GB RAM)
├── Flask app (app.py) - port 80
├── Celery worker (celery_app.py) - concurrency=5 (PROBLEM)
├── Redis - message broker
└── GlobalImageExecutor - 20 threads per job
```

### Target Architecture
```
VPS (3.8GB RAM + 2GB swap)
├── Flask app (app.py) - port 80 (~100MB)
├── Celery worker (celery_app.py) - concurrency=1 (~300MB)
│   └── Memory limits: 500MB soft, 800MB hard
│   └── Health checks: watchdog, max tasks
├── Redis - message broker (~50MB)
└── GlobalImageExecutor - 20 threads (unchanged)
```

### Key Files
- `/etc/systemd/system/celery-worker.service` - systemd config
- `backend/celery_app.py` - Celery configuration
- `backend/celery_config.py` - Celery settings (if exists)
- `batch_processing.db` - SQLite database with stuck jobs

### Memory Budget
| Component | Current | Target |
|-----------|---------|--------|
| Celery workers | ~1.5GB | ~300MB |
| Flask app | ~100MB | ~100MB |
| Redis | ~50MB | ~50MB |
| System | ~500MB | ~500MB |
| **Total** | ~2.1GB | ~950MB |
| **Buffer** | ~1.7GB | ~2.8GB + 2GB swap |

---

## 7. Success Metrics

1. **Zero OOM crashes** over 7-day period
2. **Memory usage < 1GB** during peak processing
3. **No orphaned jobs** older than 1 hour in queue
4. **Same throughput** - 165 images in ~8 minutes (unchanged)
5. **429 errors reduced by 90%+** (no more worker competition)

---

## 8. Rollback Plan

If issues occur after deployment:
1. Revert systemd service to concurrency=5
2. Remove memory limits from systemd
3. Keep swap (doesn't hurt)
4. Investigate specific issue before re-attempting

---

## 9. Open Questions

1. ~~Should we add swap?~~ **Resolved: Yes, 2GB**
2. ~~What to do with stuck jobs?~~ **Resolved: Delete + add cleanup script**
3. Should cleanup script run on cron or only manually? (Recommend: manual first, cron later if needed)
