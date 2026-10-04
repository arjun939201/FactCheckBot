# Production Fix Notes — 2026-10-04

## Fixed

### 1. Media fact-check 502 / AttributeError
The fact-check pipeline assumed that Groq would always return evidence entries as objects. In production, the model returned at least one evidence entry as a string URL, causing:

`AttributeError: 'str' object has no attribute 'get'`

The pipeline now normalizes list entries at the model-output boundary and filters them against evidence URLs actually retrieved by the application. The same hardening was applied to article URL fact-check sources.

### 2. Invalid top-level model output
The pipeline now explicitly rejects a non-object Groq response with a controlled `RuntimeError` instead of failing later with an unrelated Python exception.

### 3. Regression test
A regression test was added for string-valued evidence output.

## Not changed automatically

The Render logs also show:

`llama-3.3-70b-versatile` -> HTTP 404 model_not_found

The application already retries the configured model with `openai/gpt-oss-120b`. This patch does not guess a new Groq model name because model availability is account/region/provider dependent and hardcoding an unverified model would create another production failure.

If the fallback itself later becomes unavailable, update `GROQ_MODEL` / `GROQ_FALLBACK_MODEL` to models currently available to the Groq API key.

## Verification performed

- Python application and tests compile successfully with `compileall`.
- Full pytest could not be executed in this sandbox because outbound package installation is unavailable (`psycopg` could not be downloaded).
- No GitHub commit, branch, or pull request was created for this fix package.
