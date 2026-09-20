from __future__ import annotations

from pathlib import Path

import pytest

from backend.run_benchmark import ModelConfig, ModelPrediction, load_json, model_config_from_env, run


ROOT = Path(__file__).resolve().parents[2]
BENCHMARK = ROOT / "backend" / "benchmark"


def test_benchmark_runner_scores_all_matrix_cases() -> None:
    def predict(_policy: dict, _appetite: dict) -> ModelPrediction:
        return ModelPrediction("accept", [], [])

    report = run(
        load_json(BENCHMARK / "policies.json"),
        load_json(BENCHMARK / "appetites.json"),
        load_json(BENCHMARK / "evaluation-matrix.json"),
        ModelConfig("openai", "test-model", "test-key", "https://example.test/v1", "medium"),
        predict,
    )

    assert report["case_count"] == 40
    assert report["scores"]["disposition_accuracy"] < 1.0
    assert all(item["actual_disposition"] == "accept" for item in report["results"])


def test_benchmark_runner_accepts_baseten_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BENCHMARK_PROVIDER", "baseten")
    monkeypatch.setenv("BASETEN_API_KEY", "test-key")
    monkeypatch.setenv("BASETEN_MODEL_URL", "https://example.test/v1/")

    config = model_config_from_env()

    assert config.provider == "baseten"
    assert config.model == "baseten-model"
    assert config.base_url == "https://example.test/v1"


def test_baseten_endpoint_requires_both_environment_variables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BASETEN_API_KEY", "test-key")
    monkeypatch.delenv("BASETEN_MODEL_URL", raising=False)
    monkeypatch.setenv("BENCHMARK_PROVIDER", "baseten")

    with pytest.raises(ValueError, match="must be set together"):
        model_config_from_env()
