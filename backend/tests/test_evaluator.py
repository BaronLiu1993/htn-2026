from datetime import date

from backend.app.demo_data import DEMO_SUBMISSIONS
from backend.app.evaluator import evaluate_submission, rank_assessments
from backend.app.models import BuildingEvidence, SubmissionEvidence


def _base(**overrides):
    values = {
        "id": "test",
        "submission_number": "SUB-TEST",
        "insured_name": "Test Account",
        "received_date": date(2026, 9, 1),
        "submission_type": "new_business",
        "line_of_business": "property",
        "primary_state": "OH",
        "tiv": 75_000_000,
        "premium": 90_000,
        "buildings": [
            BuildingEvidence(
                id="building", year_built=2018, construction_type="non-combustible steel", tiv=75_000_000
            )
        ],
        "claims": [],
    }
    values.update(overrides)
    return SubmissionEvidence(**values)


def test_clear_target_matches_all_preferences():
    result = evaluate_submission(_base(), "run_test", as_of=date(2026, 9, 19))

    assert result.status == "target"
    assert result.target_matches == 4
    assert result.target_preferences_total == 4
    assert result.appetite_version == "2025.1"
    assert result.evidence_completeness == 1.0
    assert not result.failed_requirements


def test_hard_failure_beats_preference_points():
    result = evaluate_submission(
        _base(submission_type="renewal"), "run_test", as_of=date(2026, 9, 19)
    )

    assert result.status == "out_of_appetite"
    assert result.target_matches == 4
    assert [item.rule_id for item in result.failed_requirements] == ["R1"]


def test_exact_1990_is_needs_review():
    result = evaluate_submission(
        _base(
            buildings=[
                BuildingEvidence(
                    id="building",
                    year_built=1990,
                    construction_type="joisted masonry",
                    tiv=75_000_000,
                )
            ]
        ),
        "run_test",
        as_of=date(2026, 9, 19),
    )

    assert result.status == "needs_review"
    assert "R6" in {item.rule_id for item in result.unresolved_rules}


def test_ranking_is_status_then_score_then_completeness():
    assessments = [
        evaluate_submission(item, "run_test", as_of=date(2026, 9, 19))
        for item in DEMO_SUBMISSIONS
    ]
    ranked = rank_assessments(assessments)

    assert ranked[0].status == "target"
    assert ranked[-1].status == "out_of_appetite"
    assert [item.target_matches for item in ranked if item.status == "acceptable"] == [
        1,
        0,
        0,
    ]


def test_cope_is_informational_without_invented_rules():
    result = evaluate_submission(_base(), "run_test", as_of=date(2026, 9, 19))

    categories = {item.category: item for item in result.cope}
    assert categories["construction"].appetite_rule_applied is True
    assert categories["occupancy"].appetite_rule_applied is False
    assert categories["protection"].appetite_rule_applied is False


def test_ranking_handles_more_than_fifty_submissions():
    submissions = [
        DEMO_SUBMISSIONS[index % len(DEMO_SUBMISSIONS)].model_copy(
            update={
                "id": f"load-{index:03d}",
                "submission_number": f"SUB-LOAD-{index:03d}",
            }
        )
        for index in range(60)
    ]

    ranked = rank_assessments(
        [
            evaluate_submission(item, "run_load", as_of=date(2026, 9, 19))
            for item in submissions
        ]
    )

    assert len(ranked) == 60
    assert ranked[0].status == "target"
    assert ranked[-1].status == "out_of_appetite"
