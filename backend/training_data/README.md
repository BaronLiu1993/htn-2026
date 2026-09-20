# Appetite-Driven Underwriting Training Data

This package implements the versioned underwriting benchmark as an auditable policy/appetite matrix and exports Baseten-ready supervised fine-tuning data. It evaluates stated appetite only. Its labels are synthetic benchmark labels, not historical carrier underwriting decisions or a universal risk-quality score.

## Benchmark inputs

- `benchmark/appetites.json`: the five versioned carrier profiles, using the supplied schema version `1.0`.
- `benchmark/policies.json`: six live-policy references and ten synthetic boundary, counterfactual, and missing-information policies. Its `live_query` is the documented Federato query envelope for retrieving the expanded live record.

Live policy `facts` are only the cohort-selection facts. In production, query the stable `source.id`, retain the raw expanded API response outside this directory, then normalize locations, buildings, and claims into the `facts` shape. The evaluator never needs API credentials and this package never stores them.

## Pull expanded live policies

The collector fetches the benchmark's documented `live_query`, discovers the live API schema first, paginates expanded `Policy` records, and writes both raw and normalized data beneath ignored `live_data/`. Set the Federato credentials in your shell; do not add them to files.

```bash
cd backend
export FEDERATO_CLIENT_ID="..."
export FEDERATO_CLIENT_SECRET="..."
python -m training_data.federato_collector --limit 500
python -m training_data.generate \
  --policies training_data/live_data/expanded-policies.json \
  --mode regression \
  --output training_data/output
```

`raw-policy-snapshot.json` retains the API schema and source records for audit/debugging. `expanded-policies.json` merges normalized, deduplicated live policies with the committed synthetic benchmark. Both are ignored by git. Fields that cannot be safely derived remain `null` with a `normalization_warnings` entry, becoming valid missing-information examples instead of invented data.

The normalizer derives five-year loss value only from claims whose `date_of_loss` falls in the five years before the policy effective date. A claim with an unusable loss date leaves the field null rather than understating losses. Re-run this logic against an existing snapshot without another API request:

```bash
python -m training_data.federato_collector \
  --rebuild-from-raw training_data/live_data/raw-policy-snapshot.json
```

## Enrich with synthetic counterfactuals

After collection, create deterministic profile-aware variants from only the real live policy families. The enricher samples premium, TIV, building-year, and loss boundaries, missing material facts, prohibited/referral hazards, multistate exposure, location-count exceptions, and open/litigated claims as applicable to each selected appetite. Every generated policy has `kind: "synthetic"` and `counterfactual_of` its live source, so the generator keeps the whole family in one split.

Only live policies with complete core appetite facts are eligible as synthetic sources. Incomplete live policies remain in canonical data as genuine missing-information cases, but they do not create artificial complete variants.

```bash
cd backend
python -m training_data.synthetic_enricher \
  --policies training_data/live_data/expanded-policies.json \
  --output training_data/live_data/enriched-policies.json \
  --per-appetite 4
python -m training_data.generate \
  --policies training_data/live_data/enriched-policies.json \
  --mode regression \
  --synthetic-to-live-ratio 3 \
  --output training_data/output
```

With 500 fetched live policies and the default four variants per appetite, this produces up to 10,000 synthetic policy variants. The five-profile matrix then supplies roughly 52,500 policy/appetite SFT examples before approved teacher recommendations are added.

The full canonical matrix is retained for audit. Qwen-facing train and validation data starts with all live rows plus a deterministic synthetic subset capped by `--synthetic-to-live-ratio` (default `3`). Exact model-visible prompts are emitted once and assigned to one eligible split with 60/20/20 targets, preventing duplicate examples and exact-prompt train/test leakage while leaving policy-family assignments intact. The `sft_sampling_manifest.json` file records available, unique, removed, and selected counts. Policy IDs, scenario names, and run metadata are intentionally excluded from Qwen prompts.

Each evaluation takes one `policy.facts` object and one complete `appetite` snapshot. The deterministic evaluator applies these configured rule types:

- state and business-type eligibility
- inclusive premium, TIV, and loss ceilings
- exclusive building-year minimums
- prohibited hazards, referral hazards, multistate referral, and renewal mitigation referral
- location-count and open/litigated-claim rules
- missing fact and profile condition handling

## Generate benchmark data

From `backend/`:

```bash
python -m training_data.generate \
  --mode regression \
  --output training_data/output
```

Supported modes:

- `demo`: first live policy against all five appetites.
- `matrix`: six live policies against all five appetites, for 30 cells.
- `boundary`: ten synthetic policies against all five appetites.
- `regression`: all live and synthetic policies against all five appetites.

The output directory contains:

