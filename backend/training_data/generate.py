"""Generate benchmark matrices and Baseten/TRL SFT JSONL for underwriting."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from training_data.appetite import BENCHMARK_SCHEMA_VERSION, evaluate_appetite, model_target


SYSTEM_PROMPT = """You are an appetite-driven commercial insurance underwriting classifier.

Apply only the supplied appetite profile to the supplied policy facts. This is not a universal risk score, pricing recommendation, or historical carrier decision. Do not use outside knowledge or infer missing values.

Decision order: first decline for any evidenced hard-rule failure; otherwise return insufficient_information for a required missing, invalid, or contradictory fact; otherwise refer only for an evidenced explicit referral rule; otherwise accept. A rule threshold with min or max is inclusive. An exclusive minimum requires a value strictly above the threshold. A restricted-state rule fails when any policy state is ineligible.

Return JSON only, with exactly this schema and no additional keys:
{"expected_disposition":"accept | refer | decline | insufficient_information","expected_rules":["stable_rule_category"]}

expected_rules must list only the rule categories that determine the disposition. Use building_age for building-year rules, loss_history for five-year loss rules, hazard_exposure for hazard rules, and primary_risk_location for multistate location requirements. Use an empty array only when no rule category determines the result.

/no_think"""
SFT_SCHEMA_VERSION = "underwriting-sft-v2"
BENCHMARK_DIRECTORY = Path(__file__).with_name("benchmark")
MODEL_TARGET_FIELDS = {"expected_disposition", "expected_rules"}
ALLOWED_DISPOSITIONS = {"accept", "refer", "decline", "insufficient_information"}


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as error:
        raise ValueError(f"{path} is not valid JSON: {error.msg}") from error
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def _write_jsonl(path: Path, records: Iterable[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w") as handle:
        for record in records:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
            count += 1
    return count


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _split_counts(total: int) -> dict[str, int]:
    if total < 3:
        return {"train": total, "validation": 0, "test": 0}
    train = max(1, int(total * 0.6))
    validation = max(1, int(total * 0.2))
    test = total - train - validation
    if test == 0:
        train -= 1
        test = 1
    return {"train": train, "validation": validation, "test": test}


def _payload_fingerprint(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return _hash(encoded)[:16]


def _assign_splits(policies: list[dict[str, Any]], salt: str) -> dict[str, str]:
    """Keep a policy and all of its counterfactual variants in one split."""

    families: dict[str, list[str]] = {}
    for policy in policies:
        policy_id = str(policy["policy_id"])
        family_id = str(policy.get("counterfactual_of") or policy_id)
        families.setdefault(family_id, []).append(policy_id)
    targets = _split_counts(len(policies))
    assigned_counts = {split: 0 for split in targets}
    ordered = sorted(
        families,
        key=lambda family_id: (-len(families[family_id]), _hash(f"{salt}:{family_id}")),
    )
    assignments: dict[str, str] = {}
    for family_id in ordered:
        family_size = len(families[family_id])
        split = min(
            targets,
            key=lambda candidate: (
                sum(
                    ((assigned_counts[other] + (family_size if other == candidate else 0) - targets[other]) ** 2)
                    / targets[other]
                    for other in targets
                    if targets[other]
                ),
                candidate,
            ),
        )
        for policy_id in families[family_id]:
            assignments[policy_id] = split
        assigned_counts[split] += family_size
    return assignments


def _select_policies(policies: list[dict[str, Any]], mode: str) -> list[dict[str, Any]]:
    live = [policy for policy in policies if policy.get("kind") == "live"]
    synthetic = [policy for policy in policies if policy.get("kind") == "synthetic"]
    if mode == "demo":
        return live[:1]
    if mode == "matrix":
        return live
    if mode == "boundary":
        return synthetic
    return policies


def _sft_record(payload: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
    return {
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(payload, sort_keys=True)},
            {"role": "assistant", "content": json.dumps(target, sort_keys=True)},
        ]
    }


def _model_payload(appetite: dict[str, Any], facts: dict[str, Any]) -> dict[str, Any]:
    """Return only model-relevant facts; audit identifiers stay out of SFT prompts."""

    return {
        "task": "evaluate_appetite",
        "schema_version": SFT_SCHEMA_VERSION,
        "benchmark_schema_version": BENCHMARK_SCHEMA_VERSION,
        "appetite": appetite,
        "policy": facts,
    }


def _select_sft_candidates(
    candidates: list[dict[str, Any]],
    split: str,
    synthetic_to_live_ratio: int,
) -> list[dict[str, Any]]:
    """Keep all real rows and a balanced, deterministic synthetic subset for SFT."""

    if split == "test":
        return candidates
    live = [candidate for candidate in candidates if candidate["kind"] == "live"]
    synthetic = [candidate for candidate in candidates if candidate["kind"] != "live"]
    capacity = len(live) * synthetic_to_live_ratio
    if capacity <= 0:
        return live

    buckets: dict[str, list[dict[str, Any]]] = {disposition: [] for disposition in ALLOWED_DISPOSITIONS}
    for candidate in synthetic:
        sampling_target = candidate.get("sampling_target", candidate["target"])
        buckets[sampling_target["expected_disposition"]].append(candidate)
    for disposition in buckets:
        buckets[disposition].sort(key=lambda candidate: _hash(candidate["id"]))

    selected = list(live)
    indexes = {disposition: 0 for disposition in buckets}
    order = ("refer", "accept", "insufficient_information", "decline")
    while len(selected) - len(live) < capacity:
        added = False
        for disposition in order:
            index = indexes[disposition]
            if index >= len(buckets[disposition]) or len(selected) - len(live) >= capacity:
                continue
            selected.append(buckets[disposition][index])
            indexes[disposition] += 1
            added = True
        if not added:
            break
    return selected


def _dedupe_sft_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Emit each model-visible prompt once and reject inconsistent targets."""

    selected: dict[str, dict[str, Any]] = {}
    for candidate in sorted(candidates, key=lambda item: (item["kind"] != "live", _hash(item["id"]))):
        fingerprint = _payload_fingerprint(candidate["payload"])
        existing = selected.get(fingerprint)
        if existing is not None and existing["target"] != candidate["target"]:
            raise ValueError(f"Identical prompt has conflicting targets: {existing['id']} and {candidate['id']}")
        selected.setdefault(fingerprint, candidate)
    return list(selected.values())


