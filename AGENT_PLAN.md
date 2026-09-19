# UnderwriteIQ AI Agent Plan

## Goal

Turn the existing deterministic underwriting application into an auditable AI agent that reasons about which Federato data to request, executes schema-valid queries, adapts to results, and explains deterministic appetite decisions without inventing underwriting policy.

## Design principles

1. **The model plans; code decides.** OpenAI selects and adapts evidence queries. The versioned appetite engine remains the decision authority.
2. **Schema before query.** The agent must inspect the runtime Federato schema rather than assume resources or fields.
3. **Every query is constrained.** Model tool arguments are strict, every query is parsed as JSON, locally validated, limited, and then executed.
4. **Evidence before prose.** AI explanations may summarize only deterministic outcomes and evidence returned by tools.
5. **No hidden reasoning disclosure.** The product stores concise query purposes, adaptations, tool results, and decision summaries—not private chain-of-thought.
6. **Graceful fallback.** If OpenAI is unavailable, the complete deterministic workflow still runs and is labeled accordingly.

## Agent loop

```text
Queue + deterministic assessment summaries
                  |
                  v
        OpenAI Responses API agent
                  |
        +---------+----------+
        |         |          |
 inspect_schema  get_appetite  query_federato
        |         |          |
        +---------+----------+
                  |
        validate and execute tools
                  |
        return results to the model
                  |
          adapt or finish
                  |
                  v
    structured AgentReport JSON
                  |
                  v
  grounded explanations + visible trace
```

## Tools

### `inspect_schema`

Returns a compact runtime digest containing resources, fields, types, and references. It is cached for the run.

### `get_appetite`

Returns the active appetite ID, version, effective date, requirements, target preferences, and canonical evidence facts.

### `query_federato`

Accepts a human-readable purpose plus a JSON-encoded query. The server:

1. parses the JSON;
2. restricts top-level query keys;
3. validates resources, fields, references, operators, sorting, and pagination;
4. caps each page at 100 records;
5. executes against Federato in live mode or the representative query fixture in demo mode;
6. returns a bounded result and records the action in the run trace.

## Bounded behavior

- Maximum six model turns per run.
- Maximum four Federato query tool calls per run.
- Maximum 100 records per query response.
- Network timeout inherited from backend settings.
- Invalid queries return a safe validation error to the model so it can repair once.
- Only the supplied `schema` and `query` Federato actions remain available.

## Structured result

The agent must produce:

- a concise plan summary;
- the evidence strategy it used;
- adaptations made after tool results;
- one grounded explanation for each requested assessment;
- referenced submission/evidence identifiers;
- limitations or missing data.

The backend validates this with Pydantic and ignores explanations for unknown submission IDs.

## Scoring and ranking

The existing versioned appetite pack remains authoritative. Hard failures cannot be offset by target preferences or model prose. Ranking stays:

1. target;
2. acceptable;
3. needs review;
4. out of appetite;
5. target matches;
6. evidence completeness;
7. received date;
8. stable submission ID.

## UI changes

- Show `OpenAI agent` or `Deterministic fallback` beside the run.
- Display the model name when used.
- Put query purpose, selected fields, validation failures, adaptations, and result summaries in Agent activity.
- Label AI-authored explanations while preserving rule breakdowns and source evidence.

## Testing strategy

### Unit tests

- strict tool loop handling;
- tool-call limit enforcement;
- invalid query rejection;
- unknown submission explanations ignored;
- AI failure falls back to deterministic explanations;
- schema and appetite tools return bounded data.

### Integration tests

- scripted OpenAI responses call all tools and produce a valid report;
- API response exposes agent mode, plan summary, model, and traces;
- deterministic classifications and ranking are unchanged;
- 60-record synthetic queue completes within the bounded workflow.

### Live acceptance test

When credentials are available:

1. validate the OpenAI key with a minimal Responses API call;
2. discover the real Federato schema;
3. run the agent over the full 50+ submission queue;
4. confirm at least one schema inspection, appetite inspection, and dynamic query call;
5. confirm no invalid evidence IDs or decision changes;
6. record latency, model, tool count, and any fallback.

## Challenge mapping

| Federato criterion | Implementation |
| --- | --- |
| Queries API successfully | Existing OAuth client plus agent query tool |
| Reasons about data to request | OpenAI schema/appetite/query tool loop |
| Dynamic queries | Model-authored, locally validated query JSON |
| Adapts based on results | Tool outputs return to the same bounded agent loop |
| Scores and ranks | Versioned deterministic appetite engine |
| Explains every decision | Structured, evidence-grounded AgentReport |
| Handles edge cases | Missing/conflict states, safe validation errors, bounded fallback |
| Auditable reasoning | Query purpose and adaptation summaries in trace |
| Polished output | Existing queue and detail drawer |

The core live acceptance checks now pass against 158 Federato submissions. External enrichment remains an optional future enhancement.
