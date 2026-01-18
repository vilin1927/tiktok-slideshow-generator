# Tasks: Celery Worker Optimization

**PRD:** `prd-celery-worker-optimization.md`
**Date:** January 18, 2026

---

## Relevant Files

- `/etc/systemd/system/celery-worker.service` - Systemd service config for Celery worker
- `backend/celery_app.py` - Celery application configuration
- `backend/celery_config.py` - Celery settings (may need to create)
- `backend/batch_processing.db` - SQLite database with batch jobs
- `backend/cleanup_stuck_jobs.py` - New cleanup script (to create)
- `/swapfile` - Swap file on VPS (to create)
- `/etc/fstab` - Filesystem table for persistent swap

### Notes

- All VPS changes require SSH access: `ssh root@31.97.123.84`
- Test locally where possible before deploying to VPS
- Keep backup of original systemd service file before modifying

---

## Instructions for Completing Tasks

**IMPORTANT:** As you complete each task, you must check it off in this markdown file by changing `- [ ]` to `- [x]`. This helps track progress and ensures you don't skip any steps.

Example:
- `- [ ] 1.1 Read file` → `- [x] 1.1 Read file` (after completing)

Update the file after completing each sub-task, not just after completing an entire parent task.

---

## Tasks

- [x] **0.0 Create feature branch**
  - [x] 0.1 Create and checkout new branch: `git checkout -b feature/celery-optimization`
  - [x] 0.2 Push branch to remote: `git push -u origin feature/celery-optimization`

- [x] **1.0 Kill zombie processes and clean up stuck jobs**
  - [x] 1.1 SSH into VPS: `ssh root@31.97.123.84`
  - [x] 1.2 List all celery processes: `ps aux | grep celery`
  - [x] 1.3 Kill zombie workers from Jan 16: `pkill -f "celery -A celery_app worker"` (kills all)
  - [x] 1.4 Stop celery-worker service: `systemctl stop celery-worker`
  - [x] 1.5 Verify no celery processes remain: `ps aux | grep celery | grep -v grep`
  - [x] 1.6 Delete stuck job 77cfa3cd from jobs table
  - [x] 1.7 Delete stuck job b49d06f6 from jobs table
  - [x] 1.8 Delete orphaned batch entries from batch_processing.db (deleted 4 batches, 12 links)
  - [x] 1.9 Verify clean state: `curl http://localhost/api/jobs?limit=10` (0 jobs)

- [x] **2.0 Add 2GB swap space to VPS**
  - [x] 2.1 Check current swap: `free -h` (was 0B)
  - [x] 2.2 Check available disk space: `df -h` (42GB available)
  - [x] 2.3 Create 2GB swap file: `fallocate -l 2G /swapfile`
  - [x] 2.4 Set correct permissions: `chmod 600 /swapfile`
  - [x] 2.5 Format as swap: `mkswap /swapfile`
  - [x] 2.6 Enable swap: `swapon /swapfile`
  - [x] 2.7 Verify swap active: `free -h` (now shows 2GB swap)
  - [x] 2.8 Add to fstab for persistence: `echo '/swapfile none swap sw 0 0' >> /etc/fstab`
  - [x] 2.9 Set swappiness to 10: `sysctl vm.swappiness=10`

- [x] **3.0 Reduce Celery concurrency from 5 to 1**
  - [x] 3.1 Backup current service file: `cp /etc/systemd/system/celery-worker.service /etc/systemd/system/celery-worker.service.bak`
  - [x] 3.2 Edit service file: change `--concurrency=5` to `--concurrency=1`
  - [x] 3.3 Add ExecStartPre to kill stale processes before start: `ExecStartPre=-/usr/bin/pkill -9 celery`
  - [x] 3.4 Reload systemd: `systemctl daemon-reload`
  - [x] 3.5 Start celery-worker: `systemctl start celery-worker`
  - [x] 3.6 Verify single worker: `ps aux | grep celery` (2 processes: main + 1 worker)
  - [x] 3.7 Check service status: `systemctl status celery-worker` (active, running)

- [x] **4.0 Add memory safeguards and health checks**
  - [x] 4.1 Edit celery-worker.service to add memory limits: MemoryMax=800M, MemoryHigh=600M
  - [x] 4.2 Add watchdog for unresponsive worker: WatchdogSec=300
  - [x] 4.3 Ensure Restart=always is set
  - [x] 4.4 Add RestartSec=10 for restart delay
  - [x] 4.5 Skip Celery config (systemd limits are sufficient for now)
  - [x] 4.6 Reload systemd: `systemctl daemon-reload`
  - [x] 4.7 Restart celery-worker: `systemctl restart celery-worker`
  - [x] 4.8 Verify settings applied: Memory limits visible in systemctl status

- [x] **5.0 Create orphaned job cleanup script**
  - [x] 5.1 Create `backend/cleanup_stuck_jobs.py` with:
    - Function to find jobs stuck in "processing" > 30 min
    - Option to reset to "pending" or mark as "failed"
    - Option to delete job entirely
    - Handles both main jobs table and batch_processing.db
  - [x] 5.2 Add CLI arguments: `--action [reset|fail|delete]`, `--dry-run`, `--threshold`
  - [x] 5.3 Test script locally with --dry-run
  - [x] 5.4 Deploy script to VPS
  - [x] 5.5 Run script on VPS (no stuck jobs - already cleaned in task 1.0)
  - [x] 5.6 Document usage in script header

- [x] **6.0 Test and verify optimization**
  - [x] 6.1 Check memory usage: `free -h` (444MB used, 2GB swap available)
  - [x] 6.2 Check celery worker count: `ps aux | grep celery` (2 processes - correct!)
  - [ ] 6.3 Submit test single job via UI (user to test)
  - [ ] 6.4 Monitor logs: `journalctl -u celery-worker -f`
  - [ ] 6.5 Verify no 429 rate limit errors in logs
  - [ ] 6.6 Verify job completes successfully
  - [ ] 6.7 Check memory didn't spike: `free -h`
  - [ ] 6.8 Submit test batch job (2-3 links)
  - [ ] 6.9 Verify batch completes without OOM
  - [ ] 6.10 Monitor for 24 hours - no OOM crashes
  - [x] 6.11 Commit and push all changes
  - [ ] 6.12 Merge feature branch to main (after testing)

---

## Final Systemd Service File Reference

After all changes, `/etc/systemd/system/celery-worker.service` should look like:

```ini
[Unit]
Description=Celery Worker for TikTok Slideshow Generator
After=network.target redis.service

[Service]
Type=simple
User=root
WorkingDirectory=/root/tiktok-slideshow-generator/Desktop/clients/tiktok/backend
ExecStartPre=/usr/bin/pkill -f "celery.*worker" || true
ExecStart=/usr/bin/celery -A celery_app worker --loglevel=info --concurrency=1 -Q celery,batch,links,variations
Restart=always
RestartSec=10
MemoryMax=800M
MemoryHigh=600M
WatchdogSec=300
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```
