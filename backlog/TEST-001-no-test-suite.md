# TEST-001: No Test Suite

**Priority**: P1 - High
**Status**: OPEN
**Category**: Testing

## Description

No automated tests exist for the codebase. This makes refactoring risky and regressions likely.

## Current State

- No `tests/` directory
- No pytest, unittest, or other test framework configured
- No CI/CD pipeline for automated testing

## Risk

- Regressions go undetected
- Refactoring is risky
- New contributors can't verify their changes
- Security fixes might break functionality

## Suggested Implementation

### 1. Setup pytest

```bash
pip install pytest pytest-cov pytest-mock
```

Create `pytest.ini`:
```ini
[pytest]
testpaths = tests
python_files = test_*.py
python_functions = test_*
addopts = -v --cov=backend --cov-report=html
```

### 2. Priority Test Areas

**Critical (write first):**
- `test_database.py` - CRUD operations, SQL injection prevention
- `test_admin_auth.py` - Login, rate limiting, session management
- `test_batch_routes.py` - Job creation, validation

**Important:**
- `test_gemini_service.py` - API mocking, retry logic
- `test_tiktok_scraper.py` - Response parsing
- `test_google_drive.py` - Upload/download mocking

### 3. Example Test Structure

```
tests/
├── conftest.py          # Fixtures
├── test_database.py
├── test_admin_auth.py
├── test_batch_routes.py
├── test_gemini_service.py
└── test_integration.py
```

### 4. Sample Test

```python
# tests/test_admin_auth.py
import pytest
from backend.admin_routes import _check_rate_limit, _record_login_attempt

def test_rate_limit_allows_initial_attempts():
    is_allowed, _ = _check_rate_limit('192.168.1.1')
    assert is_allowed is True

def test_rate_limit_blocks_after_max_attempts():
    ip = '192.168.1.100'
    for _ in range(5):
        _record_login_attempt(ip, success=False)

    is_allowed, seconds = _check_rate_limit(ip)
    assert is_allowed is False
    assert seconds > 0
```

## Acceptance Criteria

- [ ] pytest configured and working
- [ ] At least 50% code coverage
- [ ] Critical paths tested (auth, database, batch processing)
- [ ] Tests run in CI before merge
- [ ] README updated with test instructions
