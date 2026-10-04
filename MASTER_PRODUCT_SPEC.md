# FactCheckBot — Senior Product & Engineering Specification

## Product goal

FactCheckBot is an evidence-first claim verification system. It should accept claims, articles, URLs, and eventually media, research available online evidence, compare independent sources, detect contradictions, and produce an auditable claim-by-claim report.

The system must never treat an LLM's confidence as proof. AI interprets evidence; the application owns provenance, source metadata, corroboration, uncertainty, and verdict validation.

## Core flow

Input → extraction → claim decomposition → research planning → parallel source discovery → source retrieval → evidence extraction → source quality/independence → contradiction analysis → claim verification → confidence calculation → report → history/share

## Product capabilities

### 1. Claim checking
- Single factual claim
- Multiple claims in one input
- Paragraph/social-post analysis
- Claim decomposition into independently verifiable units
- Claim-type classification: factual, numerical, causal, legal, political, historical, attribution, motive, prediction, opinion, argument, satire/unclear, mixed

### 2. Online research
The production architecture should use a provider/connector model rather than pretending one search engine covers the entire web.

Provider categories:
- General web search
- News search/RSS
- Government/official sources
- Academic/scientific sources
- Legal/court sources
- Fact-check databases/sites
- YouTube/public video discovery
- Public social-source discovery where APIs/access permit it

Every provider should return normalized results with URL, title, publisher/domain, publication time, snippet/content, provider, and retrieval time.

### 3. Evidence system
Every evidence item should have:
- evidence ID
- claim ID
- source ID
- exact supporting excerpt where available
- whether it supports/contradicts/is neutral
- directness
- source quality
- publication/retrieval time
- independence group

Never invent URLs, quotations, evidence IDs, or source content.

### 4. Source evaluation
Source quality should consider:
- primary-source status
- authority
- transparency
- editorial/research standards
- specificity
- freshness
- independence
- corroboration

Bias is not equivalent to falsehood. A source's perspective should not automatically invalidate factual material.

### 5. Independence and corroboration
Do not count syndicated/copy-pasted reporting as independent confirmation. Group sources that originate from the same press release, wire story, dataset, quotation, or underlying document.

Reports should distinguish:
- total references found
- genuinely independent corroborating sources
- primary sources

### 6. Contradiction handling
When sources disagree, first determine whether they actually measure the same thing:
- date/time period
- geography
- population
- definition
- methodology
- dataset/version

Then identify genuine contradictions and explain them rather than forcing a binary answer.

### 7. Time awareness
Claims and sources are time-sensitive. Store publication/effective/retrieval dates and distinguish:
- true at the time of the claim
- true currently
- outdated
- historically true but no longer current

### 8. Verdicts
Recommended verdict vocabulary:
- TRUE
- MOSTLY TRUE
- PARTLY TRUE
- MISLEADING
- MOSTLY FALSE
- FALSE
- UNSUPPORTED
- UNVERIFIABLE
- OUTDATED
- CONTEXT MISSING
- SATIRE / NOT A FACTUAL CLAIM

Confidence should be computed from evidence strength, source quality, independent corroboration, directness, agreement, contradiction, and uncertainty — not copied from an LLM response.

### 9. Article/URL checking
URL → fetch → parse title/author/date/body → extract claims → independently research claims → identify unsupported/misleading sections → article-level summary.

### 10. Media pipeline
Media extraction is separate from verification.

Image:
image → OCR/visual extraction → candidate claims → research

Audio:
audio → transcription → candidate claims → research

Video:
video → sampled frames/OCR + audio transcription → candidate claims → research

A detected statement in media is not evidence that the statement is true.

## Agentic architecture

Use a small number of purposeful workers instead of many decorative agents:

Research Manager
- controls the workflow
- assigns research tasks
- enforces time/source/token budgets
- decides whether evidence is sufficient

Claim Analyst
- decomposes input
- classifies claim types

Search Workers
- execute searches across provider categories

Evidence Worker
- extracts and normalizes relevant evidence

Source Worker
- evaluates source metadata and independence

Contradiction Worker
- compares competing evidence

Verification Worker
- produces grounded claim assessments

Report Worker
- produces the human-readable final report

The workflow must have hard limits and stopping criteria.

## Provider architecture

AI and search providers should be replaceable. Recommended conceptual interfaces:

AIProvider
- text generation
- structured output
- vision when supported
- transcription when supported

SearchProvider
- search(query, filters)

SourceFetcher
- fetch(url)

This prevents the application from being coupled permanently to one provider.

## Recommended backend structure

```text
app/
  api/
  core/
  models/
  routes/
  services/
    ai/
    search/
    research/
    evidence/
    source_quality/
    media/
    reports/
  database/
  workers/
```

The current repository is an incremental implementation of this target. Do not claim that every connector listed above is already implemented.

## Database direction

Recommended entities:
- users
- research_runs
- claims
- claim_relationships
- sources
- evidence
- source_snapshots
- verdicts
- media_assets
- reports
- conversations

A research run should make the final answer reproducible and auditable.

## Production requirements

Before calling the project production-ready:

1. `/api/health` passes in production.
2. Text claim checking works against live evidence.
3. Multi-claim decomposition works.
4. Evidence IDs map to real retrieved sources.
5. Source links are valid and traceable.
6. Contradictory evidence is surfaced rather than hidden.
7. History works on Render PostgreSQL.
8. Share links work.
9. URL/article checking works.
10. Error handling does not expose secrets.
11. Tests pass.
12. Render deployment is manually verified.
13. Provider failures degrade gracefully.
14. Media support is only called production-ready after a real multimodal provider succeeds in the deployment environment.

## Learning strategy

This project is an engineering laboratory. Learn by reading and modifying:
- Python
- FastAPI
- async/await
- HTTP APIs
- search/retrieval
- structured JSON/Pydantic
- PostgreSQL/SQLAlchemy
- evidence ranking
- LLM integration
- agent orchestration
- testing
- logging
- Docker/Render
- production debugging

Build only features that strengthen the verification engine. Avoid turning every possible idea into a separate UI feature.
