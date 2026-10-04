# Fix Notes

## Latest investigation

Render logs showed `meta-llama/llama-prompt-guard-2-22m` being selected after the configured model returned 404. That model is not compatible with the application chat prompt. The provider selector is now allowlisted by generative model families and explicitly excludes guard/classifier/transcription/safety infrastructure models.

The same logs showed Groq rejecting oversized prompts. The application now bounds claim input, research text, evidence excerpts, article text, and chat history before requests are sent.

Production Snapshot Notes

This snapshot includes the media evidence normalization fix and a broader production hardening pass.

The original production crash was caused by model output containing strings where the code expected dictionaries. The repository now normalizes recoverable model output before accessing dictionary fields.

The repository also handles stale Groq text-model configuration more gracefully, but Groq model availability remains account-dependent. Vision support similarly requires a currently available multimodal Groq model.

No GitHub commits, branches, or pull requests were created for this downloadable snapshot.

## Latest production fix — Groq HTTP 429
The previous media failure was caused by Groq throttling a discovered multimodal model. The service previously converted that upstream 429 into a generic 502.

The media pipeline now treats 429 as a first-class provider condition. It retries the same model only within a bounded budget, honors `Retry-After` when it is within the configured retry window, never switches models because of throttling, and returns HTTP 429 with a retry hint when the provider remains unavailable. Each investigation also has a configurable vision-call budget.
