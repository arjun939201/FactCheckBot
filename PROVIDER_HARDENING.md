# Provider hardening

## What this release fixes

- Generic text-model discovery no longer accepts Guard, moderation, embedding, reranker, Whisper, or TTS models.
- `llama-prompt-guard-*` can never be selected as the generic analysis/chat model.
- Research prompts are bounded before they reach Groq.
- Media-derived evidence is compacted before final analysis.
- Article analysis is compacted and bounded.
- Provider payload-too-large errors are returned as HTTP 413.
- Provider availability errors are returned as HTTP 503.
- Multimodal 429 handling remains separate: it honors `Retry-After`, uses bounded retries, and never switches models on a rate limit.

## Recommended Render environment

The current known-good text model from the deployment logs is:

```text
GROQ_MODEL=openai/gpt-oss-120b
```

If `GROQ_MODEL` is left at an obsolete value, the application can now recover through compatible-model discovery, but setting the known-good model avoids the initial failed request.

Do not paste `GROQ_API_KEY` into source code or chat.

## Important limitation

A 429 from Groq is an upstream quota/rate-limit condition. Application code can prevent retry storms and report it accurately, but it cannot create additional Groq capacity.
