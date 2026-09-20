# Appetite-driven underwriting benchmark

This benchmark evaluates an agent's ability to apply an underwriter's stated appetite to
the same policy facts. It is intentionally not a universal risk-quality score.

The live cases intentionally store only stable API identifiers. A runner should fetch them
from the Federato API using the query envelope documented in
`../resources/fixed/query-request-body.md`, then normalize the expanded policy into the
case shape used by the synthetic fixtures.

The expected outcomes are benchmark labels, not historical underwriting decisions from a
carrier. The five profile appetites are synthetic but grounded in the factors represented in
`../resources/fixed/2025-appetite-guidelines.md`; they require underwriter review before use
as production ground truth.

## Case shape

The benchmark has three source files:

- `appetites.json`: five versioned carrier appetite profiles
- `policies.json`: six live-policy references plus ten synthetic policies
- `evaluation-matrix.json`: expected results for each policy/appetite pair

The live demo is a 6 × 5 matrix (30 evaluations). The synthetic set supplies ten
appetite-specific boundary, counterfactual, and missing-information evaluations.

For live cases, `facts` contains only the facts used while selecting the initial cohort.
The runner should retain the raw API response separately and derive complete facts from
expanded locations, buildings, and claims.

## Evaluation contract

For every matrix cell, the agent must return a structured disposition (`accept`, `refer`,
`decline`, or `insufficient_information`), the appetite rules that drove it, evidence paths,
and any recommended conditions. Score the disposition, rule attribution, evidence grounding,
missing-data detection, and condition quality separately.

## Suggested run modes

- `demo`: one live policy evaluated under several appetites
- `matrix`: all 30 live policy/appetite combinations
- `regression`: all matrix and synthetic evaluations
- `boundary`: the ten synthetic evaluations

## Run the checked-in benchmark

From the repository root (the runner loads `backend/.env` automatically):

```bash
python3 backend/run_benchmark.py \
  --output /tmp/underwriteiq-benchmark.json
```

The runner also accepts a Baseten OpenAI-compatible deployment. Set both variables
to use it; `BASETEN_MODEL` is optional and defaults to `baseten-model`:

```bash
BASETEN_API_KEY="..." \
BASETEN_MODEL_URL="https://model-.../v1" \
BASETEN_MODEL="baseten-model" \
BENCHMARK_PROVIDER="baseten" \
python3 backend/run_benchmark.py
```

The runner calls the configured model for every matrix cell, then scores its
returned disposition, rule categories, and conditions against the benchmark
labels. By default it calls `gpt-5.6-sol` using `OPENAI_API_KEY`; set
`OPENAI_MODEL` only to override that default. It uses a Baseten deployment when
`BENCHMARK_PROVIDER=baseten` and both Baseten variables are set. The report
never writes either API key.