def _read_reviewed(
    path: Path | None,
) -> tuple[dict[tuple[str, str], dict[str, Any]], dict[str, dict[str, Any]]]:
    if path is None:
        return {}, {}
    approved: dict[tuple[str, str], dict[str, Any]] = {}
    approved_prompts: dict[str, dict[str, Any]] = {}
    for line_number, line in enumerate(path.read_text().splitlines(), start=1):
        if not line.strip():
            continue
        record = json.loads(line)
        if record.get("review_status") != "approved":
            continue
        policy_id = str(record.get("policy_id", ""))
        appetite_id = str(record.get("appetite_id", ""))
        recommendation = record.get("recommendation")
        if not policy_id or not appetite_id or not isinstance(recommendation, dict):
            raise ValueError(f"{path}:{line_number} approved rows require policy_id, appetite_id, and recommendation")
        if set(recommendation) != MODEL_TARGET_FIELDS or recommendation.get("expected_disposition") not in ALLOWED_DISPOSITIONS:
            raise ValueError(f"{path}:{line_number} recommendation must use the exact Qwen output schema")
        if not isinstance(recommendation.get("expected_rules"), list) or not all(isinstance(rule, str) for rule in recommendation["expected_rules"]):
            raise ValueError(f"{path}:{line_number} expected_rules must be an array of strings")
        approved[(policy_id, appetite_id)] = recommendation
        prompt_fingerprint = record.get("prompt_fingerprint")
        if prompt_fingerprint:
            previous = approved_prompts.setdefault(str(prompt_fingerprint), recommendation)
            if previous != recommendation:
                raise ValueError(f"{path}:{line_number} prompt fingerprint has conflicting recommendations")
    return approved, approved_prompts


