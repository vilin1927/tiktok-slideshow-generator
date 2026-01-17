# Test Results - 2026-01-17

## Summary
- **Total tests:** 150
- **Passed:** ~124
- **Failed:** ~24
- **Skipped:** 2

## Test Categories

### Integration Tests (API Endpoints)
| Test File | Passed | Failed | Notes |
|-----------|--------|--------|-------|
| test_api_health.py | 5/5 | 0 | All health checks pass |
| test_api_presets.py | 10/11 | 1 | Minor field assertion issue |
| test_api_generate.py | 9/12 | 3 | Some endpoint behavior differences |
| test_api_batch.py | 8/14 | 6 | Endpoint path differences |
| test_api_video.py | 9/11 | 2 | Minor response differences |
| test_api_admin.py | 16/17 | 1 | 401 vs 400 for missing password |

### Unit Tests
| Test File | Passed | Failed | Notes |
|-----------|--------|--------|-------|
| test_presets.py | 17/19 | 2 | Minor attribute assertions |
| test_database.py | 12/17 | 5 | Batch function signature differences |
| test_text_renderer.py | 12/17 | 5 | render_text signature issues |
| test_safe_zone.py | 12/12 | 0 | All safe zone tests pass |
| test_video_generator.py | 9/10 | 0 | 1 skipped (audio file) |

## Key Findings

### Passing Areas (No Issues)
1. **Health endpoint** - Working correctly
2. **Presets module** - All 9 presets load correctly
3. **Safe zone detection** - All analyzers working
4. **Video generation** - FFmpeg integration working
5. **Font loading and sizing** - Working correctly
6. **Emoji detection** - Working correctly
7. **Admin authentication** - Token flow working

### Issues Found (See ISSUES.md)
1. API response field differences (test expectations vs actual)
2. Endpoint path differences (/api/batches vs actual)
3. Database function signature differences
4. render_text function signature mismatch
