from __future__ import annotations

import os
import shutil
import tempfile
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml
from pydantic import ValidationError

from .models import (
    CanvasEdge,
    CanvasLayout,
    CanvasNode,
    ProjectDetail,
    ProjectSummary,
    SDL,
)
from .settings import projects_dir
from .validator import validate_sdl


class StorageError(RuntimeError):
    pass


class ProjectNotFound(StorageError):
    pass


class RevisionConflict(StorageError):
    pass


class InvalidWorkflow(StorageError):
    def __init__(self, issues: list[dict[str, Any]]) -> None:
        super().__init__("Workflow validation failed")
        self.issues = issues


def utc_now() -> datetime:
    return datetime.now(UTC)


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def _atomic_write_json(path: Path, data: Any) -> None:
    import json

    _atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def _read_json(path: Path) -> Any:
    import json

    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def default_sdl() -> SDL:
    return SDL.model_validate(
        {
            "sdl_version": "0.1",
            "workflow": {
                "id": "content-review",
                "name": "内容审查流水线",
                "global_context": {
                    "text": "You are part of a content production system. Be concise and concrete.",
                    "injection": "prefix",
                    "applies_to": "all",
                },
                "inputs": [
                    {
                        "name": "requirement",
                        "type": "TEXT",
                        "required": True,
                    }
                ],
                "outputs": [
                    {
                        "name": "approved_draft",
                        "agent": "drafter",
                        "port": "draft",
                    }
                ],
                "execution": {
                    "feedback_limit_default": 3,
                    "max_steps": 200,
                },
            },
            "start": ["drafter"],
            "api_profiles": [
                {
                    "id": "openai-main",
                    "name": "OpenAI Compatible",
                    "provider": "openai_compatible",
                    "base_url": "https://api.openai.com/v1",
                    "model": "gpt-4o-mini",
                    "request": {
                        "timeout_seconds": 60,
                        "max_retries": 2,
                        "temperature": 0.2,
                    },
                    "capabilities": {
                        "function_calling": False,
                        "vision": False,
                        "long_context": False,
                        "structured_output": True,
                    },
                }
            ],
            "agents": [
                {
                    "id": "drafter",
                    "name": "内容起草",
                    "api_profile": "openai-main",
                    "system_prompt": (
                        "根据需求撰写初稿。如果存在 feedback，必须吸收反馈后重写。"
                        "只返回约定 JSON。"
                    ),
                    "inputs": [
                        {"name": "requirement", "type": "TEXT", "required": True},
                        {"name": "feedback", "type": "TEXT", "required": False},
                    ],
                    "outputs": [
                        {"name": "draft", "type": "TEXT"},
                        {
                            "name": "outline",
                            "type": "DATA",
                            "schema_hint": {"type": "object"},
                        },
                    ],
                    "limits": {"max_tokens": None},
                    "json_output": {
                        "enabled": True,
                        "template": {
                            "draft": "完整的 Markdown 草稿文本",
                            "outline": {
                                "sections": "章节标题字符串数组",
                            },
                        },
                    },
                    "output_dir": None,
                },
                {
                    "id": "reviewer",
                    "name": "质量审查",
                    "api_profile": "openai-main",
                    "system_prompt": "审查初稿，指出问题并给出 0 到 1 的评分。只返回约定 JSON。",
                    "inputs": [
                        {"name": "draft", "type": "TEXT", "required": True}
                    ],
                    "outputs": [
                        {"name": "review", "type": "TEXT"},
                        {
                            "name": "score",
                            "type": "DATA",
                            "schema_hint": {
                                "type": "number",
                                "minimum": 0,
                                "maximum": 1,
                            },
                        },
                    ],
                    "limits": {"max_tokens": None},
                    "json_output": {
                        "enabled": True,
                        "template": {
                            "review": "具体、可执行的审查意见",
                            "score": "0 到 1 之间的数字",
                        },
                    },
                    "output_dir": None,
                },
            ],
            "edges": [
                {
                    "id": "draft-to-review",
                    "from": "drafter",
                    "to": "reviewer",
                    "type": "PIPE",
                    "mappings": [{"from": "draft", "to": "draft"}],
                },
                {
                    "id": "review-feedback",
                    "from": "reviewer",
                    "to": "drafter",
                    "type": "FEEDBACK",
                    "mappings": [{"from": "review", "to": "feedback"}],
                    "condition": {
                        "op": "lt",
                        "left": "$.agents.reviewer.outputs.score",
                        "right": 0.8,
                    },
                    "guard": {"max_traversals": 3},
                },
            ],
        }
    )


