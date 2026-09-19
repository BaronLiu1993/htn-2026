# UnderwriteIQ

UnderwriteIQ is a Federato-first underwriting triage agent for Hack the North 2026. It ranks commercial property submissions against the supplied carrier appetite, explains every result with source evidence, and surfaces missing or conflicting information instead of guessing.

> Which submissions should an underwriter review first, and why?

UnderwriteIQ does not approve, price, quote, or bind insurance coverage.

## What is implemented

- Federato OAuth client-credentials flow with server-side token caching.
- Live schema discovery, local query validation, and schema-declared relationship traversal.
- Deterministic appetite evaluation with hard requirements before target preferences.
- Versioned, validated appetite JSON reloaded on every analysis run.
- Generic rule operators, so approved threshold and eligibility changes do not require evaluator code changes.
- `target`, `acceptable`, `needs_review`, and `out_of_appetite` classifications.
- COPE evidence coverage that separates informational factors from carrier decision rules.
- OpenAI Responses API agent with strict schema, appetite, and Federato query tools.
- Dynamic schema-grounded query construction with bounded repair and deterministic fallback.
- Evidence-grounded AI explanations that cannot override appetite outcomes.
- Stable queue ranking, evidence-backed explanations, and auditable tool traces.
- Responsive Next.js queue, filters, search, result details, evidence, and activity views.
- Deterministic demo mode with 12 representative submissions when credentials are absent.
- Backend tests for rule priority, ambiguity handling, ranking, schema validation, and the API.

## Architecture

```mermaid
flowchart LR
    U[Underwriter] --> UI[Next.js dashboard]
    UI --> API[FastAPI]
    API --> O[Analysis service]
    O --> A[OpenAI evidence-planning agent]
    O --> S[Schema registry]
    O --> Q[Federato client]
    O --> E[Deterministic evaluator]
    O --> T[Evidence and traces]
    A --> S
    A --> Q
    S --> F[Federato schema action]
    Q --> FQ[Federato query action]
    E --> R[Ranked assessments]
    T --> R
    R --> UI
```

OpenAI performs bounded evidence planning and explanation. It cannot override verified rule outcomes.

## Run locally

### Backend

From the repository root:

```bash
python3 -m pip install -r backend/requirements.txt
python3 -m uvicorn backend.main:app --reload --port 8000
```

Without credentials, the API starts in demo mode. API documentation is at [http://localhost:8000/docs](http://localhost:8000/docs).

### Frontend

In another terminal:

```bash
cd frontend
npm install
npm run dev
```

Open [http://localhost:3000](http://localhost:3000). The frontend defaults to `http://localhost:8000`. Copy `frontend/.env.example` to `frontend/.env.local` to change it.

## Live Federato mode

Set credentials only in the backend process environment:

```bash
export FEDERATO_CLIENT_ID="..."
export FEDERATO_CLIENT_SECRET="..."
python3 -m uvicorn backend.main:app --reload --port 8000
```

Never prefix these secrets with `NEXT_PUBLIC_` or place them in the frontend. Other documented defaults are in `.env.example`.

Live mode discovers the schema first, queries only resources found in that schema, validates requests locally, and resolves schema-declared relationships. The live schema remains authoritative for resource names, fields, and references.

## OpenAI underwriting agent

Set the OpenAI key only in the backend environment. Never put it in `frontend/.env.local`, a `NEXT_PUBLIC_` variable, source code, screenshots, or committed files.

```bash
export OPENAI_API_KEY="..."
export OPENAI_MODEL="gpt-5.6-terra"
export OPENAI_REASONING_EFFORT="medium"
python3 -m uvicorn backend.main:app --reload --port 8000
```

Each queue run gives the agent three strict tools:

- `inspect_schema` returns the runtime Federato resources, fields, and references.
- `get_appetite` returns the active, versioned carrier rules.
- `query_federato` validates and executes model-authored query JSON with bounded pagination.

The model must inspect schema and appetite, issue at least one dynamic query, and return a schema-constrained report. The backend accepts an AI explanation only when its submission and evidence identifiers are verified. If OpenAI times out, refuses, returns invalid data, exceeds tool limits, or is not configured, deterministic classification, ranking, and explanations continue unchanged.

The complete design and challenge acceptance matrix are in [AGENT_PLAN.md](AGENT_PLAN.md).

## Appetite logic

The 2025 property rules require new property business in an eligible state, TIV at or below $150M, premium from $50K-$175K, buildings newer than 1990, a majority of acceptable construction, and aggregate applicable five-year losses below $100K.

Target state, TIV, premium, and building age are displayed as a transparent match count, such as `3/4`. They are not presented as an actuarial risk score. Ambiguous boundaries such as exactly 1990, exactly $100K in losses, or a 50/50 construction mix produce `needs_review`.

The active policy is [backend/appetite/commercial-property-2025.json](backend/appetite/commercial-property-2025.json). It stores the appetite ID, version, effective date, hard requirements, target preferences, operators, and thresholds. The API validates and reloads it at the beginning of every analysis run, so an approved edit takes effect on the next run without a Python code change or service restart.

Federato schema discovery controls where evidence is retrieved. The appetite file controls how that evidence is evaluated. A data schema does not define carrier underwriting policy.

## API

- `GET /api/health`
- `GET /api/schema/status`
- `GET /api/appetite/status`
- `GET /api/submissions`
- `POST /api/analysis/batch`
- `GET /api/runs/{run_id}`
- `GET /api/runs/{run_id}/trace`

Omit `submission_ids` or send `null` to analyze the full queue:

```json
{
  "submission_ids": ["101", "102"],
  "force_schema_refresh": false
}
```

## Checks

```bash
python3 -m pytest backend/tests -q
cd frontend
npm run lint
npm run build
```

## Current limitations

- Live normalization uses schema-aware traversal plus conservative semantic aliases because the challenge does not provide a static field catalog. Verify the first credentialed run against the actual schema and sample records.
- Runs and traces are in memory and reset when the backend restarts.
- Analysis is synchronous and sized for the challenge dataset.
- External enrichment is deferred because Federato marks it optional.
- A real OpenAI run requires a server-side key and network access. Automated tests use a scripted transport so CI never consumes API credits.
- Live acceptance has been verified against the 158-submission Federato challenge dataset; demo mode remains available as a representative schema/query sandbox when credentials are absent.
- Baseten deployment and fine-tuning are deferred.
- Construction is TIV-weighted when all per-building values exist; otherwise building count is used and disclosed.

See [MVP.md](MVP.md) for the complete scope and decision record.
