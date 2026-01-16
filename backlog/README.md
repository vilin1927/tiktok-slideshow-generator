# Backlog

Technical debt and improvements identified during code review.

## Priority Levels

- **P0 - Critical**: Security vulnerabilities, data loss risks
- **P1 - High**: Major bugs, performance issues
- **P2 - Medium**: Code quality, maintainability
- **P3 - Low**: Nice-to-have improvements

## Status

| ID | Priority | Status | Title |
|----|----------|--------|-------|
| SEC-001 | P0 | DONE | SQL Injection in database.py |
| SEC-002 | P0 | DONE | Weak Authentication |
| SEC-003 | P0 | DONE | Hardcoded VPS Paths |
| SEC-004 | P1 | OPEN | Missing CSRF Protection |
| SEC-005 | P1 | OPEN | Pickle Token Storage |
| SEC-006 | P2 | OPEN | Add Security Headers |
| PERF-001 | P1 | OPEN | Uncapped Job Queue |
| PERF-002 | P2 | OPEN | Thread Safety in Progress Tracking |
| CODE-001 | P2 | OPEN | Broad Exception Handling |
| CODE-002 | P2 | OPEN | Magic Numbers |
| CODE-003 | P2 | OPEN | Unused Task Parameters |
| CODE-004 | P3 | OPEN | Input Type Validation |
| TEST-001 | P1 | OPEN | No Test Suite |