def default_canvas() -> CanvasLayout:
    return CanvasLayout(
        viewport={"x": 0, "y": 0, "zoom": 1},
        nodes=[
            CanvasNode(id="drafter", x=80, y=180, width=240, height=120),
            CanvasNode(id="reviewer", x=440, y=180, width=240, height=120),
        ],
        edges=[
            CanvasEdge(id="draft-to-review"),
            CanvasEdge(id="review-feedback"),
        ],
    )


def _auto_layout(sdl: SDL) -> CanvasLayout:
    nodes: list[CanvasNode] = []
    for index, agent in enumerate(sdl.agents):
        column = index % 3
        row = index // 3
        nodes.append(
            CanvasNode(
                id=agent.id,
                x=80 + column * 360,
                y=140 + row * 220,
                width=240,
                height=120,
            )
        )
    return CanvasLayout(
        nodes=nodes,
        edges=[CanvasEdge(id=edge.id) for edge in sdl.edges],
    )


def normalize_canvas(sdl: SDL, canvas: CanvasLayout | None) -> CanvasLayout:
    if canvas is None:
        return _auto_layout(sdl)
    node_positions = {node.id: node for node in canvas.nodes}
    edge_positions = {edge.id: edge for edge in canvas.edges}
    normalized_nodes = [
        node_positions.get(agent.id)
        or CanvasNode(
            id=agent.id,
            x=80 + (index % 3) * 360,
            y=140 + (index // 3) * 220,
            width=240,
            height=120,
        )
        for index, agent in enumerate(sdl.agents)
    ]
    return CanvasLayout(
        viewport=canvas.viewport,
        nodes=normalized_nodes,
        edges=[
            edge_positions.get(edge.id) or CanvasEdge(id=edge.id) for edge in sdl.edges
        ],
    )


def parse_sdl_yaml(text: str) -> SDL:
    if len(text.encode("utf-8")) > 1_000_000:
        raise InvalidWorkflow(
            [
                {
                    "code": "sdl_too_large",
                    "message": "SDL must be smaller than 1 MB",
                    "path": "",
                    "severity": "error",
                }
            ]
        )
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise InvalidWorkflow(
            [
                {
                    "code": "invalid_yaml",
                    "message": str(exc),
                    "path": "",
                    "severity": "error",
                }
            ]
        ) from exc
    if not isinstance(data, dict):
        raise InvalidWorkflow(
            [
                {
                    "code": "invalid_sdl_root",
                    "message": "SDL root must be an object",
                    "path": "",
                    "severity": "error",
                }
            ]
        )
    try:
        return SDL.model_validate(data)
    except ValidationError as exc:
        issues = []
        for error in exc.errors():
            issues.append(
                {
                    "code": "schema_error",
                    "message": error["msg"],
                    "path": ".".join(str(part) for part in error["loc"]),
                    "severity": "error",
                }
            )
        raise InvalidWorkflow(issues) from exc


def sdl_to_yaml(sdl: SDL) -> str:
    return yaml.safe_dump(
        sdl.canonical_dict(),
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    )


class ProjectStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or projects_dir()).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def _project_dir(self, project_id: str) -> Path:
        if "/" in project_id or "\\" in project_id or ".." in project_id:
            raise ProjectNotFound(project_id)
        path = (self.root / project_id).resolve()
        if path.parent != self.root:
            raise ProjectNotFound(project_id)
        return path

    def project_path(self, project_id: str) -> Path:
        path = self._project_dir(project_id)
        if not path.exists():
            raise ProjectNotFound(project_id)
        return path

    def ensure_seed(self) -> None:
        with self._lock:
            if not list(self.root.iterdir()):
                self.create("内容审查示例", default_sdl(), default_canvas())

    def list(self) -> list[ProjectSummary]:
        projects: list[ProjectSummary] = []
        for path in sorted(self.root.iterdir(), reverse=True):
            if not path.is_dir() or not (path / "project.json").exists():
                continue
            try:
                projects.append(ProjectSummary.model_validate(_read_json(path / "project.json")))
            except (OSError, ValueError, ValidationError):
                continue
        return sorted(projects, key=lambda project: project.updated_at, reverse=True)

    def create(
        self,
        name: str,
        sdl: SDL | None = None,
        canvas: CanvasLayout | None = None,
    ) -> ProjectDetail:
        with self._lock:
            workflow = sdl or default_sdl()
            validation = validate_sdl(workflow)
            if not validation.valid:
                raise InvalidWorkflow(
                    [issue.model_dump(mode="json") for issue in validation.issues]
                )
            normalized = SDL.model_validate(validation.normalized_sdl)
            project_id = uuid4().hex
            now = utc_now()
            detail = ProjectDetail(
                id=project_id,
                name=name,
                revision=1,
                created_at=now,
                updated_at=now,
                last_build_hash=None,
                sdl=normalized,
                canvas=normalize_canvas(normalized, canvas or _auto_layout(normalized)),
            )
            project_path = self._project_dir(project_id)
            project_path.mkdir(parents=True, exist_ok=False)
            self._write_project(detail)
            return detail

    def get(self, project_id: str) -> ProjectDetail:
        project_path = self._project_dir(project_id)
        if not project_path.exists():
            raise ProjectNotFound(project_id)
        meta = _read_json(project_path / "project.json")
        with (project_path / "workflow.sdl.yaml").open("r", encoding="utf-8") as handle:
            sdl = parse_sdl_yaml(handle.read())
        canvas = CanvasLayout.model_validate(_read_json(project_path / "canvas.json"))
        return ProjectDetail(
            **meta,
            sdl=sdl,
            canvas=normalize_canvas(sdl, canvas),
        )

    def save(
        self,
        project_id: str,
        sdl: SDL,
        canvas: CanvasLayout,
        base_revision: int | None,
    ) -> ProjectDetail:
        with self._lock:
            current = self.get(project_id)
            if base_revision is not None and current.revision != base_revision:
                raise RevisionConflict(
                    f"Expected revision {base_revision}, current is {current.revision}"
                )
            validation = validate_sdl(sdl)
            if not validation.valid:
                raise InvalidWorkflow(
                    [issue.model_dump(mode="json") for issue in validation.issues]
                )
            normalized = SDL.model_validate(validation.normalized_sdl)
            detail = ProjectDetail(
                id=current.id,
                name=current.name,
                revision=current.revision + 1,
                created_at=current.created_at,
                updated_at=utc_now(),
                last_build_hash=current.last_build_hash,
                sdl=normalized,
                canvas=normalize_canvas(normalized, canvas),
            )
            self._write_project(detail)
            return detail

    def set_last_build(self, project_id: str, compiler_key: str) -> ProjectDetail:
        with self._lock:
            current = self.get(project_id)
            detail = ProjectDetail(
                id=current.id,
                name=current.name,
                revision=current.revision,
                created_at=current.created_at,
                updated_at=utc_now(),
                last_build_hash=compiler_key,
                sdl=current.sdl,
                canvas=current.canvas,
            )
            self._write_project(detail)
            return detail

    def read_sdl_text(self, project_id: str) -> str:
        return (self._project_dir(project_id) / "workflow.sdl.yaml").read_text(
            encoding="utf-8"
        )

    def import_sdl(
        self,
        project_id: str,
        text: str,
        base_revision: int | None,
    ) -> ProjectDetail:
        sdl = parse_sdl_yaml(text)
        current = self.get(project_id)
        return self.save(project_id, sdl, current.canvas, base_revision)

    def delete(self, project_id: str) -> None:
        with self._lock:
            path = self.project_path(project_id)
            shutil.rmtree(path)

    def _write_project(self, detail: ProjectDetail) -> None:
        project_path = self._project_dir(detail.id)
        summary = {
            "id": detail.id,
            "name": detail.name,
            "revision": detail.revision,
            "created_at": detail.created_at.isoformat(),
            "updated_at": detail.updated_at.isoformat(),
            "last_build_hash": detail.last_build_hash,
        }
        _atomic_write_json(project_path / "project.json", summary)
        _atomic_write_text(project_path / "workflow.sdl.yaml", sdl_to_yaml(detail.sdl))
        _atomic_write_json(
            project_path / "canvas.json",
            detail.canvas.model_dump(mode="json"),
        )

