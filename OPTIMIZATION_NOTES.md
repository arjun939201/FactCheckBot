# Product Optimization Pass — 2026-10-04

## Focus
- Filter irrelevant web evidence before it reaches the fact-check report.
- Preserve user text as the primary investigation subject.
- Prevent weak search hits from being mapped as supporting/contradicting evidence.
- Reduce redundant search probes and same-domain evidence.
- Expose compact relevance reasons such as `Exact phrase` or `2/5 key terms match`.
- Simplify report micro-copy: Bottom line, Claims, Supports, Contradicts, Why, Sources, Media.
- Improve media upload UX with drag-and-drop and removable file chips.
- Keep the research UI focused on evidence rather than AI-generated filler.

## Verification
- Python compile: passed
- Pytest: 22 passed
- JavaScript syntax check: passed
- Runtime caches and generated files excluded from release archive
