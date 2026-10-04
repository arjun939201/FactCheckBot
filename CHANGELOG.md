# Changelog

## Production snapshot — 2026-10-04

### Fixed
- Prevented malformed Groq evidence arrays from causing `AttributeError: 'str' object has no attribute 'get'`.
- Normalized evidence quality/tier values before validation.
- Added graceful text-model discovery after Groq `404 model_not_found` responses.
- Made DDGS import lazy for cleaner startup/test behavior.

### Hardened
- Switched the default text model away from the stale `llama-3.3-70b-versatile` setting seen in production logs.
- Added request IDs, response timing, security headers, compression, configurable CORS, and trusted-host support.
- Added SSRF, redirect, page-size, and robots.txt protections to URL retrieval.
- Added history indexes and bounded history queries.
- Added anonymous browser-scoped history with HttpOnly/SameSite cookie isolation.
- Separated private history reads from public share retrieval.
- Added production Docker image definition.

### Verification
- Python compilation: passed.
- Test suite: 12 passed.
- Render/Groq/live-search production behavior remains environment-dependent and must be smoke-tested after deployment.