- `evaluation-matrix.json`: effective expected label, original deterministic label, and audit evaluation for every selected policy/appetite pair. Review before treating any result as production ground truth.
- `canonical/{train,validation,test}.jsonl`: traceable input/target pairs.
- `baseten_sft/{train,validation,test}.jsonl`: one JSONL record per example, containing exactly the conversational `messages` array consumed by TRL `SFTTrainer`.
- `teacher_candidates.jsonl`: local audit mapping for the sampled train/validation rows. Identifiers stay here and never enter model prompts.
- `teacher_review_queue.jsonl`: candidates for frontier-model drafting and underwriter approval; this is not automatically included as training data.
- `split_manifest.json`: policy-family split assignments. Counterfactuals remain with their source policy.
- `manifest.json`: versions, checksums, and output counts.

## Output contract

Every Qwen assistant target has exactly these fields:

```text
expected_disposition, expected_rules
```

`expected_disposition` is `accept`, `refer`, `decline`, or `insufficient_information`. `expected_rules` uses stable underwriting categories such as `building_age`, not implementation-specific appetite keys. The rich rule and evidence ledger remains in canonical records and `evaluation-matrix.json` as internal audit data; it is not part of Qwen's output contract.

## Distillation

1. Generate deterministic matrix labels and retain the held-out test split.
2. Prepare and submit the selected train/validation rows to GPT-5.6 Sol through the OpenAI Batch API. The teacher never receives policy IDs or deterministic labels.
3. Import the results. The importer writes corrections only where the complete normalized two-field teacher label disagrees with the deterministic evaluator.
4. Regenerate with `--reviewed-recommendations`. A correction replaces the deterministic target for that cell and any byte-identical model prompt; it is never added as a conflicting duplicate. Both labels and provenance remain in canonical audit records.
5. Measure held-out disposition accuracy and rule-category precision/recall on the prompt-exclusive test export. Test messages are never included in Qwen training.

```bash
cd backend
python -m training_data.teacher_labeler prepare
python -m training_data.teacher_labeler submit --chunk 1
python -m training_data.teacher_labeler wait --chunk 1
# Repeat submit/wait for every chunk reported by prepare_summary.json.
python -m training_data.teacher_labeler import
python -m training_data.generate \
  --policies training_data/live_data/enriched-policies.json \
  --mode regression \
  --synthetic-to-live-ratio 3 \
  --reviewed-recommendations training_data/output/teacher_corrections.jsonl \
  --output training_data/output
```

`prepare` defaults to 475 requests per chunk to stay below modest organization enqueue limits. `submit` and `wait` require `OPENAI_API_KEY` in the process environment. Batch artifacts, API resource IDs, model outputs, and correction files remain under ignored `output/`; credentials are never written to disk. Repeated byte-identical prompts are resolved by teacher majority; an exact tie retains the deterministic label. A resulting correction is propagated to equivalent prompts so the same Qwen input can never have conflicting targets.

## Baseten LoRA job

Baseten Training Jobs supports local JSON files bundled with the project, and TRL `SFTTrainer` consumes the exported chat-format `messages` column directly. The included Training Jobs setup fine-tunes `Qwen/Qwen3-8B` with a LoRA adapter on a single H100. It uses assistant-only loss, three epochs, micro-batches of one with 16-step gradient accumulation, gradient checkpointing, and a 1,024-token limit to keep memory bounded. Qwen thinking is disabled by `/no_think` in the training system prompt and `enable_thinking=False` during generation evaluation.

The job evaluates validation and test data after training in chunks of four prompts. Strict JSON, exact-match, disposition, rule precision/recall/F1, confusion data, and every raw completion are saved beneath `$BT_CHECKPOINT_DIR` so Baseten synchronizes them with the adapter.

Regenerate the SFT files after changes to the system prompt:

```bash
cd backend
python -m training_data.generate \
  --policies training_data/live_data/enriched-policies.json \
  --mode regression \
  --synthetic-to-live-ratio 3 \
  --reviewed-recommendations training_data/output/teacher_corrections.jsonl \
  --output training_data/output
```

Install and authenticate the current Baseten CLI on macOS (it requires `uv` on `PATH`):

```bash
brew install uv
brew tap basetenlabs/baseten
brew trust --formula basetenlabs/baseten/baseten
brew install baseten
baseten auth login
```

```bash
cd backend/training_data
baseten train push --config baseten_config.py --team 15
```

Keep the returned job ID, then monitor it from the Baseten training dashboard or with `baseten train job logs --job-id <job_id> --tail`.

The generated `output/` directory is ignored by git but must be present when the project is submitted. Override memory-related settings without editing code by setting `MAX_LENGTH`, `EVAL_BATCH_SIZE`, or `MAX_NEW_TOKENS` in the job environment. Deploy the latest completed adapter only after checking `generation_eval_summary.json`:

```bash
baseten train checkpoint deploy --job-id <job_id> --dry-run
baseten train checkpoint deploy --job-id <job_id>
```
