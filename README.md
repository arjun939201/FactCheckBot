# Fact Check

**Check claims. Follow the evidence.**

A production-oriented, evidence-first fact-checking application built with FastAPI, live web research, Groq models, PostgreSQL/SQLite, and a lightweight web UI.

## What it does

**Core flow:**

`Input → claim decomposition → live web research → evidence normalization → AI assessment → grounded report → history → share`

Supported inputs:

- Text claims, social posts, paragraphs, and questions
- Article URLs
- Images with visual/text extraction
- Audio with transcription
- Video with sampled frames + audio transcription
- Follow-up AI chat

Supported analysis modes:

- Auto-detect
- Factual claim
- Argument
- Opinion
- Propaganda / persuasion
- Prediction
- Question
- Satire / unclear
- Mixed content

Political or controversial content is not automatically treated as propaganda or opinion. Verifiable assertions are separated from subjective judgments and rhetorical techniques.

## Evidence-first design

The model is not allowed to treat its private knowledge as retrieved evidence. Research results are normalized before being sent to the analysis model, and final evidence mappings are restricted to URLs actually retrieved by the search layer.

The system can return `UNVERIFIED` when the available evidence is insufficient. Confidence is an assessment signal, not a claim of mathematical certainty.

Search currently uses a provider abstraction around DDGS and Google News RSS. The architecture is deliberately designed so additional official, academic, news, social, and specialist providers can be added without rewriting the fact-checking pipeline.

## Media

Up to five attachments per request:

| Type | Limit | Processing |
|---|---:|---|
| Images | 10 MB each | visual analysis + visible text |
| Audio | 25 MB each | transcription |
| Video | 25 MB each | sampled frames + audio transcription |

Media extraction is **not verification**. Extracted text, transcript, and visual context are merely additional material for the evidence-research stage.

Uploaded binaries are processed in memory and are not retained as permanent files. History stores analysis metadata.

## Production hardening in this snapshot

- Current Groq model configured as `openai/gpt-oss-120b` instead of the retired `llama-3.3-70b-versatile` default seen in production logs.
- Optional Groq model discovery when configured text models return 404.
- Robust normalization of malformed model evidence lists, including URL strings.
- Evidence quality/type normalization before Pydantic validation.
- Request IDs and response timing headers.
- Security headers (`nosniff`, frame protection, referrer policy, permissions policy).
- Configurable CORS and trusted hosts.
- GZip compression.
- SSRF protection and redirect validation for article fetching.
- robots.txt compliance for article retrieval.
- Maximum redirect count and article-size guard.
- Bounded history queries.
- Anonymous browser-scoped history using an HttpOnly, SameSite session cookie.
- Explicitly generated, high-entropy share tokens; numeric history IDs never grant public access.
- PostgreSQL startup failures are surfaced instead of silently writing history to ephemeral SQLite.
- PostgreSQL/SQLite indexes for history.
- Lazy DDGS import so health/UI tests do not fail during dependency discovery.
- Regression coverage for the production `str.get()` crash.
- CI workflow for Python 3.13.5.

## Environment

Copy `.env.example` to `.env` for local use.

Required:

- `GROQ_API_KEY`
- `DATABASE_URL`

Important optional model settings:

- `GROQ_MODEL` — primary text model
- `GROQ_FALLBACK_MODEL` — optional second text model
- `GROQ_VISION_MODEL` — optional vision model
- `GROQ_VISION_FALLBACK_MODEL` — optional vision fallback
- `GROQ_TRANSCRIPTION_MODEL` — audio model

Other controls:

- `REQUEST_TIMEOUT`
- `SEARCH_TIMEOUT`
- `MAX_ARTICLE_CHARS`
- `MAX_SEARCH_RESULTS`
- `MAX_HISTORY_ITEMS`
- `CORS_ORIGINS`
- `ALLOWED_HOSTS`

**Never commit API keys.**

## Local development

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate

pip install -r requirements.txt
uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000`.

Run tests:

```bash
pytest -q
```

## Render

The repository includes `render.yaml`.

Build command:

```bash
pip install -r requirements.txt
```

Start command:

```bash
uvicorn app.main:app --host 0.0.0.0 --port $PORT --proxy-headers
```

Health endpoint:

`GET /api/health`

For production, set `CORS_ORIGINS` and `ALLOWED_HOSTS` to the actual allowed origins/hosts rather than leaving `*` when a dedicated frontend/domain is available.

## API

- `POST /api/fact-check`
- `POST /api/fact-check/media`
- `POST /api/fact-check/url`
- `POST /api/chat`
- `GET /api/history`
- `GET /api/history/{id}`
- `DELETE /api/history/{id}`
- `DELETE /api/history`
- `POST /api/history/{id}/share` — create/reuse an owner-authorized share link
- `GET /api/share/{token}`
- `GET /api/health`

`/api/history*` is browser-session scoped. A result becomes public only after its owner explicitly creates a share link; anyone holding that opaque token can open it at `/share/{token}`. Numeric history IDs are not public access tokens.

## Accuracy boundaries

This is an evidence-research system, not an oracle.

- Search coverage is limited by the providers and public pages available at runtime.
- Social platforms, paywalled pages, private pages, blocked pages, and anti-bot protected pages may be unavailable.
- Repeated reporting of the same underlying wire story or press release is not equivalent to independent corroboration.
- Media extraction can be wrong; it must be treated as a research lead.
- A high confidence score does not mean certainty.
- The application should prefer `UNVERIFIED` over fabricated certainty.

## Current known external dependency

Groq model availability is account/region dependent and can change. The application now attempts configured models and can discover currently exposed text models after a 404. Vision models remain dependent on a multimodal model being available to the Groq account.

## Verification status

The earlier baseline passed Python compilation and 12 automated tests before the latest audit changes. New regression tests now cover share-token authorization, same-stage vision rate-limit recovery, malformed request-size headers, and media-only submissions. The latest changes still need a completed CI run and a live Render smoke test; neither should be inferred from a successful GitHub write.
