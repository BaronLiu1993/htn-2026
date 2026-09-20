# Handoff: UnderwriteIQ (imported from Codex → Cursor)

Continue UnderwriteIQ in `/Users/kash/Documents/htn-2026`.

## Start here

- Current checkpoint: `80e57df` on `main` (`Merge pull request #1` — selectable Baseten underwriting model). In sync with `origin/main`.
- Read `backend/AGENTS.md` and `frontend/AGENTS.md` before editing their code.
- Read this handoff before `GUIDANCE_AGNOSTIC_HARNESS_PLAN.md`. This handoff supersedes conflicting decisions in older plan docs.
- Challenge reference (local): Federato HTN2026 student guidelines PDF under Downloads.
- Conserve usage: no Playwright/browser testing; do not add backend tests; use focused existing verification and compilation.
- Cursor always-on context: `.cursor/rules/underwriteiq-context.mdc` (extracted from Codex session `01a0ba72…`).

## What Codex already shipped

- Federato OAuth + live schema/query path, guideline packages, evidence ledger, deterministic evaluator, demo mode.
- OpenAI Responses agent for evidence planning; deterministic engine remains final authority.
- Distillation/training pipeline; Qwen deployed on Baseten; UI provider selector (`openai` | `baseten`).
- Performance write-up in `MODEL_COMPARISON.md`. Honest claim: Qwen model-call ~33× faster / ~94× fewer tokens on `SUB-2026-00007`; full request still ~30s because Federato retrieval dominates. Do not claim the whole app is 33× faster.
- Held-out specialist benchmark (decline-heavy, oracle labels): ~96% disposition accuracy / ~96% rule micro-F1 / ~93% exact match / 100% valid JSON — measures appetite-rule application, not underwriter replacement.

## Latest product decision — replaces assess-everything behavior

The user rejected queue summaries like:

`158 submissions · 0 eligible · 9 unresolved · 149 excluded`

Identify submissions relevant to the selected guideline **before** detailed evidence and appetite assessment. Query only fields/related records needed for that work. Do not assess all ~158 against a property guideline just because they are available.

For Guideline A, use its declared commercial-property scope. Discover the live in-scope population from data. Do not hard-code ~40, 38, or 158.

Keep these concepts separate:

- **Available:** total submissions in the source queue (cheap count if supported).
- **In scope:** match selected guideline business scope → evidence, assessment, ranking.
- **Outside scope:** different business line — not appetite failures; not “excluded” assessments.
- **Scope unknown:** missing/conflicting scope evidence — report separately; do not silently discard.
- **Outside appetite:** in-scope submission with confirmed hard-rule failure — keep visible.

Scope filtering is not a way to hide unfavorable underwriting results. Do not pre-filter in-scope accounts by premium, TIV, losses, construction, or other eligibility rules.

Guideline selector remains the only user-controlled setting. Preserve queue-first layout/sidebar. Do not add technical configuration controls.

## Known failure evidence (still open)

User-supplied activity trace:

1. Failed broad evidence query: `Federato returned HTTP 500 ... [NOT_FOUND] Unknown field in path "id"`.
2. Successful first-segment query: 100 records, 725 changed facts.
3. Another query: 100 records, zero changed facts.
4. Completed-looking queue summary despite the failed query.

Exact failed payload was not captured. Do not invent which nested `id` failed. 546 unresolved facts have no single proven cause yet (pagination, mapping, normalization, missing data).

## Remaining work, in order

### 1. Capture evidence to diagnose query failure

Files: `backend/app/tool_gateway.py`, `backend/app/agent.py`, `backend/app/schema_registry.py`, `scripts/run_guideline_acceptance.py`.

- Persist exact query payload + schema digest in an **internal** run artifact (counts, pagination, mapping, failures). Do not expose raw queries as primary UI or store credentials.
- Record failing resource/field path; inspect live schema — do not assume every object has `id`.
- Fix identifier assumptions across loader, mapper, and agent instructions together.
- Required-source Federato failure must fail the run visibly; do not show a previous successful run’s summary as the current run.

### 2. Select guideline population before detailed retrieval

Files: `backend/app/service.py`, `backend/app/live_data.py`, `backend/app/guideline_registry.py`, `backend/app/rule_engine.py`, `backend/app/models.py`.

- Translate package scope predicate to real source fields; prefer server-side scope query; paginate fully (do not treat one 100-record page as the queue).
- Preserve separate scope counts vs assessment counts. Stop marking every loaded submission applicable.
- Report scope-unknown explicitly.

Done when assessment IDs equal independently verified in-scope IDs, with scope-unknown accounted for.

### 3. Make each evidence query targeted and useful

Files: `backend/app/agent.py`, `backend/app/evidence_search.py`, `backend/app/evidence_ledger.py`, `backend/app/live_data.py`.

- Give the agent candidate IDs, unresolved questions, coverage, schema, remaining budget.
- Batch projections/expansions/filters for those candidates; avoid per-submission spam and unrelated resource scans.
- Diagnose zero-change queries honestly; attribute by `(resource, source identifier)`.
- Stop on resolved facts, no useful search left, or budget exhausted; record stop reason.

### 4. Correct queue summary and failure presentation

Files: `frontend/lib/types.ts`, `frontend/lib/api.ts`, `frontend/app/(dashboard)/underwriting-queue.tsx`, `backend/app/service.py`, `backend/app/evaluator.py`.

- Summary like `N relevant · E eligible · U need review · X outside appetite` with `E + U + X = N` (eligible = target + acceptable).
- Optional secondary: `N of T available match this guideline` using live counts.
- Scope-unknown separate from assessed-with-unresolved-facts.
- Do not label unrelated business as appetite exclusions.
- One business activity event per query; keep implementation details internal.
- Explanations stay 2–3 sentences: result, evidence-backed factors, recommended action.

### 5. Replace old acceptance criteria and verify once

Files: `scripts/run_guideline_acceptance.py`, `scripts/smoke_openai_agent.py`, `README.md`.

- Drop assertions requiring 158 assessments and zero outside-scope.
- Compare exact candidate IDs from independent minimal scope evidence.
- Persist local trace/ledger artifact for the run.
- Compile + existing checks; frontend lint/`tsc` if UI touched; no Playwright; no new backend tests.
- One focused live acceptance after that. Live Federato + OpenAI consent already granted in Codex — do not re-ask.

## Boundaries

Do not redesign the UI, add production-style fallback paths, change deterministic guideline thresholds, or hard-code a desired eligible count. Do not push or merge unless requested. Leave untracked local tooling (`.tools/`, training artifacts) out of implementation commits unless explicitly asked.

## Secrets note

Codex chat history contained pasted API keys/client secrets. Those must stay in `.env` only. If any secret was committed or shared outside `.env`, rotate it. Never paste secrets into rules, handoffs, or chat.
