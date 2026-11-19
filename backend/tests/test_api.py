from __future__ import annotations

import errno
from pathlib import Path

from fastapi.testclient import TestClient

from loomlane import storage as storage_module
from loomlane.main import create_app
from loomlane.models import EdgeType, Port, PortType, SecretStatus, WorkflowInput
from loomlane.runtime import RunManager
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


def test_project_save_returns_structured_error_for_persistent_file_lock(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store = ProjectStore(tmp_path / "projects")
    app = create_app(store=store, credentials=MemoryCredentials())

    with TestClient(app) as client:
        summary = client.get("/api/projects").json()[0]
        detail = client.get(f"/api/projects/{summary['id']}").json()

        def locked_replace(_: str | Path, destination: str | Path) -> None:
            raise PermissionError(
                errno.EACCES,
                "file is locked",
                destination,
            )

        monkeypatch.setattr(storage_module.os, "replace", locked_replace)
        monkeypatch.setattr(storage_module.time, "sleep", lambda _: None)
        response = client.put(
            f"/api/projects/{summary['id']}",
            json={
                "sdl": detail["sdl"],
                "canvas": detail["canvas"],
                "base_revision": detail["revision"],
            },
        )

        assert response.status_code == 503
        payload = response.json()
        assert payload["code"] == "project_storage_write_failed"
        assert "retry" in payload["detail"].lower()
        assert client.get(f"/api/projects/{summary['id']}").json()["revision"] == 1


def test_get_project_with_v0_2_workflow_schema(tmp_path: Path) -> None:
    store = ProjectStore(tmp_path / "projects")
    sdl = default_sdl()
    sdl.sdl_version = "0.2"
    sdl.workflow.execution.loop_limit_default = 4
    sdl.workflow.execution.max_parallel = 2
    feedback = next(edge for edge in sdl.edges if edge.id == "review-feedback")
    feedback.type = EdgeType.LOOP
    feedback.guard.max_traversals = None

    project = store.create("v0.2 project", sdl)
    app = create_app(store=store, credentials=MemoryCredentials())

    with TestClient(app) as client:
        response = client.get(f"/api/projects/{project.id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["sdl"]["sdl_version"] == "0.2"
    assert payload["sdl"]["workflow"]["execution"]["max_parallel"] == 2
    loop = next(
        edge
        for edge in payload["sdl"]["edges"]
        if edge["id"] == "review-feedback"
    )
    assert loop["guard"]["max_traversals"] == 4


def test_cors_allows_local_development_ports(tmp_path: Path) -> None:
    store = ProjectStore(tmp_path / "projects")
    app = create_app(store=store, credentials=MemoryCredentials())

    with TestClient(app) as client:
        for origin in (
            "http://127.0.0.1:5174",
            "http://localhost:4173",
        ):
            response = client.options(
                "/api/projects",
                headers={
                    "Origin": origin,
                    "Access-Control-Request-Method": "GET",
                },
            )
            assert response.status_code == 200
            assert response.headers["access-control-allow-origin"] == origin

        rejected = client.options(
            "/api/projects",
            headers={
                "Origin": "https://example.com",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert rejected.status_code == 400


def test_file_input_upload_and_open_folder_routes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store = ProjectStore(tmp_path / "projects")
    sdl = default_sdl()
    sdl.workflow.inputs.append(
        WorkflowInput(name="source", type=PortType.FILE, required=True)
    )
    sdl.agents[0].inputs.append(
        Port(name="source", type=PortType.FILE, required=True)
    )
    project = store.create("file input api", sdl)

    async def fake_open_output_folder(self, run_id: str) -> str:
        assert run_id == "a" * 32
        return str(tmp_path / "output")

    monkeypatch.setattr(
        RunManager,
        "open_output_folder",
        fake_open_output_folder,
    )
    app = create_app(store=store, credentials=MemoryCredentials())

    with TestClient(app) as client:
        uploaded = client.post(
            f"/api/projects/{project.id}/input-files",
            params={"input_name": "source", "file_name": "notes.txt"},
            content=b"reference material",
            headers={"Content-Type": "application/octet-stream"},
        )
        assert uploaded.status_code == 201
        payload = uploaded.json()
        assert payload["name"] == "notes.txt"
        assert payload["size"] == len(b"reference material")
        assert (
            store.project_path(project.id) / payload["path"]
        ).read_bytes() == b"reference material"

        invalid = client.post(
            f"/api/projects/{project.id}/input-files",
            params={
                "input_name": "requirement",
                "file_name": "wrong.txt",
            },
            content=b"not a file port",
        )
        assert invalid.status_code == 400

        opened = client.post(f"/api/runs/{'a' * 32}/open-folder")
        assert opened.status_code == 200
        assert opened.json() == {
            "path": str(tmp_path / "output"),
            "opened": True,
        }


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
