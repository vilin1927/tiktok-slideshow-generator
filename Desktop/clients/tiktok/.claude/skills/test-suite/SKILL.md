# Test Suite Skill

Run comprehensive tests for the TikTok Slideshow Generator.

## Trigger
Use when user says: `/test-suite`, "run tests", "test everything", "run all tests"

## Workflow

### Phase 1: Setup (if needed)
1. Check if pytest is installed: `pip show pytest`
2. If not, install: `pip install pytest pytest-cov responses freezegun`
3. Check if `backend/tests/` directory exists
4. If not, create test structure

### Phase 2: Run Backend Tests
1. Navigate to backend directory: `cd backend`
2. Activate venv: `source ../venv/bin/activate`
3. Run pytest: `pytest tests/ -v --cov=. --cov-report=term`
4. Capture output and count pass/fail

### Phase 3: Frontend Tests (Optional)
1. Open browser to frontend URL (local or VPS)
2. Open DevTools (F12)
3. Execute test scenarios from the checklist
4. Log any console errors

### Phase 4: VPS Tests (Optional)
1. SSH to VPS: `ssh root@31.97.123.84`
2. Navigate: `cd /root/tiktok-slideshow-generator/Desktop/tiktok`
3. Pull latest: `git fetch origin && git pull`
4. Run pytest: `cd backend && pytest tests/ -v`
5. Compare with local results

### Phase 5: Generate Report
1. Create/update `tests/RESULTS.md` with test summary
2. Create/update `tests/ISSUES.md` with failures
3. Categorize by severity (Critical/High/Medium/Low)

---

## Commands Reference

```bash
# Install test dependencies
pip install pytest pytest-cov responses freezegun pytest-asyncio

# Run all tests with coverage
pytest tests/ -v --cov=. --cov-report=term

# Run API tests only
pytest tests/integration/ -v

# Run unit tests only
pytest tests/unit/ -v

# Run single test file
pytest tests/integration/test_api_health.py -v

# Run with HTML coverage report
pytest tests/ -v --cov=. --cov-report=html
open htmlcov/index.html

# Run tests matching pattern
pytest tests/ -v -k "test_health"
```

---

## Test Structure

```
backend/tests/
├── __init__.py
├── conftest.py           # Shared fixtures (Flask client, test DB, mocks)
├── unit/
│   ├── __init__.py
│   ├── test_presets.py        # Preset configuration tests
│   ├── test_database.py       # Database CRUD tests
│   ├── test_text_renderer.py  # Text rendering (9 presets) tests
│   ├── test_safe_zone.py      # Safe zone detection tests
│   └── test_video_generator.py # Video generation tests
└── integration/
    ├── __init__.py
    ├── test_api_health.py     # Health endpoint
    ├── test_api_presets.py    # Preset endpoints
    ├── test_api_generate.py   # Generation endpoint
    ├── test_api_batch.py      # Batch processing endpoints
    ├── test_api_video.py      # Video creation endpoints
    └── test_api_admin.py      # Admin authentication endpoints
```

---

## Frontend Test Checklist

### Single Mode
- [ ] Form loads correctly
- [ ] Image upload (drag & click)
- [ ] Variation sliders work
- [ ] Preset dropdown works
- [ ] Form validation (URL, images)
- [ ] Progress polling works
- [ ] Success result displays

### Batch Mode
- [ ] Link validation works
- [ ] Invalid links shown
- [ ] Step navigation (1 → 2 → 3)
- [ ] Product mapping works
- [ ] Batch progress displays
- [ ] Cancel/Retry buttons work

### Video Mode
- [ ] Image upload + reorder
- [ ] Audio upload
- [ ] Job queue displays
- [ ] Job status updates

### Admin Panel
- [ ] Login works
- [ ] API keys display (masked)
- [ ] Job list + pagination
- [ ] Delete job works
- [ ] Logout works

---

## VPS Connection

```bash
# SSH to VPS
ssh root@31.97.123.84

# Project location
cd /root/tiktok-slideshow-generator/Desktop/tiktok

# Pull latest code
git fetch origin && git pull origin feature/text-multi

# Install test deps
cd backend && pip3 install pytest pytest-cov responses freezegun

# Run tests
pytest tests/ -v --cov=.
```

---

## Issue Report Template

```markdown
# Issues Found - [DATE]

## Critical (Must Fix)
| # | Issue | Location | Description |
|---|-------|----------|-------------|

## High Priority
| # | Issue | Location | Description |
|---|-------|----------|-------------|

## Medium Priority
| # | Issue | Location | Description |
|---|-------|----------|-------------|

## Low Priority
| # | Issue | Location | Description |
|---|-------|----------|-------------|
```
