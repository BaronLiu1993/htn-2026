# Handoff: correct guideline scope, evidence queries, and queue counts

Continue UnderwriteIQ in `/Users/naveed/htn-2026`.

## Start here

- Current checkpoint: `4db103a` on local `main` (`Implement guideline-driven underwriting evidence search`). It has not been pushed by this task.
- Read `backend/AGENTS.md` and `frontend/AGENTS.md` before editing their code.
- Read this handoff before `GUIDANCE_AGNOSTIC_HARNESS_PLAN.md`. This handoff supersedes conflicting decisions in that older plan and the previous handoff.
- Challenge reference: `/Users/naveed/Downloads/Federato_HTN2026_API_Challenge_Docs/STUDENT_PROJECT_GUIDELINES.pdf`.
- The user wants to conserve usage. Do not use Playwright or browser testing. Do not add backend tests. Use focused existing verification and compilation.

## Latest product decision — replaces assess-everything behavior

The user rejected the current queue summary:

`158 submissions · 0 eligible · 9 unresolved · 149 excluded`

Identify submissions relevant to the selected guideline before collecting detailed evidence and assessing appetite. Query only the fields and related records needed for that work. Do not assess all 158 submissions against a property guideline simply because they are available.

For Guideline A, use its declared commercial-property scope. The relevant population may be around 40; discover its actual size from live data. Do not hard-code 40, the older estimate of 38, or the total of 158. We want to show the traces of how we search via the agent traces.

Keep these concepts separate:

- **Available:** total submissions in the source queue, if a cheap supported count is available.
- **In scope:** submissions matching the selected guideline's business scope; these receive evidence gathering, deterministic assessment, and ranking.
- **Outside scope:** submissions for a different business line; these are not appetite failures and do not appear as excluded assessments.
- **Scope unknown:** missing or conflicting scope evidence; report separately and attempt a focused lookup. Do not silently discard these submissions or count them as confirmed in scope.
- **Outside appetite:** an in-scope submission with a confirmed hard-rule failure. Keep it visible in the assessed queue.

Scope filtering is not a shortcut for hiding unfavorable underwriting results. Do not filter out in-scope accounts by premium, TIV, losses, construction, or other eligibility requirements before assessment.

The guideline selector remains the only user-controlled setting. Preserve the queue-first layout and sidebar. Do not add technical configuration controls.

## Current implementation and evidence

The code now loads submission identity/link fields, gives the agent the live schema and guideline, and updates an evidence ledger after each agent query. `EvidenceSearch` stores source records and rebuilds facts. Final explanations come from deterministic rule outcomes with source references. The UI uses an activity projection.

A ledger is the per-submission evidence record: each fact's value, state, source record, field, retrieval time, and available source date. It is needed to tell missing source data apart from failed retrieval or mapping. It is an internal audit structure, not another user setting.

Verification already completed:

- Frontend lint and TypeScript checks passed.
- Python compilation and seven existing schema/demo query checks passed.
- Offline replay: 12 assessments, 3 queries, 94 fact changes, 2 unresolved facts, zero failed trace events. Claim retrieval changed four actual intermediate outcomes from needs review to target/acceptable. This is a scripted replay, not proof of live model reasoning.
- One live acceptance run completed in 42.6 seconds: 158 assessments, 4 queries, 725 fact changes, 546 unresolved facts, 149 outside appetite, 9 needs review, zero failed events in that run's summary.

That live acceptance check only proved its old completion criteria. It did not establish adequate evidence coverage or correct scoping. Do not repeat the claim that these totals demonstrate a correct result.

The user subsequently supplied a different activity trace containing:

1. A failed broad evidence query: `Federato returned HTTP 500 ... [NOT_FOUND] Unknown field in path "id"`.
2. A successful first-segment query: 100 returned records and 725 changed facts.
3. Another successful query: 100 returned records and zero changed facts.
4. A completed-looking queue summary despite the failed query.

The exact failed payload was not captured in the supplied text. Do not claim to know which nested `id` caused it until the payload and schema are inspected. The 546 unresolved facts likewise have no established single cause yet. Pagination, relationship mapping, field normalization, and actual missing data all need evidence-based inspection.

## Remaining work, in order

### 1. Capture the evidence needed to diagnose the query failure

Files: `backend/app/tool_gateway.py`, `backend/app/agent.py`, `backend/app/schema_registry.py`, `scripts/run_guideline_acceptance.py`.

- Preserve the exact query payload and schema digest in an internal run artifact, together with returned counts, pagination, mapping counts, and failure details. Do not expose raw queries as primary UI copy or store credentials.
- Record which resource and field path failed. Inspect the live schema instead of assuming every object exposes `id`.
- Check how validation handles nested projections, expanded references, arrays, and identifier fields. A query passing local validation must not rely on invented paths.
- Retain the source's actual identifiers and relationships needed to assign evidence. Correct identifier assumptions in the loader, mapper, and agent instructions together.
- Reconcile the user's continued run after a required Federato failure with the intended fail-visible behavior. Ensure the UI does not display a previous successful run's summary as the summary of a failed rerun.

Done when the failing query shape has an explained cause and its replacement is grounded in the discovered schema.

