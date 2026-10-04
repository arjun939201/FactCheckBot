# Fact Check — World-Class Product Build

## Product direction
Fact Check is designed as an evidence-first research product rather than a binary AI truth button.

**Core promise:** Check claims. Follow the evidence.

## Frontend
- Research-first workspace with claim/media and article modes.
- Progressive research preferences to keep the primary workflow focused.
- Live character count and attachment feedback.
- Claim-level verdict cards with confidence and source quality.
- Supporting vs contradicting evidence separated visually.
- Source traceability with publisher, tier, quality and corroboration.
- Explicit uncertainty and research limitations.
- Browser-session history with search/open/delete/clear.
- Dedicated assistant experience.
- Dedicated public share-result experience.
- Responsive mobile layout.
- Accessible labels, semantic sections and keyboard-friendly controls.

## Backend
- FastAPI application with typed Pydantic contracts.
- Evidence-first live search and grounded AI analysis.
- Claim decomposition and evidence ID mapping.
- Media extraction pipeline for image/audio/video.
- Capability-aware multimodal model discovery.
- Browser-scoped history isolation.
- PostgreSQL or SQLite persistence.
- Request IDs and response timing headers.
- Security headers and optional trusted-host enforcement.
- GZip compression.
- Lightweight rate limiting and request-size guardrails.
- Health, readiness and capability endpoints.
- Production Render/Docker support.

## Remaining platform-level work
This repository is production-oriented, but a genuinely world-scale service would still need managed rate limiting/queues, distributed caching, durable object storage, authentication/accounts, observability/metrics/tracing, source connector credentials, browser automation where permitted, asynchronous research jobs, abuse prevention, and a larger integration/test matrix.

### Provider resilience
Multimodal provider failures are classified separately from application failures. HTTP 404 means a model is unavailable and may trigger capability discovery; HTTP 429 means the provider is throttling and never triggers model switching. Retries are bounded, `Retry-After` is propagated, and each investigation has a configurable multimodal-call budget.
