# CODE-004: Input Type Validation

**Priority**: P3 - Low
**Status**: OPEN
**Category**: Code Quality

## Description

Variation parameters are clamped but not type-validated before conversion, which could expose stack traces.

## Current Behavior

```python
# batch_routes.py:125-136
hook_photo_var = int(request.form.get('hook_photo_var', ...))
# Clamp to valid range (1-5)
hook_photo_var = max(1, min(5, hook_photo_var))
```

If a non-numeric value is passed, `int()` raises `ValueError`, caught by generic exception handler.

## Risk

- Stack traces may leak in error responses
- Poor user experience with cryptic errors

## Files Affected

- `backend/batch_routes.py:125-136`
- Other endpoints accepting numeric parameters

## Suggested Fix

Add helper function for safe integer parsing:

```python
def safe_int(value, default: int, min_val: int = None, max_val: int = None) -> int:
    """Safely parse integer with bounds checking."""
    try:
        result = int(value) if value is not None else default
    except (ValueError, TypeError):
        result = default

    if min_val is not None:
        result = max(min_val, result)
    if max_val is not None:
        result = min(max_val, result)

    return result

# Usage:
hook_photo_var = safe_int(request.form.get('hook_photo_var'), default=1, min_val=1, max_val=5)
```

## Acceptance Criteria

- [ ] All numeric inputs validated before conversion
- [ ] Clear error messages for invalid input
- [ ] No stack traces in error responses
