# FactCheckBot — Development Roadmap

## Current snapshot

This ZIP is a snapshot of `arjun939201/FactCheckBot` based on the latest merged `main` development at commit:

`7b77ca4144cceca705a1f13fafddee21ad7b77e3`

The project currently includes:
- FastAPI backend
- Web frontend
- Live web evidence research
- Evidence/source ranking and validation
- Claim decomposition and claim-level assessments
- Multiple content-analysis modes
- Groq-powered text analysis
- Article URL parsing and checking
- History storage with PostgreSQL/SQLite support
- Shareable result flow
- AI follow-up chat
- Media upload pipeline for image/audio/video
- Render configuration
- Automated test configuration and GitHub Actions CI workflow

## Important current limitation

The media pipeline is implemented, but the currently configured Groq account does not expose a usable multimodal model. Production logs showed the configured vision models returning unavailable/model-not-found responses and dynamic discovery finding no usable multimodal model.

Therefore media should remain an experimental/non-blocking capability until a supported multimodal provider/model is configured.

## Core production target

Keep the main product focused on:

`Claim / URL -> research -> evidence -> claim-level verification -> report -> history -> share`

Do not add large new features until this core path is production-verified.

---

# Project ideas

## 1. Elementary projects — fast learning laboratories

These are deliberately small. Each should teach one engineering concept and finish quickly.

### A. URL Evidence Extractor
Input a URL and return title, author, publication date, clean text, and important links.

Learn: HTTP, HTML parsing, error handling, Pydantic models.

### B. Source Quality Scorer
Give a URL a deterministic quality score based on domain/category and explain the score.

Learn: rules, normalization, data structures, testable functions.

### C. Claim Splitter
Turn one paragraph into a list of atomic claims.

Learn: text processing, structured LLM output, validation.

### D. Evidence Deduplicator
Given many search results, remove duplicate or near-duplicate sources.

Learn: hashing, normalization, similarity, ranking.

### E. FactCheck CLI
A terminal version of the core fact-check pipeline.

Learn: Python packages, CLI design, service separation, configuration.

### F. Research Cache
Cache repeated searches for a short period.

Learn: caching, TTL, persistence, performance.

---

# 2. Agentic projects — serious portfolio work

These should be built only after the elementary projects and after FactCheckBot's core is stable.

### A. Research Agent
Given a question or claim, autonomously:
1. decomposes the task
2. creates search queries
3. searches multiple sources
4. evaluates source quality
5. identifies missing evidence
6. performs follow-up searches
7. produces a grounded research brief

Learn: agent loops, tool calling, planning, evidence grounding, stopping criteria.

### B. Fact-Checking Agent
An autonomous version of FactCheckBot.

Agent loop:
`Understand -> Decompose -> Search -> Evaluate -> Challenge -> Corroborate -> Decide -> Report`

The agent must be able to say "insufficient evidence" rather than forcing a verdict.

### C. News Monitoring Agent
Track selected topics and periodically identify meaningful new developments.

Learn: scheduled jobs, state, deduplication, change detection, notifications.

### D. Source Comparison Agent
Given a controversial event, collect reporting from different source categories and build a comparison showing agreement, disagreement, missing context, and unresolved questions.

Learn: multi-source synthesis and conflict detection.

### E. Claim Watch Agent
The user submits a recurring claim. The agent periodically checks whether new evidence changes its assessment.

Learn: persistent agent state, scheduled execution, evidence versioning.

### F. Research-to-Report Agent
Input a broad topic. The agent produces a structured research report with sources, evidence table, uncertainties, and follow-up questions.

Learn: multi-step workflows and document generation.

---

# 3. Optimistic projects — useful products with positive real-world value

These focus on helping people make better decisions rather than creating outrage or engagement bait.

### A. Civic Information Assistant
Explain government schemes, public services, official announcements, and eligibility requirements using primary sources.

### B. Student Opportunity Researcher
Find scholarships, internships, exams, fellowships, and government opportunities and explain eligibility and deadlines with sources.

### C. Consumer Claim Checker
Check product claims, pricing claims, warranty statements, and misleading advertisements against available evidence.

### D. Health Information Evidence Guide
Help users distinguish established evidence, preliminary findings, uncertainty, and unsupported health claims. It should clearly avoid pretending to diagnose users.

### E. Local Public Information Monitor
Track official notices affecting a selected district or city: transport, education, weather alerts, government notices, and public services.

### F. Knowledge Reliability Engine
A reusable backend service that answers:
- What is being claimed?
- What evidence supports it?
- What evidence contradicts it?
- How strong are the sources?
- What remains unknown?

This could become the common evidence layer for several future applications.

---

# Recommended learning order

1. Finish and production-verify FactCheckBot core.
2. Study every major module by reading and modifying it.
3. Build the elementary projects quickly.
4. Build one Research Agent.
5. Extend it into a Fact-Checking Agent.
6. Build one optimistic real-world product.
7. Reuse the evidence/search infrastructure instead of rebuilding everything.

## Rule

Do not build an application merely because an AI platform can be prompted to do the same task.

Build when the project teaches engineering, creates reusable infrastructure, solves a real workflow, or demonstrates a capability worth putting in a portfolio.

Use ChatGPT/Gemini/Perplexity directly when that is the fastest way to get a personal task done.
