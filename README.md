# Fact Check

**Check claims. Follow the evidence.**

A FastAPI fact-checking application using **Groq** for structured claim analysis and a modular web-search provider for live evidence retrieval.

## Stack
- FastAPI + Pydantic
- Groq API (OpenAI-compatible chat completions)
- Tavily-compatible search provider
- SQLite by default; PostgreSQL supported through `DATABASE_URL`
- Vanilla HTML/CSS/JavaScript frontend
- Render deployment

## Environment
Set:
`GROQ_API_KEY`, `GROQ_MODEL` (default `llama-3.3-70b-versatile`), `SEARCH_API_KEY`, `SEARCH_API_URL`, and `DATABASE_URL`.

Never expose API keys in frontend code or commit them to Git.

## Local
```bash
pip install -r requirements.txt
uvicorn app.main:app --reload
```

## Render
Build command:
```bash
pip install -r requirements.txt
```
Start command:
```bash
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```
Health check: `/api/health`

## Accuracy and safety
The model receives only evidence returned by the configured search layer for evidence-based conclusions. Source URLs are restricted to URLs actually returned by that layer. When live evidence is unavailable or insufficient, the application reports uncertainty/UNVERIFIED rather than presenting unsupported verification as fact. URL fetching respects robots.txt, uses SSRF protections, and does not bypass paywalls or anti-bot controls.

## API
- `POST /api/fact-check`
- `POST /api/fact-check/url`
- `POST /api/chat`
- `GET /api/history`
- `GET /api/history/{id}`
- `DELETE /api/history/{id}`
- `DELETE /api/history`
- `GET /api/health`
