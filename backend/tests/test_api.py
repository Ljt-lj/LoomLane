from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from loomlane.main import create_app
from loomlane.models import SecretStatus
from loomlane.secrets import CredentialError
from loomlane.storage import ApiProfileStore, ProjectStore, default_sdl


class MemoryCredentials:
    def get(self, project_id: str, profile_id: str, base_url: str) -> str | None:
        return None

    def status(self, project_id: str, profile_id: str, base_url: str):
        return SecretStatus(
            profile_id=profile_id,
            configured=False,
            origin=base_url,
            scope="project",
        )

    def put(self, project_id: str, profile_id: str, api_key: str, base_url: str):
        return SecretStatus(
            profile_id=profile_id,
            configured=True,
            origin=base_url,
            scope="project",
        )

    def delete(self, project_id: str, profile_id: str) -> None:
        return None

    def get_library(self, profile_id: str, base_url: str) -> str | None:
        return None

    def status_library(self, profile_id: str, base_url: str):
        return SecretStatus(
            profile_id=profile_id,
            configured=False,
            origin=base_url,
            scope="library",
        )

    def put_library(self, profile_id: str, api_key: str, base_url: str):
        return SecretStatus(
            profile_id=profile_id,
            configured=True,
            origin=base_url,
            scope="library",
        )

    def delete_library(self, profile_id: str) -> None:
        return None

    def copy_to_library(
        self,
        project_id: str,
        profile_id: str,
        library_id: str,
        base_url: str,
    ):
        return SecretStatus(
            profile_id=library_id,
            configured=True,
            origin=base_url,
            scope="library",
        )


class UnavailableVaultCredentials(MemoryCredentials):
    def delete_library(self, profile_id: str) -> None:
        raise CredentialError("System credential vault is unavailable")


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


def test_api_profile_library_crud_and_secret_routes(tmp_path: Path) -> None:
    store = ProjectStore(tmp_path / "projects")
    profile_store = ApiProfileStore(tmp_path / "api-profiles.json")
    app = create_app(
        store=store,
        api_profiles=profile_store,
        credentials=MemoryCredentials(),
    )
    profile = {
        "id": "deepseek-flash",
        "name": "DeepSeek Flash",
        "provider": "openai_compatible",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-flash",
        "request": {
            "timeout_seconds": 60,
            "max_retries": 2,
            "temperature": 0.2,
        },
        "capabilities": {
            "function_calling": False,
            "vision": False,
            "long_context": True,
            "structured_output": True,
        },
    }

    with TestClient(app) as client:
        created = client.post("/api/api-profiles", json=profile)
        assert created.status_code == 201
        assert created.json()["id"] == "deepseek-flash"
        assert len(client.get("/api/api-profiles").json()) == 1

        updated = client.put(
            "/api/api-profiles/deepseek-flash",
            json={**profile, "model": "deepseek-v4-flash"},
        )
        assert updated.status_code == 200
        assert updated.json()["model"] == "deepseek-v4-flash"

        secret = client.put(
            "/api/api-profiles/deepseek-flash/secret",
            json={"api_key": "secret-value"},
        )
        assert secret.status_code == 200
        assert secret.json()["scope"] == "library"

        status = client.get(
            "/api/api-profiles/deepseek-flash/secret/status"
        )
        assert status.status_code == 200

        deleted = client.delete("/api/api-profiles/deepseek-flash")
        assert deleted.status_code == 204
        assert client.get("/api/api-profiles").json() == []

    assert profile_store.list() == []


def test_api_profile_delete_survives_vault_cleanup_failure(
    tmp_path: Path,
) -> None:
    profile_store = ApiProfileStore(tmp_path / "api-profiles.json")
    app = create_app(
        store=ProjectStore(tmp_path / "projects"),
        api_profiles=profile_store,
        credentials=UnavailableVaultCredentials(),
    )
    profile = {
        "id": "deepseek-flash",
        "name": "DeepSeek Flash",
        "provider": "openai_compatible",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-flash",
    }

    with TestClient(app) as client:
        assert client.post("/api/api-profiles", json=profile).status_code == 201
        assert client.delete("/api/api-profiles/deepseek-flash").status_code == 204
        assert client.get("/api/api-profiles").json() == []

    assert profile_store.list() == []
