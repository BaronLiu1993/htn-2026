"""Baseten Training Job entry point for Qwen3-8B LoRA supervised fine-tuning."""

from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path
from typing import Any

import torch
from datasets import Dataset, DatasetDict, load_dataset
from peft import LoraConfig
from transformers import AutoModelForCausalLM, AutoTokenizer
from trl import SFTConfig, SFTTrainer


MODEL_ID = os.environ.get("MODEL_ID", "Qwen/Qwen3-8B")
DATA_DIR = Path(os.environ.get("UNDERWRITING_SFT_DATA_DIR", "output/baseten_sft"))
CHECKPOINT_DIR = Path(os.environ.get("BT_CHECKPOINT_DIR", "./checkpoints"))
MAX_LENGTH = int(os.environ.get("MAX_LENGTH", "1024"))
EVAL_BATCH_SIZE = int(os.environ.get("EVAL_BATCH_SIZE", "4"))
MAX_NEW_TOKENS = int(os.environ.get("MAX_NEW_TOKENS", "128"))
TARGET_FIELDS = {"expected_disposition", "expected_rules"}
DISPOSITIONS = {"accept", "refer", "decline", "insufficient_information"}
RULE_CATEGORIES = {
    "business_type",
    "state",
    "premium",
    "total_tiv",
    "building_age",
    "loss_history",
    "location_count",
    "claims_status",
    "hazard_exposure",
    "multistate_exposure",
    "primary_risk_location",
}


def _load_data() -> DatasetDict:
    data_files: dict[str, str] = {}
    for split in ("train", "validation", "test"):
        path = DATA_DIR / f"{split}.jsonl"
        if path.exists() and path.stat().st_size:
            data_files[split] = str(path)
    if "train" not in data_files:
        raise ValueError(f"Training dataset is missing or empty: {DATA_DIR / 'train.jsonl'}")
    return load_dataset("json", data_files=data_files)


def _target_from_row(row: dict[str, Any]) -> dict[str, Any]:
    messages = row["messages"]
    if not isinstance(messages, list) or len(messages) < 2 or messages[-1].get("role") != "assistant":
        raise ValueError("Every evaluation row must end with an assistant target")
    target = json.loads(messages[-1]["content"])
    if set(target) != TARGET_FIELDS:
        raise ValueError("Assistant target does not match the two-field output schema")
    return target


