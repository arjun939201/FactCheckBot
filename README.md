# Fact Check

**Check claims. Follow the evidence.**

A FastAPI evidence-first fact-checking application using Groq for structured analysis and a free web-search layer for live evidence.

## Stack
- FastAPI + Pydantic
- Groq chat analysis
- Groq vision for image attachments
- Groq Whisper transcription for audio and video
- DDGS + Google News RSS web research
- SQLite locally / PostgreSQL on Render
- Vanilla HTML/CSS/JavaScript
- Render deployment

## Analysis modes
- Auto-detect
- Factual claim
- Argument
- Opinion
- Propaganda / persuasion
- Prediction
- Question
- Satire / unclear
- Mixed content

Political or controversial content is not automatically treated as propaganda or opinion. The system separates verifiable assertions from subjective judgments and rhetorical techniques.

## Media attachments
Claims can include up to 5 uploaded files:
- Images: JPG, PNG, WEBP, GIF — visual analysis and extracted text
- Audio: MP3, WAV, M4A, OGG, WEBM — transcription
- Video: MP4, WEBM, MOV, MPEG — sampled visual frames plus audio transcription

Media-derived text and visual context are treated as **input to investigate**, not as independent proof. The report shows the attachment, extracted content, and uncertainties separately.

Limits:
- Images: 10 MB each
- Audio: 25 MB each
- Video: 25 MB each

The binary attachment is processed in memory and is not stored as a permanent file. History stores the extracted analysis metadata so reports remain reproducible without retaining the uploaded binary.

## Core flow
**Input → content/media extraction → live web research → evidence collection → source validation → type-specific analysis → report → assessment → sources → save → share**

## Environment
Set:
- `GROQ_API_KEY`
- `GROQ_MODEL`
- `GROQ_FALLBACK_MODEL`
- `GROQ_VISION_MODEL`
- `GROQ_TRANSCRIPTION_MODEL`
- `DATABASE_URL`

Never expose API keys in frontend code or commit them to Git.

## Local
```bash
pip install -r requirements.txt
uvicorn app.main:app --reload
```

## Render
Production branch: `main`

Build:
```bash
pip install -r requirements.txt
```

Start:
```bash
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

Health:
`/api/health`

## API
- `POST /api/fact-check`
- `POST /api/fact-check/media` — multipart text + attachments
- `POST /api/fact-check/url`
- `POST /api/chat`
- `GET /api/history`
- `GET /api/history/{id}`
- `DELETE /api/history/{id}`
- `DELETE /api/history`
- `GET /api/health`

## Accuracy and safety
The model receives only evidence returned by the application's search layer for evidence-based conclusions. Source URLs are restricted to URLs actually returned by that layer. When evidence is insufficient, the application reports uncertainty/UNVERIFIED rather than presenting unsupported verification as fact. URL fetching respects robots.txt, uses SSRF protections, and does not bypass paywalls or anti-bot controls.
