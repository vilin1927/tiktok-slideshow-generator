# Issues Found - 2026-01-17

## Critical (Must Fix)
*None identified - core functionality works*

## High Priority

| # | Issue | Location | Description |
|---|-------|----------|-------------|
| 1 | Missing `/api/batches` endpoint | batch_routes.py | Test expects `/api/batches` but endpoint may be different path |
| 2 | Database batch function signature | database.py | `create_batch()` has different parameter signature than expected |

## Medium Priority

| # | Issue | Location | Description |
|---|-------|----------|-------------|
| 3 | Admin login returns 401 for missing password | admin_routes.py | Returns 401 instead of 400 when password field is missing (acceptable behavior) |
| 4 | Validate links response format | batch_routes.py | Returns `valid_count` instead of `valid` key |
| 5 | render_text signature | text_renderer.py | Function doesn't accept `safe_zone` as dict with x,y,width,height |
| 6 | get_gemini_option missing 'name' | presets.py | Gemini option dict missing 'name' field |
| 7 | Video jobs clear endpoint | video_routes.py | DELETE /api/video/jobs returns unexpected status |

## Low Priority / Test Fixes Needed

| # | Issue | Location | Description |
|---|-------|----------|-------------|
| 8 | Preset field assertion | test_api_presets.py | Test checks for 'name' but actual response may use different field |
| 9 | Batch link function signature | database.py | `create_batch_link()` parameter order different |
| 10 | list_all_presets return type | presets.py | Returns objects, test expects `.id` attribute |
| 11 | split_text_and_emojis return format | text_renderer.py | Returns different segment format than expected |

## Analysis

### Test Issues (Tests Need Fixing)
Most failures are due to **test expectations not matching actual API responses**, not bugs in the code:

1. **API response field names** - Tests expect `valid`/`invalid` but API returns `valid_count`/`invalid_count`
2. **Endpoint paths** - `/api/batches` may not exist, could be `/api/batch` or different
3. **Function signatures** - Database functions have different parameter signatures

### Code Issues (Code May Need Fixing)
1. **Consistency** - Some API responses use different field naming conventions
2. **Documentation** - Function signatures should be documented

## Recommended Fix Order

1. **Test Fixes First** - Update test assertions to match actual API responses:
   - Fix `/api/batches` → actual endpoint path
   - Fix `valid` → `valid_count` field name
   - Fix `create_batch()` function signature in tests
   - Fix `render_text()` function signature in tests

2. **Optional Code Improvements**:
   - Add `name` field to gemini option for consistency
   - Consider returning 400 for missing password field (currently 401)

## Coverage Areas

### Well Tested (No Issues)
- Health check endpoint
- Preset listing and retrieval
- Safe zone detection (all analyzers)
- Video generation with FFmpeg
- Font loading and sizing
- Admin authentication flow
- Job creation and retrieval

### Needs More Testing
- Full batch processing flow (end-to-end)
- Gemini pipeline (mocked in tests)
- TikTok scraper (external API)
- Google Drive upload (external API)
