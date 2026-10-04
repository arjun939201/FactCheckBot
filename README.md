# Fact Check

**Check claims. Follow the evidence.**

A FastAPI fact-checking application using **Groq** for structured claim analysis and **DuckDuckGo search via the DDGS library** for no-key web evidence retrieval.

## Stack
- FastAPI + Pydantic
- Groq API (OpenAI-compatible chat completions)
- DDGS/DuckDuckGo web search — no search API key required
- SQLite for local development
- PostgreSQL for durable Render production history
- Vanilla HTML/CSS/JavaScript frontend
- Render deployment

## Environment
Set:
- `GROQ_API_KEY`
- `GROQ_MODEL` (default: `llama-3.3-70b-versatile`)
- `DATABASE_URL` (SQLite locally or PostgreSQL on Render)

Never expose API keys in frontend code or commit them to Git.

## Local
```bash
pip install -r requirements.txt
uvicorn app.main:app --reload
```

## Render
Production branch: `main`

Build command:
```bash
pip install -r requirements.txt
```

Start command:
```bash
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

Health check:
```
/api/health
```

Required Render variables:
- `APP_ENV=production`
- `GROQ_API_KEY`
- `GROQ_MODEL=llama-3.3-70b-versatile`
- `DATABASE_URL` pointing to the Render PostgreSQL database

## Accuracy and safety
The model receives only evidence returned by the application's search layer for evidence-based conclusions. Source URLs are restricted to URLs actually returned by that layer. When search is unavailable or evidence is insufficient, the application reports uncertainty/UNVERIFIED rather than presenting unsupported verification as fact. URL fetching respects robots.txt, uses SSRF protections, and does not bypass paywalls or anti-bot controls.

## API
- `POST /api/fact-check`
- `POST /api/fact-check/url`
- `POST /api/chat`
- `GET /api/history`
- `GET /api/history/{id}`
- `DELETE /api/history/{id}`
- `DELETE /api/history`
- `GET /api/health`
