from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from loomlane.models import EdgeType, SDL
from loomlane.storage import ProjectStore


DEMO_NAME = "V0.2 内容生产综合演示"


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
