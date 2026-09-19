from fastapi.testclient import TestClient

from backend.main import app


client = TestClient(app)


def test_health_reports_demo_without_credentials():
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["mode"] in {"demo", "live"}


def test_demo_batch_analysis_returns_ranked_traceable_results():
    response = client.post("/api/analysis/batch", json={})

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "completed"
    assert len(payload["assessments"]) == 12
    assert payload["assessments"][0]["status"] == "target"
    assert payload["appetite_version"] == "2025.1"
    assert payload["agent_mode"] in {"openai", "deterministic_fallback"}
    assert payload["assessments"][0]["target_matches"] == 4
    assert any(event["tool"] == "discover_schema" for event in payload["trace"])
    assert any(event["tool"] == "load_appetite" for event in payload["trace"])
    assert all(item["run_id"] == payload["run_id"] for item in payload["assessments"])


def test_appetite_status_is_versioned():
    response = client.get("/api/appetite/status")

    assert response.status_code == 200
    assert response.json()["version"] == "2025.1"
    assert response.json()["requirement_count"] == 8
