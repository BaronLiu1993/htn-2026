"""Adjudicate deterministic underwriting labels with OpenAI's Batch API."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
import urllib.error
import urllib.request
import uuid
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


DEFAULT_MODEL = "gpt-5.6-sol"
DEFAULT_WORK_DIR = Path("training_data/output/openai_teacher")
DISPOSITIONS = ("accept", "refer", "decline", "insufficient_information")
RULE_CATEGORIES = (
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
)
RULE_ORDER = {rule: index for index, rule in enumerate(RULE_CATEGORIES)}

TEACHER_SYSTEM_PROMPT = """You are the independent label adjudicator for an appetite-driven commercial property underwriting benchmark.

Apply only the appetite object in the request to the supplied policy facts. Do not use general underwriting knowledge, outside facts, historical-carrier assumptions, pricing judgment, or unstated rules. Do not infer or repair missing, null, malformed, ambiguous, or contradictory values.

Use this exact precedence:
1. DECLINE if any supplied fact evidences a hard appetite failure. Return every evidenced hard-failure category, even if other required facts are missing.
2. Otherwise, INSUFFICIENT_INFORMATION if a fact required to evaluate an applicable appetite rule is absent, null, invalid, ambiguous, or contradictory. Return every missing required category. Use the appetite's on_missing_required_data value.
3. Otherwise, REFER only when one or more explicit referral rules are triggered. Return every triggered referral category.
4. Otherwise, ACCEPT. Return every applicable hard-rule category whose supplied facts satisfy the appetite, excluding untriggered referral-only rules.

Threshold semantics are literal. A min or max is inclusive. building_year_exclusive_min requires min_building_year to be strictly greater than the threshold. A state-list restriction fails if any policy state is outside the list; eligible_states equal to US permits all US states. Hazards trigger only on exact normalized names in the appetite lists. A multistate rule applies only when states contains more than one distinct state.

Map appetite rules to these output categories only: business_types -> business_type; eligible_states -> state; premium -> premium; total_tiv_max -> total_tiv; building_year_exclusive_min and refer_if_building_year_at_or_below -> building_age; loss_value_5yr_max -> loss_history; location_count_max -> location_count; open_or_litigated_claims_allowed -> claims_status; prohibited_hazards and refer_hazards -> hazard_exposure; refer_if_multistate -> multistate_exposure; require_primary_risk_location_for_multistate -> primary_risk_location.

