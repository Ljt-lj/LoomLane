from __future__ import annotations

import errno
from collections.abc import Callable
from pathlib import Path

import pytest

from loomlane import storage as storage_module
from loomlane.models import EdgeType, SDL
from loomlane.storage import ProjectStore, StorageWriteError, _atomic_write_text


DEMO_NAME = "V0.2 内容生产综合演示"


def test_atomic_write_text_retries_transient_file_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "project.json"
    real_replace = storage_module.os.replace
    attempts = 0

    def flaky_replace(source: str | Path, destination: str | Path) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise PermissionError(
                errno.EACCES,
                "file is temporarily locked",
                destination,
            )
        real_replace(source, destination)

    monkeypatch.setattr(storage_module.os, "replace", flaky_replace)
    monkeypatch.setattr(storage_module.time, "sleep", lambda _: None)

    _atomic_write_text(target, '{"revision": 2}\n')

    assert target.read_text(encoding="utf-8") == '{"revision": 2}\n'
    assert attempts == 2


def test_project_save_maps_persistent_file_lock_to_storage_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ProjectStore(tmp_path / "projects")
    project = store.create("locked project")

    def locked_replace(_: str | Path, destination: str | Path) -> None:
        raise PermissionError(
            errno.EACCES,
            "file is locked",
            destination,
        )

    monkeypatch.setattr(storage_module.os, "replace", locked_replace)
    monkeypatch.setattr(storage_module.time, "sleep", lambda _: None)

    with pytest.raises(StorageWriteError) as error:
        store.save(project.id, project.sdl, project.canvas, project.revision)

    assert error.value.path.name == "workflow.sdl.yaml"
    assert error.value.errno == errno.EACCES
    assert store.get(project.id).revision == project.revision


def test_ensure_seed_creates_complex_demo_once(tmp_path: Path) -> None:
    store = ProjectStore(tmp_path / "projects")

    store.ensure_seed()
    projects = store.list()

    assert len(projects) == 1
    assert projects[0].name == DEMO_NAME
    demo = store.get(projects[0].id)
    assert demo.sdl.sdl_version == "0.2"
    assert len(demo.sdl.agents) == 8
    assert len(demo.sdl.edges) == 10
    assert {edge.type for edge in demo.sdl.edges} == set(EdgeType)
    assert (store.root / ".seed-v02-demo").is_file()

    store.ensure_seed()

    assert [project.id for project in store.list()] == [projects[0].id]


def test_ensure_seed_preserves_existing_projects(
    tmp_path: Path,
    legacy_workflow: Callable[[str], SDL],
) -> None:
    store = ProjectStore(tmp_path / "projects")
    existing = store.create("既有项目", legacy_workflow())

    store.ensure_seed()

    projects = store.list()
    assert {project.id for project in projects} == {
        existing.id,
        next(project.id for project in projects if project.name == DEMO_NAME),
    }


def test_ensure_seed_does_not_duplicate_matching_project(
    tmp_path: Path,
    legacy_workflow: Callable[[str], SDL],
) -> None:
    store = ProjectStore(tmp_path / "projects")
    existing = store.create(DEMO_NAME, legacy_workflow())

    store.ensure_seed()

    projects = store.list()
    assert [project.id for project in projects] == [existing.id]
    assert projects[0].name == DEMO_NAME
    assert (store.root / ".seed-v02-demo").is_file()
