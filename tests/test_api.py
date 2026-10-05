from pathlib import Path

from fastapi.testclient import TestClient

from nas_air_intelligence.api import create_app


def test_health_endpoint(tmp_path: Path):
    client = TestClient(create_app(str(tmp_path / "api.db")))
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
