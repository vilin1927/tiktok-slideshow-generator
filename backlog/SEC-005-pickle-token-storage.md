# SEC-005: Pickle Token Storage

**Priority**: P1 - High
**Status**: OPEN
**Category**: Security

## Description

OAuth tokens are stored as pickled files in `google_drive.py`. Pickle files can execute arbitrary code if tampered with.

## Current Behavior

```python
TOKEN_PATH = os.path.join(os.path.dirname(__file__), '..', 'credentials', 'oauth_token.pickle')

with open(TOKEN_PATH, 'rb') as token:
    creds = pickle.load(token)
```

## Risk

- If an attacker gains write access to the token file, they can inject malicious code
- Token file has default permissions (may be world-readable)

## Files Affected

- `backend/google_drive.py:32-51`

## Suggested Fix

Use JSON token storage instead of pickle:

```python
import json
from google.oauth2.credentials import Credentials

TOKEN_PATH = 'credentials/oauth_token.json'

def _get_credentials():
    creds = None
    if os.path.exists(TOKEN_PATH):
        creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)

    # ... refresh logic ...

    # Save as JSON
    with open(TOKEN_PATH, 'w') as token:
        token.write(creds.to_json())
```

## Acceptance Criteria

- [ ] Replace pickle with JSON token storage
- [ ] Set restrictive file permissions (600)
- [ ] Migrate existing pickle tokens to JSON format
