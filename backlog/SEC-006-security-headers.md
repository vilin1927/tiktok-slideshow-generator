# SEC-006: Add Security Headers

**Priority**: P2 - Medium
**Status**: OPEN
**Category**: Security

## Description

The application doesn't set security headers that protect against common web vulnerabilities.

## Current Behavior

No security headers are set on responses.

## Risk

- Clickjacking attacks (missing X-Frame-Options)
- MIME type sniffing (missing X-Content-Type-Options)
- XSS attacks (missing Content-Security-Policy)

## Files Affected

- `backend/app.py`

## Suggested Fix

Add Flask-Talisman or manually set headers:

```python
from flask import Flask

app = Flask(__name__)

@app.after_request
def add_security_headers(response):
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    return response
```

Or use Flask-Talisman:
```python
from flask_talisman import Talisman
Talisman(app, content_security_policy=None)  # Configure CSP as needed
```

## Acceptance Criteria

- [ ] X-Frame-Options header set
- [ ] X-Content-Type-Options header set
- [ ] Consider adding Content-Security-Policy
