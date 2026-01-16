# SEC-004: Missing CSRF Protection

**Priority**: P1 - High
**Status**: OPEN
**Category**: Security

## Description

The Flask app uses permissive CORS (`CORS(app)`) without CSRF tokens on state-changing endpoints (POST, PUT, DELETE).

## Current Behavior

Any origin can make requests to the API, and there's no CSRF token validation.

## Risk

If the app is accessed from a browser, malicious sites could trigger actions on behalf of authenticated users.

## Files Affected

- `backend/app.py` (CORS configuration)
- All POST/PUT/DELETE endpoints

## Suggested Fix

Option 1: Add CSRF tokens using Flask-WTF
```python
from flask_wtf.csrf import CSRFProtect
csrf = CSRFProtect(app)
```

Option 2: Restrict CORS to specific origins
```python
CORS(app, origins=['http://31.97.123.84', 'http://localhost:5001'])
```

Option 3: Use token-based auth (Bearer tokens) instead of cookies

## Acceptance Criteria

- [ ] State-changing endpoints protected against CSRF
- [ ] CORS restricted to known origins OR CSRF tokens implemented
