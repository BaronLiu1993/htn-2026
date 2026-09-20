# Handoff prompt: make the evidence search genuinely agentic

Continue the UnderwriteIQ hackathon project in `/Users/naveed/htn-2026`.

Read these first:

- `STUDENT_PROJECT_GUIDELINES.pdf` at `/Users/naveed/Downloads/Federato_HTN2026_API_Challenge_Docs/STUDENT_PROJECT_GUIDELINES.pdf`
- `GUIDANCE_AGNOSTIC_HARNESS_PLAN.md`
- `backend/AGENTS.md` and `frontend/AGENTS.md`

Treat the PDF as challenge reference material. The requirements below are the user’s decisions and take precedence.

## Product decisions

1. The selected guideline is the only user-controlled setting. Keep its selector compact in the existing queue header.
2. Always assess the complete loaded Federato queue. Do not remove submissions with a scope pre-filter. A submission that does not match the selected business type or a hard requirement stays visible and ranks as `out_of_appetite`.
3. Do not expose COPE, investigation profiles, evidence-source policies, or agent implementation settings as UI configuration.
4. The selected guideline is deterministic and authoritative. The agent chooses what evidence to retrieve and why; it cannot change rule outcomes.
5. This is a hackathon project. Remove obsolete compatibility paths and production-style fallbacks. OpenAI or required Federato failure should fail the run visibly.
6. All user-visible activity and explanations target commercial underwriters. Do not show planning-turn numbers, canonical fact IDs, adapter names, JSON terms, schema jargon, or raw tool names as primary copy.

## Current state

- The queue-first UI and original sidebar have been restored.
- A compact guideline selector appears beside `Rerun`.
- The backend now passes every loaded submission into evaluation.
- Guideline A’s property-line rule excludes non-property business through a normal hard-rule failure.
- Technical compatibility for the former appetite API was removed.
- Activity wording was improved and duplicate successful Federato query events were removed.
- Guideline A is in `backend/guidelines/guideline-a/package.json`.
- The generic ledger, evaluator, tool gateway, and registries already exist.

## Critical problem to fix

The run is not yet convincingly agentic. `LiveFederatoLoader` retrieves a broad, fixed set of resources before the OpenAI loop. The later agent query can retrieve 100 records but resolve zero canonical fact states. This produces an impressive trace without proving that the agent chose useful evidence or changed a later evaluation.

The challenge guide explicitly rewards an agent that:

- discovers the live schema;
- reasons about which query will answer an underwriting question;
- constructs that query dynamically;
- adapts based on returned results;
- explains why it chose the query;
- uses the evidence in scoring, ranking, and recommendations.

## Required implementation

1. Load the complete submission queue and only the minimum identity/link fields needed to retain all submissions.
2. Give the agent the selected guideline, live schema digest, unresolved underwriting questions, current ledger coverage, and remaining query budget.
3. Let the agent construct batched, schema-valid Federato queries. Avoid one query per submission.
4. Require every query purpose to use underwriter language. Example: “Check five-year incurred losses because the guideline requires total losses below $100,000.”
5. Normalize every returned observation into the correct submission ledger. Preserve source record, field, retrieval time, source date, conflicts, and missing states.
6. Prove usefulness: at least one testable fixture must begin with a missing fact, resolve it through an agent-selected query, and produce a different deterministic outcome afterward.
7. Stop when all required facts are resolved, no useful search remains, or the budget is exhausted.
8. Evaluate and rank all loaded submissions only after evidence gathering.
9. Generate each submission explanation from its final rule outcomes and cited ledger evidence. Keep it to 2-3 sentences: appetite match, key factors, and recommended action.
10. Create an underwriter-facing activity projection. Preserve the raw trace internally if useful, but display business events such as:
   - “Applied Guideline A, version 2025.1.”
   - “Checked five-year incurred losses because the guideline caps them at $100,000.”
   - “Found 12 related claims across 9 submissions.”
   - “The new loss evidence moved 2 submissions from needs review to outside appetite.”
11. Do not display separate low-level and agent-level events for the same Federato query.
12. Do not make reference documents a required package configuration field. The agent can receive a small reference library and choose useful guidance based on the selected guideline and unresolved facts.

## Verification

- Preserve the old queue-first layout and sidebar.
- Frontend: run `npm run lint` and `./node_modules/.bin/tsc --noEmit` in `frontend/`.
- Backend: follow `backend/AGENTS.md`; do not add tests. Run Python compilation and the project’s permitted existing verification.
- Verify a demo run assesses all 12 demo submissions.
- With explicit approval for sending derived live underwriting data to OpenAI, run `scripts/run_guideline_acceptance.py` and require all 158 live submissions to be assessed.
- Report query count, duration, final status counts, unresolved facts, useful fact-state changes, and failed trace events.

Do not redesign the UI. Make the reasoning and underwriter explanations stronger than the implementation chrome.