def _safe_divide(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _audit_token_lengths(dataset: DatasetDict, tokenizer: AutoTokenizer) -> None:
    """Fail before allocating the model if any record would be truncated."""

    for split, rows in dataset.items():
        lengths = [
            len(
                tokenizer.apply_chat_template(
                    row["messages"],
                    tokenize=True,
                    add_generation_prompt=False,
                    enable_thinking=False,
                )
            )
            for row in rows
        ]
        longest = max(lengths, default=0)
        print(f"{split}: {len(lengths)} examples; maximum token length={longest}")
        if longest > MAX_LENGTH:
            raise ValueError(
                f"{split} contains a {longest}-token record, exceeding MAX_LENGTH={MAX_LENGTH}; "
                "increase MAX_LENGTH instead of silently truncating training data"
            )


@torch.inference_mode()
def _generation_metrics(
    model: AutoModelForCausalLM,
    tokenizer: AutoTokenizer,
    dataset: Dataset,
    split: str,
) -> dict[str, Any]:
    """Run bounded, non-thinking generation in small chunks and score strict JSON."""

    model.eval()
    model.config.use_cache = True
    tokenizer.padding_side = "left"
    records: list[dict[str, Any]] = []
    confusion: Counter[str] = Counter()
    disposition_totals: Counter[str] = Counter()
    disposition_correct: Counter[str] = Counter()
    true_rule_count = predicted_rule_count = matched_rule_count = 0
    valid_json = schema_valid = exact_match = disposition_match = 0

    for start in range(0, len(dataset), EVAL_BATCH_SIZE):
        rows = [dataset[index] for index in range(start, min(start + EVAL_BATCH_SIZE, len(dataset)))]
        prompts = [
            tokenizer.apply_chat_template(
                row["messages"][:-1],
                tokenize=False,
                add_generation_prompt=True,
                enable_thinking=False,
            )
            for row in rows
        ]
        encoded = tokenizer(
            prompts,
            return_tensors="pt",
            padding=True,
            truncation=True,
            max_length=MAX_LENGTH,
        ).to(model.device)
        generated = model.generate(
            **encoded,
            do_sample=False,
            max_new_tokens=MAX_NEW_TOKENS,
            pad_token_id=tokenizer.pad_token_id,
            eos_token_id=tokenizer.eos_token_id,
        )
        completions = tokenizer.batch_decode(
            generated[:, encoded["input_ids"].shape[1] :],
            skip_special_tokens=True,
        )

        for row, completion in zip(rows, completions):
            expected = _target_from_row(row)
            expected_disposition = expected["expected_disposition"]
            expected_rules = set(expected["expected_rules"])
            disposition_totals[expected_disposition] += 1
            true_rule_count += len(expected_rules)
            predicted: dict[str, Any] | None = None
            error: str | None = None
            try:
                parsed = json.loads(completion.strip())
                valid_json += 1
                if (
                    isinstance(parsed, dict)
                    and set(parsed) == TARGET_FIELDS
                    and parsed.get("expected_disposition") in DISPOSITIONS
                    and isinstance(parsed.get("expected_rules"), list)
                    and all(isinstance(rule, str) for rule in parsed["expected_rules"])
                    and set(parsed["expected_rules"]).issubset(RULE_CATEGORIES)
                    and len(parsed["expected_rules"]) == len(set(parsed["expected_rules"]))
                ):
                    predicted = parsed
                    schema_valid += 1
                else:
                    error = "schema_mismatch"
            except json.JSONDecodeError:
                error = "invalid_json"

            if predicted is not None:
                predicted_disposition = predicted["expected_disposition"]
                confusion[f"{expected_disposition}->{predicted_disposition}"] += 1
                if predicted_disposition == expected_disposition:
                    disposition_match += 1
                    disposition_correct[expected_disposition] += 1
                predicted_rules = set(predicted["expected_rules"])
                predicted_rule_count += len(predicted_rules)
                matched_rule_count += len(expected_rules & predicted_rules)
                if predicted == expected:
                    exact_match += 1
            else:
                confusion[f"{expected_disposition}->invalid"] += 1

            records.append(
                {
                    "expected": expected,
                    "prediction": predicted,
                    "raw_completion": completion,
                    "error": error,
                }
            )

    rule_precision = _safe_divide(matched_rule_count, predicted_rule_count)
    rule_recall = _safe_divide(matched_rule_count, true_rule_count)
    rule_f1 = _safe_divide(2 * rule_precision * rule_recall, rule_precision + rule_recall)
    total = len(dataset)
    summary = {
        "split": split,
        "examples": total,
        "generation_batch_size": EVAL_BATCH_SIZE,
        "valid_json_rate": _safe_divide(valid_json, total),
        "schema_valid_rate": _safe_divide(schema_valid, total),
        "exact_match_rate": _safe_divide(exact_match, total),
        "disposition_accuracy": _safe_divide(disposition_match, total),
        "rule_micro_precision": rule_precision,
        "rule_micro_recall": rule_recall,
        "rule_micro_f1": rule_f1,
        "disposition_recall": {
            disposition: _safe_divide(disposition_correct[disposition], count)
            for disposition, count in sorted(disposition_totals.items())
        },
        "confusion": dict(sorted(confusion.items())),
    }
    output = {"summary": summary, "records": records}
    (CHECKPOINT_DIR / f"generation_eval_{split}.json").write_text(
        json.dumps(output, indent=2, sort_keys=True) + "\n"
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return summary


def main() -> None:
    dataset = _load_data()
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)

    # Build the configuration before downloading or allocating the model so an
    # incompatible dependency/API combination fails quickly and cheaply.
    config = SFTConfig(
        output_dir=str(CHECKPOINT_DIR),
        num_train_epochs=3,
        per_device_train_batch_size=1,
        per_device_eval_batch_size=1,
        gradient_accumulation_steps=16,
        gradient_checkpointing=True,
        gradient_checkpointing_kwargs={"use_reentrant": False},
        learning_rate=1e-4,
        # Transformers 5.x accepts a fraction here and interprets it as a
        # ratio of total training steps.
        warmup_steps=0.05,
        logging_steps=5,
        save_strategy="epoch",
        eval_strategy="epoch" if "validation" in dataset else "no",
        load_best_model_at_end="validation" in dataset,
        metric_for_best_model="eval_loss",
        greater_is_better=False,
        save_total_limit=2,
        max_length=MAX_LENGTH,
        assistant_only_loss=True,
        packing=False,
        bf16=True,
        tf32=True,
        seed=42,
        data_seed=42,
        report_to="none",
    )

    tokenizer = AutoTokenizer.from_pretrained(MODEL_ID, use_fast=True)
    tokenizer.padding_side = "right"
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    _audit_token_lengths(dataset, tokenizer)
    model = AutoModelForCausalLM.from_pretrained(
        MODEL_ID,
        dtype=torch.bfloat16,
        device_map="auto",
        use_cache=False,
    )

    trainer = SFTTrainer(
        model=model,
        args=config,
        train_dataset=dataset["train"],
        eval_dataset=dataset.get("validation"),
        processing_class=tokenizer,
        peft_config=LoraConfig(
            r=16,
            lora_alpha=32,
            target_modules="all-linear",
            lora_dropout=0.05,
            task_type="CAUSAL_LM",
        ),
    )
    trainer.train()
    trainer.save_model(str(CHECKPOINT_DIR))
    tokenizer.save_pretrained(str(CHECKPOINT_DIR))

    trainer.model.gradient_checkpointing_disable()
    summaries = {}
    for split in ("validation", "test"):
        if split in dataset:
            summaries[split] = _generation_metrics(trainer.model, tokenizer, dataset[split], split)
            torch.cuda.empty_cache()
    (CHECKPOINT_DIR / "generation_eval_summary.json").write_text(
        json.dumps(summaries, indent=2, sort_keys=True) + "\n"
    )


if __name__ == "__main__":
    main()
