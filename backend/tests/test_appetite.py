import json
from pathlib import Path

from backend.app.appetite_loader import DEFAULT_APPETITE_PATH, load_appetite
from backend.app.demo_data import DEMO_SUBMISSIONS
from backend.app.evaluator import evaluate_submission


def test_versioned_appetite_file_is_valid():
    appetite = load_appetite()

    assert appetite.id == "commercial-property-2025"
    assert len(appetite.requirements) == 8
    assert len(appetite.preferences) == 4


def test_rule_change_requires_no_evaluator_code_change(tmp_path: Path):
    payload = json.loads(DEFAULT_APPETITE_PATH.read_text())
    payload["version"] = "2025.2"
    payload["requirements"][3]["value"] = 125_000_000
    changed = tmp_path / "changed-appetite.json"
    changed.write_text(json.dumps(payload))

    appetite = load_appetite(changed)

    assert appetite.version == "2025.2"
    assert appetite.requirements[3].value == 125_000_000


def test_changed_threshold_changes_decision_without_code_change(tmp_path: Path):
    payload = json.loads(DEFAULT_APPETITE_PATH.read_text())
    payload["version"] = "2025.2"
    payload["requirements"][3]["value"] = 70_000_000
    changed = tmp_path / "changed-appetite.json"
    changed.write_text(json.dumps(payload))

    result = evaluate_submission(
        DEMO_SUBMISSIONS[0],
        "run_changed",
        appetite=load_appetite(changed),
    )

    assert result.status == "out_of_appetite"
    assert result.appetite_version == "2025.2"
    assert "R4" in {rule.rule_id for rule in result.failed_requirements}
