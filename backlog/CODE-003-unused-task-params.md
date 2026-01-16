# CODE-003: Unused Task Parameters

**Priority**: P2 - Medium
**Status**: OPEN
**Category**: Code Quality

## Description

The `generate_variation` task uses old pipeline arguments that don't match the current `run_pipeline` signature.

## Current Behavior

```python
# tasks.py:394-456
@celery.task(name='generate_variation', ...)
def generate_variation(
    variation_id: str,
    tiktok_url: str,
    product_photo_path: str,
    product_description: str,
    hook_variations: int = 1,      # OLD parameter
    body_variations: int = 1,      # OLD parameter
    ...
):
```

But `run_pipeline` now expects:
- `hook_photo_var`
- `hook_text_var`
- `body_photo_var`
- `body_text_var`
- `product_photo_var`
- `product_text_var`

## Risk

- Batch processing may not use correct variation settings
- Confusing for developers

## Files Affected

- `backend/tasks.py:394-456`

## Suggested Fix

Update the task signature to match current pipeline:

```python
@celery.task(name='generate_variation', ...)
def generate_variation(
    variation_id: str,
    tiktok_url: str,
    product_photo_path: str,
    product_description: str,
    hook_photo_var: int = 1,
    hook_text_var: int = 1,
    body_photo_var: int = 1,
    body_text_var: int = 1,
    product_photo_var: int = 1,
    product_text_var: int = 1,
    ...
):
    # Pass correct params to run_pipeline
    result = run_pipeline(
        ...,
        hook_photo_var=hook_photo_var,
        hook_text_var=hook_text_var,
        ...
    )
```

## Acceptance Criteria

- [ ] Task signature matches run_pipeline parameters
- [ ] Batch routes pass correct parameters
- [ ] Old parameter names removed
