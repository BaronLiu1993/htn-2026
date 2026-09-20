# Handoff: show evidence when there is no Policy

Continue UnderwriteIQ in `/Users/naveed/htn-2026`.

Read `backend/AGENTS.md` and `frontend/AGENTS.md` before you edit. Do not add backend tests. Do not use Playwright. Conserve live Federato and OpenAI use.

This handoff supersedes the “empty drawer is correct” reading for **SUB-2025-00126**. The API nulls are honest about **Policy**. They are **not** honest about name, risk state, or building TIV. Those facts exist in Federato on another path.

## Ubiquitous language

Use these words in code comments, activity copy, and later recaps:

- **submission** — one in-scope account in the selected guideline
- **in-scope** — matches Guideline A commercial property
- **Policy** — Federato Policy record linked by `Policy.submission`
- **Insured** — Federato Insured record linked by `Submission.insured`
- **headquarters location** — `Insured.hq` → Location
- **evidence** — source values used by the guideline
- **needs review** — a guideline fact is missing or unconfirmed
- **agent** — the bounded OpenAI evidence planner
- **Federato** — the live source

## What the underwriter sees

Submission **SUB-2025-00126** (id **126**) shows:

- Unnamed account
- State not available
- Insured value not available
- Premium not available
- Status **needs review**
- Note: “No linked property Policy was found”

That screen is wrong for name, risk state, TIV, construction, occupancy, and protection. Those values exist in Federato. Premium, business type, and claims do **not** exist. There is no Policy for this submission.

## Live source check (2026-09-20)

Do not treat this as a display bug. Confirm the source.

Submission 126:

- `line_of_business`: `property` (in-scope)
- `insured`: `19`
- `received_date`: `2025-08-02`
- **no `policy` field on Submission**

`Policy.where.submission.$in = [126]` returns **0 rows**. Total 0. There is no Policy.

`Submission` expand `insured`:

- Insured id `19`
- name **Lakeside Medical Group Group**

`Insured` expand `hq.buildings` (Location id `46`):

- state **TX**
- city Houston
- buildings:
  - id 87, TIV 20_509_000, year 1975, construction Joisted Masonry
  - id 88, TIV 9_199_000, year 1948, construction Masonry Non-Combustible
  - id 89, TIV 6_008_000, year 1998, construction Fire Resistive
- occupancy Physicians Offices
- protection_class 7

Building TIV sum = **35_716_000**.

Same pattern for the other 10 in-scope submissions with no Policy from the last live run: `115, 118, 131, 132, 133, 134, 138, 141, 143, 147`.

## Why the last agent run missed this

Last completed live run: `run_4944cb346357`.

1. Scope found 38 in-scope submissions. 126 is in that set.
2. The agent queried `Policy` with numeric `submission.$in` for all 38 ids. Federato returned **27** policies. 126 was not in that 27.
3. The agent then queried `Location` by headquarters ids from **other** policies. Those 23 rows did not attach to the selected submissions. The agent stopped.
4. Analyze no longer loads Insured during scope. If the agent does not expand `Submission.insured`, the name is **Unnamed account**.
5. The ledger note “No linked property Policy” is applied to Building and Location facts whenever no Policy exists. That hides the Insured → headquarters path.

The ID-type fix is still valid. Numeric `$in` is required. It is not sufficient. Name, state, and TIV can exist with **no Policy**.

## Correct product behavior

| Guideline fact | Source when Policy exists | Source when Policy is absent | 126 |
|---|---|---|---|
| Line of business | Submission | Submission | visible (`property`) |
| Insured name | Insured.name | `Submission.insured` → name | should be visible |
| Risk state | Location.state | `Insured.hq.state` | should be **TX** |
| TIV | Policy.tiv or sum(Building.tiv) | sum of `Insured.hq.buildings.tiv` | should be **35.7M** |
| Construction / year | Building | same buildings | should be visible |
| Occupancy / protection | Building / Location | same | should be visible |
| Premium | Policy.premium | none | stay missing |
| Submission type / new business | Policy.business_type | none (Submission has no type field) | stay missing |
| Five-year losses | Policy.claims | none | stay missing |

Needs review can remain if new business or losses are missing. The drawer must still show name, TX, and TIV.

## Work, in order

### 1. Agent: second search when Policy is missing

Files: `backend/app/agent.py`, `backend/app/evidence_search.py`

After a Policy search, coverage must list **in-scope ids with no Policy**, as native query ids.

Instruct the agent:

- If a submission has no Policy, do not stop.
- Query `Submission` for those ids.
- Use expand `insured.hq.buildings` (schema: Insured.hq → Location, Location.buildings → Building).
- Keep declared identifiers and relationship fields.
- Do not hardcode a Policy expand payload.

Search note for the underwriter must say, in short sentences, that N submissions have no Policy and that the next search uses the insured headquarters.

### 2. Map Insured → headquarters → buildings

Files: `backend/app/live_data.py`, `backend/app/evidence_ledger.py`

After ingest:

- Name from Insured.name
- State from Location.state (including `Insured.hq`)
- TIV = Policy.tiv if present, else sum of Building.tiv on the submission graph
- Keep buildings found through Insured.hq, not only `Policy.exposure_units.location.buildings`

Fix `_missing_source_note`. Do not say “no Policy” for Building or Location when those records can come from Insured.hq. Say “no Policy” only for Policy, premium, business type, and claims.

### 3. Activity copy in simplified English

Files: `backend/app/agent.py`, `backend/app/service.py`, `backend/app/tool_gateway.py`, `frontend/app/(dashboard)/underwriting-queue.tsx`

Each activity row is two short sentences:

1. **Why** (`purpose`): the underwriting goal.
2. **Result** (`result_summary`): what the search found.

Rules:

- One idea per sentence.
- Use the ubiquitous language above.
- Do not show fact ids, adapter names, JSON, or resource dumps.
- Keep the same Queue / Run activity / account drawer layout.

Examples:

- Why: “Find submissions that match this guideline.”
- Result: “38 submissions are in scope. 120 submissions are outside scope.”
- Why: “Need the Policy for the commercial property submissions.”
- Result: “The search returned 27 policies. 11 submissions have no Policy.”
- Why: “Need the insured name, risk state, and building values.”
- Result: “The search returned insured headquarters and buildings. 11 submissions now have a name and a risk state.”

### 4. Verify once

Compile. Run `backend/tests/test_schema_registry.py` and `backend/tests/test_demo_federato.py`. Run frontend lint and `tsc --noEmit`. Run `scripts/smoke_openai_agent.py --fixture`.

Then one live Guideline A run. Done when **SUB-2025-00126** shows:

- name Lakeside Medical Group Group (not Unnamed account)
- risk state TX
- TIV about 35.7M
- premium still not available
- needs review is allowed if new business or losses stay missing
- activity uses the short sentences above

## Boundaries

Do not hardcode a Policy expand. Do not change guideline thresholds. Do not assess outside-scope submissions. Do not invent premium or losses. Do not push unless asked.

Live Federato already showed the Insured → headquarters path. The user approved live Federato and OpenAI in this project. Ask before a second live run if one already proved the 126 fields.
