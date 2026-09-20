# UnderwriteIQ model comparison

The two model choices serve different roles behind the same queue contract:

| Provider | Best use | Retrieval | Final decision authority |
| --- | --- | --- | --- |
| OpenAI (`gpt-5.6-terra`) | Flexible schema inspection and evidence-query planning | Model-authored Federato queries | Deterministic guideline engine |
| UnderwriteIQ (`Qwen3-8B`) | Fast, narrow appetite classification | Deterministic guideline-declared Federato retrieval | Deterministic guideline engine |

## Live same-account smoke comparison

Measured on submission `SUB-2026-00007` with Guideline A on September 19, 2026. These are single-run observations, not a statistically complete benchmark.

| Metric | UnderwriteIQ Qwen3-8B | OpenAI gpt-5.6-terra |
| --- | ---: | ---: |
| Model latency | 581 ms | 18,966 ms |
| Prompt tokens | 480 | 44,976 |
| Completion tokens | 18 | 1,621 |
| Total tokens | 498 | 46,597 |
| Deterministic outcome reached | `out_of_appetite` | `needs_review` |
| Material result | Found renewal and ineligible-state failures | Federato query repair failed; facts remained missing |

On this one case, UnderwriteIQ used about 94x fewer tokens and its model call was about 33x faster:

- Latency ratio: `18,966 / 581 = 32.64x`
- Token ratio: `46,597 / 498 = 93.57x`

Its strongest honest advantage is its small, strict insurance appetite contract: it does not spend tokens inspecting schemas or planning tools. OpenAI remains more adaptable when the source schema is unfamiliar or the task needs open-ended reasoning.

The full request still takes longer than the Qwen model call because Federato schema discovery and pagination dominate wall-clock time. The UI therefore displays total run time separately from model time.

## How the live measurement was performed

Both requests were sent through the same local `POST /api/analysis/batch` endpoint, used the same backend process, selected the same Guideline A version `2025.1`, and requested only Federato submission ID `7`. The provider field was the only intentional request-level change:

```json
{"guideline_id":"guideline-a","guideline_version":"2025.1","submission_ids":["7"],"model_provider":"baseten"}
```

and then:

```json
{"guideline_id":"guideline-a","guideline_version":"2025.1","submission_ids":["7"],"model_provider":"openai"}
```

The deterministic guideline evaluator was unchanged between runs. It remained the authority for the four-state UI result.

### What “model latency” includes

- For UnderwriteIQ, a monotonic timer starts immediately before launching the authenticated Baseten CLI request and stops after the complete response is received. It therefore includes local request serialization, CLI startup, network transit, Baseten queue/serving time, generation, and response parsing. It is not GPU kernel time alone.
- For OpenAI, a monotonic timer wraps each Responses API call. The displayed value is the sum across all model turns, including tool-planning and final-report turns. Federato tool execution time between those calls is not counted as model latency.
- Total run time is measured around the complete service operation. It includes schema discovery, Federato queries, normalization, deterministic evaluation, model calls, validation, and response construction.

The raw service results were:

| Timing | UnderwriteIQ | OpenAI |
| --- | ---: | ---: |
| Total request | 29,909 ms | 28,503 ms |
| Model portion | 581 ms | 18,966 ms |
| Non-model remainder | 29,328 ms | 9,537 ms |

The OpenAI request was slightly shorter end to end only because its model-authored Federato query failed and it stopped without retrieving the material policy facts. That is not evidence of a faster successful workflow. The comparison supports the claim that Qwen inference was faster; it does not yet support claiming that the complete Qwen queue refresh is 33x faster.

### How tokens were measured

Token counts come from each provider's response `usage` object, not from a local character estimate:

- UnderwriteIQ: Baseten's OpenAI-compatible `prompt_tokens` and `completion_tokens` for the single classification request.
- OpenAI: `input_tokens` and `output_tokens` summed over all Responses API turns. Because the evidence agent carries schema, guideline, coverage, and tool history through multiple turns, repeated context is included in the total.

Tokens between different tokenizer families are not perfectly interchangeable. They are still useful here as provider-reported workload and context-size measurements, but should not be presented as a precise hardware-efficiency comparison.

### What was and was not held constant

Held constant:

- Submission and selected appetite guideline
- Federato environment and local backend
- Four-state deterministic evaluator
- Final hard-requirement precedence
- JSON validation before results reach the UI

Intentionally different:

- OpenAI chose and repaired Federato queries as an evidence-planning agent.
- UnderwriteIQ received evidence gathered by the deterministic guideline-declared retrieval path and performed the narrow classification task it was fine-tuned for.

This is a product-path comparison, not an isolated model-science benchmark with byte-identical prompts. A strict model-vs-model accuracy claim requires running both models on the identical 231 normalized benchmark prompts.

## Held-out specialist benchmark

The deployed LoRA checkpoint's existing 231-case held-out evaluation reported 96.1% disposition accuracy, 95.9% rule micro-F1, 100% valid JSON, and 92.6% exact match. The split is decline-heavy, so these numbers should be presented with that limitation. OpenAI has not yet been run against the identical 231-case set, so this is not evidence that Qwen is universally more accurate than OpenAI.

Metric definitions:

- Disposition accuracy: fraction of cases whose `accept`, `refer`, `decline`, or `insufficient_information` label exactly matched the deterministic oracle.
- Rule micro-F1: micro-averaged precision/recall balance over the returned rule-category set.
- Exact match: both disposition and normalized rule-category set matched.
- Valid JSON: completion parsed successfully under the strict output contract.

The benchmark is synthetic/oracle-labelled appetite evaluation, not historical loss-performance validation. It measures faithful rule application, not actuarial profitability or an underwriter replacement rate.

## How to explain the result in a demo

1. Start with role separation: OpenAI is the flexible evidence planner; Qwen is the trained appetite specialist.
2. State the raw measurements before the ratios: `581 ms and 498 tokens` versus `18,966 ms and 46,597 tokens` on one live account.
3. Say “model-call latency,” not “the whole application is 33x faster.” Federato retrieval still dominates the Qwen path.
4. Explain the quality check: Qwen agreed with the deterministic hard-rule result; OpenAI's attempted evidence query failed on this run.
5. Add the benchmark evidence with its limitation: 96.1% held-out disposition accuracy on a decline-heavy, synthetic/oracle-labelled set.
6. Close with why the architecture is safe: neither model can override deterministic hard exclusions, and the underwriter still reviews the recommendation.

## Demo claim

Use this precise claim: **UnderwriteIQ is a smaller specialist that is much faster and more token-efficient on the normalized appetite task, while OpenAI is the more flexible evidence-planning model. Deterministic rules protect both paths from overriding hard carrier requirements.**
