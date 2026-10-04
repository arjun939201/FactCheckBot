# Production Deployment Checklist

## Before deploy

- [ ] Set `GROQ_API_KEY` in Render; never commit it.
- [ ] Set `DATABASE_URL` to Render PostgreSQL.
- [ ] Confirm `GROQ_MODEL` is available in the Groq account.
- [ ] Configure `GROQ_FALLBACK_MODEL` only if a known-good fallback exists.
- [ ] Configure a working vision model if media images/video are required.
- [ ] Set `CORS_ORIGINS` to the real frontend origin when frontend/backend are separated.
- [ ] Set `ALLOWED_HOSTS` to the real Render/custom domains when appropriate.

## Smoke test after deploy

1. `GET /api/health` → HTTP 200.
2. Text claim → report with live evidence.
3. Second text claim → history contains only the current browser's results.
4. Open a history item → HTTP 200.
5. Delete one history item → it disappears.
6. Clear history → only current browser history is cleared.
7. Generate/open `/share/{id}` → public shared result loads.
8. Article URL → independent retrieval and evidence check.
9. Image → only if a currently available Groq multimodal model is configured.
10. Audio/video → transcription model works.

## Failure interpretation

- `404 model_not_found`: Groq model configuration/account availability problem.
- `502 /fact-check`: inspect Render logs for search, Groq, validation, or database failure.
- `502 /fact-check/media` with vision errors: multimodal Groq model is unavailable.
- Search returns no evidence: provider/network/rate-limit problem; the app should not manufacture evidence.
- Database save `503`: inspect PostgreSQL URL/connectivity and schema initialization.

## Security notes

History is browser-session scoped with an HttpOnly/SameSite cookie. Share links are deliberately public. Do not place secrets in frontend JavaScript. If this becomes a multi-user product, replace anonymous sessions with real authentication and authorization before adding accounts, teams, or private research workspaces.