### 2. Select the guideline population before detailed retrieval

Files: `backend/app/service.py`, `backend/app/live_data.py`, `backend/app/guideline_registry.py`, `backend/app/rule_engine.py`, `backend/app/models.py`.

- Resolve the selected package and discover the live schema.
- Translate the package's scope predicate to the actual source fields/relationships. Use a server-side scope query where supported. Use the proper pre-expansion `where` or post-expansion `filter` stage and `$elemMatch` at array boundaries.
- If server-side scope selection cannot preserve unknown values, retrieve only minimal identity and scope fields across the queue, separate known matches/nonmatches/unknowns, and fetch details only for candidates and targeted scope resolution.
- Retrieve every page of the selected population. Do not treat a 100-record page as the entire queue.
- Preserve separate scope counts and assessment counts in the API. Remove the current assignment that marks every loaded submission applicable.
- Do not silently omit scope-unknown accounts. Report their count and next action separately.

Done when assessment IDs equal the independently verified in-scope IDs, with scope-unknown submissions explicitly accounted for.

### 3. Make each evidence query targeted and useful

Files: `backend/app/agent.py`, `backend/app/evidence_search.py`, `backend/app/evidence_ledger.py`, `backend/app/live_data.py`.

- Give the agent the selected candidate IDs/link IDs, unresolved questions, current coverage, schema, and remaining query budget.
- Let the agent choose batched projections, expansions, and filters for those candidates. Avoid per-submission requests and broad unrelated resource scans.
- Return valid structured coverage and newly discovered relationship IDs after each query, so the agent can plan a useful follow-up without guessing links.
- Diagnose zero-change queries: distinguish repeated facts, unrelated records, records with no owner, unsupported mappings, and missing returned fields. Do not count returned records as useful evidence merely because they exist.
- Audit attribution by `(resource, source identifier)`, including shared records, nested references, and reverse links if the schema requires them. Never attach an unrelated record just because its ID matches in another resource.
- Confirm aggregate completeness before verifying building minima, construction shares, or loss totals. An unqueried or partially retrieved claim collection must not mean zero losses.
- Preserve contradictory observations, actual source paths, retrieval times, source dates, and missing states. Count state/value changes separately from resolved facts.
- Stop when required facts are resolved, no useful search remains, or budget is exhausted. Record the stop reason and unresolved facts by reason.

Done when a live agent-selected query demonstrably resolves a previously missing fact, and the final deterministic assessment uses that evidence. Preserve the existing offline replay as a fast development check.

### 4. Correct the queue summary and failure presentation

Files: `frontend/lib/types.ts`, `frontend/lib/api.ts`, `frontend/app/(dashboard)/underwriting-queue.tsx`, `backend/app/service.py`, `backend/app/evaluator.py`.

- Keep the existing layout and compact selector.
- Show a summary such as `N relevant submissions · E eligible · U need review · X outside appetite`, with `E + U + X = N` for completed assessments. Define eligible as target plus acceptable.
- If useful, show `N of T available submissions match this guideline` as secondary context. Use actual counts, not the illustrative N≈40 or T=158.
- Show scope-unknown accounts separately from assessed accounts with unresolved underwriting facts.
- Do not label known unrelated business as excluded by appetite rules.
- Show one business activity event per query, with evidence found, affected submissions, and useful changes. Keep implementation details internal.
- A required-source failure must fail the run visibly. Preserve any prior completed result only with a clear indication that it belongs to the previous run.
- Keep each explanation to 2–3 sentences: appetite result, evidence-backed key factors, and recommended action.

Done when the visible population, status totals, and run state agree with the underlying result.

### 5. Replace the old acceptance criteria and verify once

Files: `scripts/run_guideline_acceptance.py`, `scripts/smoke_openai_agent.py`, `README.md`.

- Remove assertions requiring 158 assessments and zero outside-scope submissions. Discover expected candidate IDs independently from minimal scope evidence and compare exact IDs, not just totals.
- Confirm no duplicate assessments, no missed pages, no unrelated assessments, and explicit handling of scope-unknown accounts.
- Report available/in-scope/outside-scope/scope-unknown counts, assessed count, duration, query count, status counts, unresolved facts by reason, useful fact changes, and failed events.
- Persist a local trace/ledger artifact for the run. Do not rely only on a terminal summary that cannot explain gaps afterward.
- Run Python compilation and relevant existing checks. Do not add backend tests. Run frontend lint and `tsc --noEmit` if frontend code changes. Do not use Playwright.
- Then run one focused live acceptance. The user explicitly approved live Federato access and sending derived underwriting data to OpenAI in this conversation; do not ask for that same consent again. Request tool-level escalation if the sandbox requires it.

Completion requires correct selection and useful evidence attribution, not merely a successful HTTP response or completed run. Report remaining gaps candidly rather than forcing all accounts to have complete evidence.

## Boundaries

Do not redesign the UI, add production-style fallback paths, change deterministic guideline thresholds, or hard-code a desired eligible count. Do not push or merge additional changes unless requested. Current progress is saved locally on main; `.playwright-cli/` is an untracked temporary artifact and should remain outside the implementation commit.
