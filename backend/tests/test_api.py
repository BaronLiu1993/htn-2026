from fastapi.testclient import TestClient

from backend.main import app


client = TestClient(app)


def test_health_reports_demo_without_credentials():
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["mode"] in {"demo", "live"}


def test_batch_analysis_requires_openai_configuration():
    response = client.post("/api/analysis/batch", json={})

    assert response.status_code == 502
    detail = response.json()["detail"]
    assert any("OpenAI is required" in error for error in detail["errors"])
    assert detail["run_id"].startswith("run_")


def test_appetite_status_is_versioned():
    response = client.get("/api/appetite/status")

    assert response.status_code == 200
    assert response.json()["version"] == "2025.1"
    assert response.json()["requirement_count"] == 8