Return one JSON object with exactly expected_disposition and expected_rules. Do not include explanations or chain-of-thought. expected_rules must contain no duplicates."""

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "expected_disposition": {"type": "string", "enum": list(DISPOSITIONS)},
        "expected_rules": {
            "type": "array",
            "items": {"type": "string", "enum": list(RULE_CATEGORIES)},
        },
    },
    "required": ["expected_disposition", "expected_rules"],
    "additionalProperties": False,
}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text().splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"{path}:{line_number} is not valid JSON: {error.msg}") from error
        if not isinstance(record, dict):
            raise ValueError(f"{path}:{line_number} must contain a JSON object")
        records.append(record)
    return records


def _write_jsonl(path: Path, records: Iterable[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w") as handle:
        for record in records:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
            count += 1
    return count


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def _custom_id(index: int, candidate: dict[str, Any]) -> str:
    digest = hashlib.sha256(
        json.dumps(candidate["input"], sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]
    return f"uw-{index:05d}-{digest}"


def _normalize_label(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"expected_disposition", "expected_rules"}:
        raise ValueError("Label must contain exactly expected_disposition and expected_rules")
    disposition = value["expected_disposition"]
    rules = value["expected_rules"]
    if disposition not in DISPOSITIONS:
        raise ValueError(f"Unsupported disposition: {disposition}")
    if not isinstance(rules, list) or not all(rule in RULE_ORDER for rule in rules):
        raise ValueError("expected_rules contains an unsupported rule category")
    normalized_rules = sorted(set(rules), key=RULE_ORDER.__getitem__)
    return {"expected_disposition": disposition, "expected_rules": normalized_rules}


def prepare(
    candidates_path: Path,
    work_dir: Path,
    model: str,
    reasoning_effort: str,
    chunk_size: int,
) -> dict[str, Any]:
    candidates = _read_jsonl(candidates_path)
    requests: list[dict[str, Any]] = []
    manifest: list[dict[str, Any]] = []
    for index, candidate in enumerate(candidates, start=1):
        required = {"policy_id", "appetite_id", "split", "input", "deterministic_label"}
        if not required.issubset(candidate):
            raise ValueError(f"Teacher candidate {index} is missing: {sorted(required - set(candidate))}")
        if candidate["split"] not in {"train", "validation"}:
            raise ValueError("Teacher candidates may only contain train and validation rows")
        deterministic_label = _normalize_label(candidate["deterministic_label"])
        custom_id = _custom_id(index, candidate)
        requests.append(
            {
                "custom_id": custom_id,
                "method": "POST",
                "url": "/v1/responses",
                "body": {
                    "model": model,
                    "input": [
                        {"role": "system", "content": TEACHER_SYSTEM_PROMPT},
                        {
                            "role": "user",
                            "content": json.dumps(candidate["input"], sort_keys=True),
                        },
                    ],
                    "reasoning": {"effort": reasoning_effort},
                    "text": {
                        "format": {
                            "type": "json_schema",
                            "name": "underwriting_label",
                            "strict": True,
                            "schema": OUTPUT_SCHEMA,
                        }
                    },
                    "max_output_tokens": 2048,
                    "store": False,
                },
            }
        )
        manifest.append(
            {
                "custom_id": custom_id,
                "policy_id": candidate["policy_id"],
                "appetite_id": candidate["appetite_id"],
                "split": candidate["split"],
                "kind": candidate.get("kind"),
                "deterministic_label": deterministic_label,
            }
        )

    request_path = work_dir / "batch_requests.jsonl"
    manifest_path = work_dir / "batch_manifest.jsonl"
    _write_jsonl(request_path, requests)
    _write_jsonl(manifest_path, manifest)
    chunks = 0
    for start in range(0, len(requests), chunk_size):
        chunks += 1
        _write_jsonl(
            work_dir / f"batch_requests-{chunks:03d}.jsonl",
            requests[start : start + chunk_size],
        )
    summary = {
        "model": model,
        "reasoning_effort": reasoning_effort,
        "requests": len(requests),
        "chunk_size": chunk_size,
        "chunks": chunks,
        "request_file": str(request_path),
        "manifest_file": str(manifest_path),
        "splits": dict(Counter(item["split"] for item in manifest)),
    }
    _write_json(work_dir / "prepare_summary.json", summary)
    return summary


def _api_request(
    path: str,
    method: str = "GET",
    json_body: dict[str, Any] | None = None,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> bytes:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY must be set in the process environment")
    request_headers = {"Authorization": f"Bearer {api_key}"}
    if headers:
        request_headers.update(headers)
    if json_body is not None:
        body = json.dumps(json_body).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        f"https://api.openai.com/v1{path}",
        data=body,
        headers=request_headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:1000]
        raise RuntimeError(f"OpenAI API returned {error.code}: {detail}") from error


def submit(work_dir: Path, chunk: int) -> dict[str, Any]:
    suffix = f"-{chunk:03d}"
    request_path = work_dir / f"batch_requests{suffix}.jsonl"
    if not request_path.exists():
        raise FileNotFoundError(f"Prepare the batch first: {request_path}")
    boundary = f"----underwriting-{uuid.uuid4().hex}"
    file_bytes = request_path.read_bytes()
    multipart = b"".join(
        (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"purpose\"\r\n\r\nbatch\r\n".encode(),
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{request_path.name}\"\r\nContent-Type: application/jsonl\r\n\r\n".encode(),
            file_bytes,
            f"\r\n--{boundary}--\r\n".encode(),
        )
    )
    upload = json.loads(
        _api_request(
            "/files",
            method="POST",
            body=multipart,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
    )
    file_id = upload["id"]
    batch = json.loads(
        _api_request(
            "/batches",
            method="POST",
            json_body={
                "input_file_id": file_id,
                "endpoint": "/v1/responses",
                "completion_window": "24h",
                "metadata": {"description": "underwriting label adjudication"},
            },
        )
    )
    state = {
        "batch_id": batch["id"],
        "input_file_id": file_id,
        "status": batch["status"],
        "created_at": batch.get("created_at"),
    }
    state["chunk"] = chunk
    _write_json(work_dir / f"batch_state{suffix}.json", state)
    return state


def _download_file(file_id: str, path: Path) -> None:
    path.write_bytes(_api_request(f"/files/{file_id}/content"))


def wait_for_batch(work_dir: Path, poll_interval: int, chunk: int) -> dict[str, Any]:
    suffix = f"-{chunk:03d}"
    state_path = work_dir / f"batch_state{suffix}.json"
    state = json.loads(state_path.read_text())
    batch_id = state["batch_id"]
    terminal = {"completed", "failed", "expired", "cancelled"}
    while True:
        batch = json.loads(_api_request(f"/batches/{batch_id}"))
        state = {
            "batch_id": batch_id,
            "input_file_id": batch.get("input_file_id"),
            "output_file_id": batch.get("output_file_id"),
            "error_file_id": batch.get("error_file_id"),
            "status": batch["status"],
            "request_counts": batch.get("request_counts"),
            "created_at": batch.get("created_at"),
            "completed_at": batch.get("completed_at"),
            "errors": batch.get("errors"),
            "chunk": chunk,
        }
        _write_json(state_path, state)
        print(json.dumps({"status": state["status"], "request_counts": state["request_counts"]}), flush=True)
        if state["status"] in terminal:
            break
        time.sleep(poll_interval)

    if state.get("output_file_id"):
        _download_file(state["output_file_id"], work_dir / f"batch_output{suffix}.jsonl")
    if state.get("error_file_id"):
        _download_file(state["error_file_id"], work_dir / f"batch_errors{suffix}.jsonl")
    if state["status"] != "completed":
        raise RuntimeError(f"Batch ended with status {state['status']}")
    return state


def _response_text(body: dict[str, Any]) -> str:
    for output in body.get("output", []):
        if output.get("type") != "message":
            continue
        for content in output.get("content", []):
            if content.get("type") == "output_text" and isinstance(content.get("text"), str):
                return content["text"]
            if content.get("type") == "refusal":
                raise ValueError(f"Teacher refused the request: {content.get('refusal', '')}")
    raise ValueError("Response did not contain output_text")


def import_results(work_dir: Path, corrections_path: Path) -> dict[str, Any]:
    manifest_records = _read_jsonl(work_dir / "batch_manifest.jsonl")
    manifest = {record["custom_id"]: record for record in manifest_records}
    prepare_summary = json.loads((work_dir / "prepare_summary.json").read_text())
    output_paths = [
        work_dir / f"batch_output-{chunk:03d}.jsonl"
        for chunk in range(1, int(prepare_summary["chunks"]) + 1)
    ]
    missing_outputs = [path for path in output_paths if not path.exists()]
    if missing_outputs:
        raise FileNotFoundError(f"Missing completed chunk outputs: {', '.join(map(str, missing_outputs))}")
    results = [record for path in output_paths for record in _read_jsonl(path)]
    corrections: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    seen: set[str] = set()
    teacher_labels: dict[str, dict[str, Any]] = {}
    dispositions: Counter[str] = Counter()
    input_tokens = output_tokens = 0

    for result in results:
        custom_id = result.get("custom_id")
        if custom_id not in manifest:
            errors.append({"custom_id": custom_id, "error": "Unknown custom_id"})
            continue
        seen.add(custom_id)
        response = result.get("response") or {}
        if response.get("status_code") != 200 or result.get("error"):
            errors.append({"custom_id": custom_id, "error": result.get("error") or response})
            continue
        body = response.get("body") or {}
        usage = body.get("usage") or {}
        input_tokens += int(usage.get("input_tokens", 0))
        output_tokens += int(usage.get("output_tokens", 0))
        try:
            teacher_label = _normalize_label(json.loads(_response_text(body)))
        except (ValueError, json.JSONDecodeError) as error:
            errors.append({"custom_id": custom_id, "error": str(error)})
            continue
        dispositions[teacher_label["expected_disposition"]] += 1
        teacher_labels[custom_id] = teacher_label

    for custom_id in sorted(set(manifest) - seen):
        errors.append({"custom_id": custom_id, "error": "No batch result returned"})

    prompt_groups: dict[str, list[str]] = {}
    for custom_id in teacher_labels:
        prompt_groups.setdefault(custom_id.rsplit("-", 1)[-1], []).append(custom_id)
    inconsistent_prompt_groups = 0
    tied_prompt_groups = 0
    resolved_labels: dict[str, dict[str, Any]] = {}
    for custom_ids in prompt_groups.values():
        label_counts = Counter(json.dumps(teacher_labels[custom_id], sort_keys=True) for custom_id in custom_ids)
        if len(label_counts) > 1:
            inconsistent_prompt_groups += 1
        top_count = max(label_counts.values())
        winners = sorted(label for label, count in label_counts.items() if count == top_count)
        if len(winners) == 1:
            resolved = json.loads(winners[0])
        else:
            tied_prompt_groups += 1
            deterministic_labels = {
                json.dumps(_normalize_label(manifest[custom_id]["deterministic_label"]), sort_keys=True)
                for custom_id in custom_ids
            }
            if len(deterministic_labels) != 1:
                errors.append({"custom_ids": custom_ids, "error": "Prompt group has a teacher tie and inconsistent deterministic labels"})
                continue
            resolved = json.loads(next(iter(deterministic_labels)))
        for custom_id in custom_ids:
            resolved_labels[custom_id] = resolved

    for custom_id, teacher_label in resolved_labels.items():
        item = manifest[custom_id]
        deterministic_label = _normalize_label(item["deterministic_label"])
        if teacher_label == deterministic_label:
            continue
        corrections.append(
            {
                "policy_id": item["policy_id"],
                "appetite_id": item["appetite_id"],
                "prompt_fingerprint": custom_id.rsplit("-", 1)[-1],
                "split": item["split"],
                "review_status": "approved",
                "recommendation": teacher_label,
                "deterministic_recommendation": deterministic_label,
                "label_provenance": "gpt-5.6-sol batch disagreement adjudication",
            }
        )
    corrections.sort(key=lambda item: (item["split"], item["policy_id"], item["appetite_id"]))
    _write_jsonl(corrections_path, corrections)
    _write_jsonl(work_dir / "import_errors.jsonl", errors)
    summary = {
        "candidates": len(manifest),
        "successful_labels": len(manifest) - len(errors),
        "agreements": len(manifest) - len(errors) - len(corrections),
        "disagreements": len(corrections),
        "errors": len(errors),
        "teacher_dispositions": dict(dispositions),
        "inconsistent_duplicate_prompt_groups": inconsistent_prompt_groups,
        "tied_duplicate_prompt_groups": tied_prompt_groups,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
        "corrections_file": str(corrections_path),
    }
    _write_json(work_dir / "import_summary.json", summary)
    if errors:
        raise RuntimeError(f"Teacher import has {len(errors)} errors; inspect {work_dir / 'import_errors.jsonl'}")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare_parser = subparsers.add_parser("prepare")
    prepare_parser.add_argument("--candidates", type=Path, default=Path("training_data/output/teacher_candidates.jsonl"))
    prepare_parser.add_argument("--work-dir", type=Path, default=DEFAULT_WORK_DIR)
    prepare_parser.add_argument("--model", default=DEFAULT_MODEL)
    prepare_parser.add_argument("--reasoning-effort", choices=("none", "low", "medium", "high", "xhigh", "max"), default="high")
    prepare_parser.add_argument("--chunk-size", type=int, default=475)

    submit_parser = subparsers.add_parser("submit")
    submit_parser.add_argument("--work-dir", type=Path, default=DEFAULT_WORK_DIR)
    submit_parser.add_argument("--chunk", type=int, required=True)

    wait_parser = subparsers.add_parser("wait")
    wait_parser.add_argument("--work-dir", type=Path, default=DEFAULT_WORK_DIR)
    wait_parser.add_argument("--poll-interval", type=int, default=30)
    wait_parser.add_argument("--chunk", type=int, required=True)

    import_parser = subparsers.add_parser("import")
    import_parser.add_argument("--work-dir", type=Path, default=DEFAULT_WORK_DIR)
    import_parser.add_argument("--corrections", type=Path, default=Path("training_data/output/teacher_corrections.jsonl"))

    args = parser.parse_args()
    if args.command == "prepare":
        if args.chunk_size <= 0:
            raise ValueError("chunk-size must be positive")
        result = prepare(args.candidates, args.work_dir, args.model, args.reasoning_effort, args.chunk_size)
    elif args.command == "submit":
        result = submit(args.work_dir, args.chunk)
    elif args.command == "wait":
        result = wait_for_batch(args.work_dir, args.poll_interval, args.chunk)
    else:
        result = import_results(args.work_dir, args.corrections)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