def generate(
    policies_path: Path,
    appetites_path: Path,
    output_dir: Path,
    mode: str,
    split_salt: str,
    reviewed_path: Path | None,
    synthetic_to_live_ratio: int,
) -> dict[str, Any]:
    policies_document = _read_json(policies_path)
    appetites_document = _read_json(appetites_path)
    policies = policies_document.get("policies")
    appetites = appetites_document.get("appetites")
    if policies_document.get("schema_version") != BENCHMARK_SCHEMA_VERSION:
        raise ValueError("Unsupported policies schema_version")
    if appetites_document.get("schema_version") != BENCHMARK_SCHEMA_VERSION:
        raise ValueError("Unsupported appetites schema_version")
    if not isinstance(policies, list) or not isinstance(appetites, list):
        raise ValueError("Benchmark files require policies and appetites arrays")

    selected = _select_policies(policies, mode)
    for policy in selected:
        if not isinstance(policy, dict) or not policy.get("policy_id") or not isinstance(policy.get("facts"), dict):
            raise ValueError("Every policy requires policy_id and object-valued facts")
    for appetite in appetites:
        if not isinstance(appetite, dict) or not appetite.get("appetite_id") or not isinstance(appetite.get("rules"), dict):
            raise ValueError("Every appetite requires appetite_id and object-valued rules")

    assignments = _assign_splits(selected, split_salt)
    approved, approved_prompts = _read_reviewed(reviewed_path)
    canonical: dict[str, list[dict[str, Any]]] = {split: [] for split in ("train", "validation", "test")}
    sft_candidates: dict[str, list[dict[str, Any]]] = {split: [] for split in ("train", "validation", "test")}
    review_queue: list[dict[str, Any]] = []
    evaluations: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()

    for policy in selected:
        policy_id = str(policy["policy_id"])
        facts = policy["facts"]
        split = assignments[policy_id]
        for appetite in appetites:
            appetite_id = str(appetite["appetite_id"])
            audit = evaluate_appetite(facts, appetite)
            deterministic_target = model_target(audit)
            model_payload = _model_payload(appetite, facts)
            reviewed = approved.get((policy_id, appetite_id)) or approved_prompts.get(
                _payload_fingerprint(model_payload)
            )
            target = reviewed or deterministic_target
            provenance = (
                "gpt-5.6-sol teacher disagreement correction"
                if reviewed
                else "deterministic synthetic benchmark label; requires underwriter review before production use"
            )
            payload = {
                "task": "evaluate_appetite",
                "schema_version": SFT_SCHEMA_VERSION,
                "benchmark_schema_version": BENCHMARK_SCHEMA_VERSION,
                "run_mode": mode,
                "policy_id": policy_id,
                "appetite": appetite,
                "policy": facts,
            }
            canonical[split].append(
                {
                    "id": f"{policy_id}:{appetite_id}",
                    "policy_id": policy_id,
                    "appetite_id": appetite_id,
                    "split": split,
                    "kind": policy.get("kind"),
                    "input": payload,
                    "target": audit,
                    "model_target": target,
                    "deterministic_model_target": deterministic_target,
                    "label_provenance": provenance,
                }
            )
            sft_candidates[split].append(
                {
                    "id": f"{policy_id}:{appetite_id}",
                    "policy_id": policy_id,
                    "appetite_id": appetite_id,
                    "split": split,
                    "kind": policy.get("kind"),
                    "target": target,
                    "sampling_target": deterministic_target,
                    "payload": model_payload,
                }
            )
            counts[f"{split}_appetite_examples"] += 1
            evaluations.append(
                {
                    "policy_id": policy_id,
                    "appetite_id": appetite_id,
                    "expected": target,
                    "deterministic_expected": deterministic_target,
                    "audit": audit,
                    "label_provenance": provenance,
                }
            )

            if reviewed:
                counts[f"{split}_approved_recommendations"] += 1

            review_queue.append(
                {
                    "policy_id": policy_id,
                    "appetite_id": appetite_id,
                    "split": split,
                    "review_status": "pending",
                    "policy": facts,
                    "appetite": appetite,
                    "deterministic_appetite_evaluation": deterministic_target,
                    "audit": audit,
                    "teacher_instruction": "Return only expected_disposition and expected_rules. Use only the supplied policy facts and appetite profile.",
                }
            )

    sampling_counts: dict[str, Any] = {}
    teacher_candidates: list[dict[str, Any]] = []
    unique_counts: dict[str, int] = {}
    selected_by_split: dict[str, list[dict[str, Any]]] = {}
    for split in canonical:
        _write_jsonl(output_dir / "canonical" / f"{split}.jsonl", canonical[split])
        unique_candidates = _dedupe_sft_candidates(sft_candidates[split])
        unique_counts[split] = len(unique_candidates)
        selected_by_split[split] = _select_sft_candidates(unique_candidates, split, synthetic_to_live_ratio)

    prompt_groups: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for split, candidates in selected_by_split.items():
        for candidate in candidates:
            prompt_groups.setdefault(_payload_fingerprint(candidate["payload"]), []).append((split, candidate))

    split_targets = _split_counts(len(prompt_groups))
    owned_candidates: dict[str, list[dict[str, Any]]] = {split: [] for split in selected_by_split}
    assigned_counts = {split: 0 for split in selected_by_split}
    shared_groups: list[tuple[str, list[tuple[str, dict[str, Any]]]]] = []
    for fingerprint, options in prompt_groups.items():
        if len(options) == 1:
            split, candidate = options[0]
            owned_candidates[split].append(candidate)
            assigned_counts[split] += 1
        else:
            targets = {json.dumps(candidate["target"], sort_keys=True) for _, candidate in options}
            if len(targets) != 1:
                raise ValueError(f"Cross-split prompt {fingerprint} has conflicting targets")
            shared_groups.append((fingerprint, options))

    for fingerprint, options in sorted(shared_groups, key=lambda item: (-len(item[1]), _hash(item[0]))):
        eligible = {split for split, _ in options}
        owner = min(
            eligible,
            key=lambda candidate: (
                sum(
                    ((assigned_counts[split] + (1 if split == candidate else 0) - split_targets[split]) ** 2)
                    / max(split_targets[split], 1)
                    for split in split_targets
                ),
                candidate,
            ),
        )
        representatives = [candidate for split, candidate in options if split == owner]
        representative = min(
            representatives,
            key=lambda candidate: (candidate["kind"] != "live", _hash(candidate["id"])),
        )
        owned_candidates[owner].append(representative)
        assigned_counts[owner] += 1

    for split in ("train", "validation", "test"):
        before_cross_split_filter = len(selected_by_split[split])
        selected_sft = sorted(owned_candidates[split], key=lambda candidate: _hash(candidate["id"]))
        _write_jsonl(
            output_dir / "baseten_sft" / f"{split}.jsonl",
            (_sft_record(candidate["payload"], candidate["target"]) for candidate in selected_sft),
        )
        if split != "test":
            teacher_candidates.extend(
                {
                    "candidate_id": candidate["id"],
                    "policy_id": candidate["policy_id"],
                    "appetite_id": candidate["appetite_id"],
                    "split": split,
                    "kind": candidate["kind"],
                    "input": candidate["payload"],
                    "deterministic_label": candidate["sampling_target"],
                }
                for candidate in selected_sft
            )
        sampling_counts[split] = {
            "available": len(sft_candidates[split]),
            "unique_available": unique_counts[split],
            "cross_split_duplicates_removed": before_cross_split_filter - len(selected_sft),
            "selected": len(selected_sft),
            "by_disposition": dict(Counter(candidate["target"]["expected_disposition"] for candidate in selected_sft)),
            "by_kind": dict(Counter(candidate["kind"] for candidate in selected_sft)),
        }
    _write_jsonl(output_dir / "teacher_candidates.jsonl", teacher_candidates)
    _write_jsonl(output_dir / "teacher_review_queue.jsonl", review_queue)
    matrix = {
        "schema_version": BENCHMARK_SCHEMA_VERSION,
        "run_mode": mode,
        "evaluation_contract": "Structured disposition, rule attribution, evidence grounding, missing-data detection, and conditions.",
        "evaluations": evaluations,
    }
    (output_dir / "evaluation-matrix.json").write_text(json.dumps(matrix, indent=2, sort_keys=True) + "\n")
    split_manifest = {
        "split_salt": split_salt,
        "policy_assignments": assignments,
        "policy_counts": dict(Counter(assignments.values())),
    }
    (output_dir / "split_manifest.json").write_text(json.dumps(split_manifest, indent=2, sort_keys=True) + "\n")
    (output_dir / "sft_sampling_manifest.json").write_text(
        json.dumps(
            {
                "synthetic_to_live_ratio": synthetic_to_live_ratio,
                "prompt_ownership": "exact model-visible prompts are assigned to one eligible split with 60/20/20 targets",
                "splits": sampling_counts,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    manifest = {
        "schema_version": SFT_SCHEMA_VERSION,
        "benchmark_schema_version": BENCHMARK_SCHEMA_VERSION,
        "mode": mode,
        "policies": len(selected),
        "appetites": len(appetites),
        "policies_sha256": hashlib.sha256(policies_path.read_bytes()).hexdigest(),
        "appetites_sha256": hashlib.sha256(appetites_path.read_bytes()).hexdigest(),
        "counts": dict(sorted(counts.items())),
        "sft_sampling": sampling_counts,
        "baseten_format": "TRL conversational JSONL: one object per line with a messages array",
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policies", type=Path, default=BENCHMARK_DIRECTORY / "policies.json")
    parser.add_argument("--appetites", type=Path, default=BENCHMARK_DIRECTORY / "appetites.json")
    parser.add_argument("--output", type=Path, default=Path("output"))
    parser.add_argument("--mode", choices=("demo", "matrix", "regression", "boundary"), default="regression")
    parser.add_argument("--split-salt", default="underwriting-benchmark-v1")
    parser.add_argument("--reviewed-recommendations", type=Path)
    parser.add_argument("--synthetic-to-live-ratio", type=int, default=3)
    args = parser.parse_args()
    if args.synthetic_to_live_ratio < 0:
        raise ValueError("synthetic-to-live-ratio cannot be negative")
    print(json.dumps(generate(args.policies, args.appetites, args.output, args.mode, args.split_salt, args.reviewed_recommendations, args.synthetic_to_live_ratio), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
