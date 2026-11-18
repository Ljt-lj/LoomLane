from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from loomlane.main import create_app
from loomlane.storage import ProjectStore, default_sdl


class MemoryCredentials:
    def get(self, project_id: str, profile_id: str, base_url: str) -> str | None:
        return None

    def status(self, project_id: str, profile_id: str, base_url: str):
        from loomlane.models import SecretStatus

        return SecretStatus(profile_id=profile_id, configured=False, origin=base_url)

    def put(self, project_id: str, profile_id: str, api_key: str, base_url: str):
        from loomlane.models import SecretStatus

        return SecretStatus(profile_id=profile_id, configured=True, origin=base_url)

    def delete(self, project_id: str, profile_id: str) -> None:
        return None


def test_project_api_round_trip(tmp_path: Path) -> None:
    store = ProjectStore(tmp_path / "projects")
    app = create_app(store=store, credentials=MemoryCredentials())

    with TestClient(app) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"

        projects = client.get("/api/projects").json()
        assert len(projects) == 1
        project = client.get(f"/api/projects/{projects[0]['id']}").json()
        assert project["sdl"]["workflow"]["id"] == "content-review"

        validation = client.post(
            f"/api/projects/{project['id']}/validate",
            json={"sdl": default_sdl().model_dump(mode="json", by_alias=True)},
        )
        assert validation.status_code == 200
        assert validation.json()["valid"] is True

        generated = client.post(f"/api/projects/{project['id']}/generate")
        assert generated.status_code == 200
        assert generated.json()["compiler_key"]


def test_revision_conflict_returns_409(tmp_path: Path) -> None:
    store = ProjectStore(tmp_path / "projects")
    app = create_app(store=store, credentials=MemoryCredentials())

    with TestClient(app) as client:
        project = client.get("/api/projects").json()[0]
        detail = client.get(f"/api/projects/{project['id']}").json()
        response = client.put(
            f"/api/projects/{project['id']}",
            json={
                "sdl": detail["sdl"],
                "canvas": detail["canvas"],
                "base_revision": detail["revision"] - 1,
            },
        )

    assert response.status_code == 409
