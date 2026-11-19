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
    ApiProfile,
    CanvasEdge,
    CanvasLayout,
    CanvasNode,
    ProjectDetail,
    ProjectSummary,
    SDL,
)
from .settings import data_dir, projects_dir
from .validator import validate_sdl


class StorageError(RuntimeError):
    pass


class ProjectNotFound(StorageError):
    pass


class RevisionConflict(StorageError):
    pass


class ApiProfileNotFound(StorageError):
    pass


class ApiProfileConflict(StorageError):
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
            "sdl_version": "0.2",
            "workflow": {
                "id": "content-review",
                "name": "V0.2 内容生产综合流水线",
                "global_context": {
                    "text": (
                        "You are part of a content production system. "
                        "Use only the inputs provided in this run, keep claims "
                        "traceable to the research notes, and return strict JSON."
                    ),
                    "injection": "prefix",
                    "applies_to": "all",
                },
                "inputs": [
                    {
                        "name": "requirement",
                        "type": "TEXT",
                        "required": True,
                    },
                    {
                        "name": "audience",
                        "type": "TEXT",
                        "required": True,
                    },
                    {
                        "name": "tone",
                        "type": "TEXT",
                        "required": True,
                    },
                ],
                "outputs": [
                    {
                        "name": "final_article",
                        "agent": "publisher",
                        "port": "published",
                    },
                    {
                        "name": "release_manifest",
                        "agent": "publisher",
                        "port": "release_manifest",
                    }
                ],
                "execution": {
                    "feedback_limit_default": 2,
                    "loop_limit_default": 4,
                    "max_steps": 200,
                    "max_total_tokens": 120_000,
                    "max_parallel": 4,
                },
            },
            "start": ["brief"],
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
                    "id": "brief",
                    "name": "任务简报与双线研究计划",
                    "api_profile": "openai-main",
                    "system_prompt": (
                        "把用户需求转成可执行的内容简报、受众约束、语气约束和两条"
                        "互补研究路线。如果存在 feedback，先修正上一轮简报再输出。"
                    ),
                    "inputs": [
                        {"name": "requirement", "type": "TEXT", "required": True},
                        {"name": "audience", "type": "TEXT", "required": True},
                        {"name": "tone", "type": "TEXT", "required": True},
                        {"name": "feedback", "type": "TEXT", "required": False},
                    ],
                    "outputs": [
                        {
                            "name": "brief",
                            "type": "TEXT",
                        },
                        {
                            "name": "source_plan",
                            "type": "DATA",
                            "schema_hint": {"type": "object"},
                        },
                    ],
                    "limits": {"max_tokens": 4_096, "max_total_tokens": 40_960},
                    "json_output": {
                        "enabled": True,
                        "template": {
                            "brief": "面向执行团队的完整内容简报",
                            "source_plan": {
                                "research_a": "第一条研究路线的目标与问题",
                                "research_b": "第二条研究路线的目标与问题",
                                "success_criteria": ["可验收标准"],
                            },
                        },
                    },
                    "output_dir": "artifacts/01-brief",
                },
                {
                    "id": "research_a",
                    "name": "研究分支 A：事实与背景",
                    "api_profile": "openai-main",
                    "system_prompt": (
                        "沿研究分支 A 收集关键事实、背景、定义和可引用的证据。"
                        "不要提前撰写成稿。"
                    ),
                    "inputs": [
                        {"name": "brief", "type": "TEXT", "required": True},
                        {"name": "plan", "type": "DATA", "required": True},
                    ],
                    "outputs": [
                        {
                            "name": "notes",
                            "type": "TEXT",
                        },
                        {
                            "name": "evidence",
                            "type": "DATA",
                            "schema_hint": {"type": "object"},
                        },
                    ],
                    "limits": {"max_tokens": 4_096, "max_total_tokens": 24_576},
                    "json_output": {
                        "enabled": True,
                        "template": {
                            "notes": "关于分支 A 的结构化研究笔记",
                            "evidence": {
                                "facts": ["关键事实"],
                                "sources": ["来源或待核实说明"],
                                "risks": ["事实风险"],
                            },
                        },
                    },
                    "output_dir": "artifacts/02-research-a",
                },
                {
                    "id": "research_b",
                    "name": "研究分支 B：反例与受众洞察",
                    "api_profile": "openai-main",
                    "system_prompt": (
                        "沿研究分支 B 寻找反例、争议、受众疑问和实践建议。"
                        "不要提前撰写成稿。"
                    ),
                    "inputs": [
                        {"name": "brief", "type": "TEXT", "required": True},
                        {"name": "plan", "type": "DATA", "required": True},
                    ],
                    "outputs": [
                        {
                            "name": "notes",
                            "type": "TEXT",
                        },
                        {
                            "name": "evidence",
                            "type": "DATA",
                            "schema_hint": {"type": "object"},
                        },
                    ],
                    "limits": {"max_tokens": 4_096, "max_total_tokens": 24_576},
                    "json_output": {
                        "enabled": True,
                        "template": {
                            "notes": "关于分支 B 的结构化研究笔记",
                            "evidence": {
                                "counterpoints": ["反例或争议"],
                                "audience_questions": ["受众疑问"],
                                "practical_actions": ["可执行建议"],
                            },
                        },
                    },
                    "output_dir": "artifacts/03-research-b",
                },
                {
                    "id": "synthesizer",
                    "name": "双线研究聚合",
                    "api_profile": "openai-main",
                    "system_prompt": (
                        "合并两条研究分支，消解重复和冲突，形成一份可直接写稿的"
                        "大纲与综合研究结论。"
                    ),
                    "inputs": [
                        {"name": "notes_a", "type": "TEXT", "required": True},
                        {"name": "notes_b", "type": "TEXT", "required": True},
                        {"name": "evidence_a", "type": "DATA", "required": True},
                        {"name": "evidence_b", "type": "DATA", "required": True},
                    ],
                    "outputs": [
                        {"name": "outline", "type": "TEXT"},
                        {
                            "name": "synthesis",
                            "type": "DATA",
                            "schema_hint": {"type": "object"},
                        },
                    ],
                    "limits": {"max_tokens": 4_096, "max_total_tokens": 40_960},
                    "json_output": {
                        "enabled": True,
                        "template": {
                            "outline": "带章节目标和证据约束的 Markdown 大纲",
                            "synthesis": {
                                "claims": ["可写入正文的核心主张"],
                                "conflicts": ["尚有冲突或待核实的信息"],
                                "evidence_map": ["主张与证据的对应关系"],
                            },
                        },
                    },
                    "output_dir": "artifacts/04-synthesis",
                },
                {
                    "id": "editor",
                    "name": "初稿与循环修订",
                    "api_profile": "openai-main",
                    "system_prompt": (
                        "根据大纲和综合研究撰写完整文章。如果存在 feedback，"
                        "必须针对审查意见逐项修订，并记录本轮修改。"
                    ),
                    "inputs": [
                        {"name": "outline", "type": "TEXT", "required": True},
                        {"name": "synthesis", "type": "DATA", "required": True},
                        {"name": "feedback", "type": "TEXT", "required": False},
                    ],
                    "outputs": [
                        {"name": "draft", "type": "TEXT"},
                        {
                            "name": "edit_log",
                            "type": "DATA",
                            "schema_hint": {"type": "object"},
                        },
                    ],
                    "limits": {"max_tokens": 4_096, "max_total_tokens": 61_440},
                    "json_output": {
                        "enabled": True,
                        "template": {
                            "draft": "完整、可进入质量审查的 Markdown 文章",
                            "edit_log": {
                                "revision_goal": "本轮修订目标",
                                "changes": ["具体修改"],
                                "remaining_risks": ["仍需审查的风险"],
                            },
                        },
                    },
                    "output_dir": "artifacts/05-editor",
                },
                {
                    "id": "reviewer",
                    "name": "循环审查与结构反馈",
                    "api_profile": "openai-main",
                    "system_prompt": (
                        "审查文章的事实、结构、受众匹配和可发布性，给出 0 到 1 的"
                        "评分。article 必须原样返回收到的 draft，供后续报告使用。"
                    ),
                    "inputs": [
                        {"name": "draft", "type": "TEXT", "required": True}
                    ],
                    "outputs": [
                        {"name": "article", "type": "TEXT"},
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
                    "limits": {"max_tokens": 4_096, "max_total_tokens": 61_440},
                    "json_output": {
                        "enabled": True,
                        "template": {
                            "article": "原样返回待审查的 draft",
                            "review": "具体、可执行且按优先级排序的审查意见",
                            "score": "0 到 1 之间的数字",
                        },
                    },
                    "output_dir": "artifacts/06-review",
                },
                {
                    "id": "report",
                    "name": "定稿与质量报告",
                    "api_profile": "openai-main",
                    "system_prompt": (
                        "根据审查结果完成最终定稿，并输出可审计的质量数据。"
                        "如果评分未达到理想阈值，也要明确记录残余风险。"
                    ),
                    "inputs": [
                        {"name": "article", "type": "TEXT", "required": True},
                        {"name": "review", "type": "TEXT", "required": True},
                        {"name": "score", "type": "DATA", "required": True},
                    ],
                    "outputs": [
                        {"name": "article", "type": "TEXT"},
                        {
                            "name": "quality",
                            "type": "DATA",
                            "schema_hint": {"type": "object"},
                        },
                    ],
                    "limits": {"max_tokens": 4_096, "max_total_tokens": 40_960},
                    "json_output": {
                        "enabled": True,
                        "template": {
                            "article": "完成修订的最终 Markdown 文章",
                            "quality": {
                                "review_score": "本轮审查分数",
                                "resolved_findings": ["已解决问题"],
                                "residual_risks": ["残余风险"],
                                "release_recommendation": "发布建议",
                            },
                        },
                    },
                    "output_dir": "artifacts/07-report",
                },
                {
                    "id": "publisher",
                    "name": "人工批准后发布",
                    "api_profile": "openai-main",
                    "system_prompt": (
                        "仅在人工 Gate 批准后执行最终发布整理，输出发布稿和发布清单。"
                    ),
                    "inputs": [
                        {"name": "article", "type": "TEXT", "required": True},
                        {"name": "quality", "type": "DATA", "required": True},
                    ],
                    "outputs": [
                        {"name": "published", "type": "TEXT"},
                        {
                            "name": "release_manifest",
                            "type": "DATA",
                            "schema_hint": {"type": "object"},
                        },
                    ],
                    "limits": {"max_tokens": 4_096, "max_total_tokens": 24_576},
                    "json_output": {
                        "enabled": True,
                        "template": {
                            "published": "最终可发布文章正文",
                            "release_manifest": {
                                "title": "发布标题",
                                "summary": "发布摘要",
                                "quality_snapshot": "质量数据摘要",
                                "approved_for_release": True,
                            },
                        },
                    },
                    "output_dir": "artifacts/08-publisher",
                },
            ],
            "edges": [
                {
                    "id": "brief-to-research-a",
                    "from": "brief",
                    "to": "research_a",
                    "type": "FAN_OUT",
                    "mappings": [
                        {"from": "brief", "to": "brief"},
                        {"from": "source_plan", "to": "plan"},
                    ],
                },
                {
                    "id": "brief-to-research-b",
                    "from": "brief",
                    "to": "research_b",
                    "type": "FAN_OUT",
                    "mappings": [
                        {"from": "brief", "to": "brief"},
                        {"from": "source_plan", "to": "plan"},
                    ],
                },
                {
                    "id": "research-a-to-synthesis",
                    "from": "research_a",
                    "to": "synthesizer",
                    "type": "AGGREGATE",
                    "aggregate": {"mode": "all"},
                    "mappings": [
                        {"from": "notes", "to": "notes_a"},
                        {"from": "evidence", "to": "evidence_a"},
                    ],
                },
                {
                    "id": "research-b-to-synthesis",
                    "from": "research_b",
                    "to": "synthesizer",
                    "type": "AGGREGATE",
                    "aggregate": {"mode": "all"},
                    "mappings": [
                        {"from": "notes", "to": "notes_b"},
                        {"from": "evidence", "to": "evidence_b"},
                    ],
                },
                {
                    "id": "synthesis-to-editor",
                    "from": "synthesizer",
                    "to": "editor",
                    "type": "PIPE",
                    "mappings": [
                        {"from": "outline", "to": "outline"},
                        {"from": "synthesis", "to": "synthesis"},
                    ],
                },
                {
                    "id": "editor-to-review",
                    "from": "editor",
                    "to": "reviewer",
                    "type": "PIPE",
                    "mappings": [{"from": "draft", "to": "draft"}],
                },
                {
                    "id": "review-loop",
                    "from": "reviewer",
                    "to": "editor",
                    "type": "LOOP",
                    "mappings": [{"from": "review", "to": "feedback"}],
                    "condition": {
                        "op": "and",
                        "args": [
                            {
                                "op": "lt",
                                "left": "$.agents.reviewer.outputs.score",
                                "right": 0.65,
                            },
                            {
                                "op": "lt",
                                "left": "$.edges.review-loop.traversals",
                                "right": 3,
                            },
                        ],
                    },
                    "guard": {"max_traversals": 3},
                },
                {
                    "id": "review-feedback",
                    "from": "reviewer",
                    "to": "brief",
                    "type": "FEEDBACK",
                    "mappings": [{"from": "review", "to": "feedback"}],
                    "condition": {
                        "op": "and",
                        "args": [
                            {
                                "op": "gte",
                                "left": "$.agents.reviewer.outputs.score",
                                "right": 0.65,
                            },
                            {
                                "op": "lt",
                                "left": "$.agents.reviewer.outputs.score",
                                "right": 0.8,
                            },
                            {
                                "op": "lt",
                                "left": "$.edges.review-feedback.traversals",
                                "right": 1,
                            },
                        ],
                    },
                    "guard": {"max_traversals": 1},
                },
                {
                    "id": "review-to-report",
                    "from": "reviewer",
                    "to": "report",
                    "type": "CONDITIONAL",
                    "mappings": [
                        {"from": "article", "to": "article"},
                        {"from": "review", "to": "review"},
                        {"from": "score", "to": "score"},
                    ],
                    "condition": {
                        "op": "or",
                        "args": [
                            {
                                "op": "gte",
                                "left": "$.agents.reviewer.outputs.score",
                                "right": 0.8,
                            },
                            {
                                "op": "and",
                                "args": [
                                    {
                                        "op": "lt",
                                        "left": "$.agents.reviewer.outputs.score",
                                        "right": 0.65,
                                    },
                                    {
                                        "op": "gte",
                                        "left": "$.edges.review-loop.traversals",
                                        "right": 3,
                                    },
                                ],
                            },
                            {
                                "op": "and",
                                "args": [
                                    {
                                        "op": "gte",
                                        "left": "$.agents.reviewer.outputs.score",
                                        "right": 0.65,
                                    },
                                    {
                                        "op": "lt",
                                        "left": "$.agents.reviewer.outputs.score",
                                        "right": 0.8,
                                    },
                                    {
                                        "op": "gte",
                                        "left": "$.edges.review-feedback.traversals",
                                        "right": 1,
                                    },
                                ],
                            },
                        ],
                    },
                },
                {
                    "id": "publish-review",
                    "from": "report",
                    "to": "publisher",
                    "type": "HUMAN_GATE",
                    "mappings": [
                        {"from": "article", "to": "article"},
                        {"from": "quality", "to": "quality"},
                    ],
                    "human_gate": {
                        "prompt": "请检查最终文章与质量报告，批准后生成发布稿。",
                        "timeout_seconds": 900,
                        "on_reject": "fail",
                        "on_timeout": "fail",
                    },
                },
            ],
        }
    )


def default_canvas() -> CanvasLayout:
    return CanvasLayout(
        viewport={"x": 20, "y": 0, "zoom": 0.7},
        nodes=[
            CanvasNode(id="brief", x=40, y=320, width=250, height=132),
            CanvasNode(id="research_a", x=350, y=130, width=250, height=132),
            CanvasNode(id="research_b", x=350, y=510, width=250, height=132),
            CanvasNode(id="synthesizer", x=660, y=320, width=250, height=132),
            CanvasNode(id="editor", x=970, y=320, width=250, height=132),
            CanvasNode(id="reviewer", x=1280, y=320, width=250, height=132),
            CanvasNode(id="report", x=1590, y=320, width=250, height=132),
            CanvasNode(id="publisher", x=1900, y=320, width=250, height=132),
        ],
        edges=[
            CanvasEdge(id="brief-to-research-a"),
            CanvasEdge(id="brief-to-research-b"),
            CanvasEdge(id="research-a-to-synthesis"),
            CanvasEdge(id="research-b-to-synthesis"),
            CanvasEdge(id="synthesis-to-editor"),
            CanvasEdge(id="editor-to-review"),
            CanvasEdge(id="review-loop"),
            CanvasEdge(id="review-feedback"),
            CanvasEdge(id="review-to-report"),
            CanvasEdge(id="publish-review"),
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


class ApiProfileStore:
    """Persists reusable API profile metadata outside project SDL files."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = (path or data_dir() / "api-profiles.json").resolve()
        self._lock = threading.RLock()

    def _read_profiles(self) -> list[ApiProfile]:
        if not self.path.exists():
            return []
        try:
            data = _read_json(self.path)
            raw_profiles = data.get("profiles", []) if isinstance(data, dict) else data
            if not isinstance(raw_profiles, list):
                raise ValueError("profiles must be a list")
            return [
                ApiProfile.model_validate(item).model_copy(update={"library_id": None})
                for item in raw_profiles
            ]
        except (OSError, TypeError, ValueError, ValidationError) as exc:
            raise StorageError("API Profile library could not be read") from exc

    def _write_profiles(self, profiles: list[ApiProfile]) -> None:
        try:
            _atomic_write_json(
                self.path,
                {
                    "version": 1,
                    "profiles": [
                        profile.model_dump(
                            mode="json",
                            by_alias=True,
                            exclude_none=True,
                        )
                        for profile in profiles
                    ],
                },
            )
        except OSError as exc:
            raise StorageError("API Profile library could not be written") from exc

    def list(self) -> list[ApiProfile]:
        with self._lock:
            return self._read_profiles()

    def get(self, profile_id: str) -> ApiProfile:
        with self._lock:
            profile = next(
                (
                    item
                    for item in self._read_profiles()
                    if item.id == profile_id
                ),
                None,
            )
            if profile is None:
                raise ApiProfileNotFound(profile_id)
            return profile

    def create(self, profile: ApiProfile) -> ApiProfile:
        with self._lock:
            profiles = self._read_profiles()
            if any(item.id == profile.id for item in profiles):
                raise ApiProfileConflict(profile.id)
            normalized = profile.model_copy(update={"library_id": None})
            self._write_profiles([*profiles, normalized])
            return normalized

    def update(self, profile_id: str, profile: ApiProfile) -> ApiProfile:
        with self._lock:
            profiles = self._read_profiles()
            index = next(
                (
                    item_index
                    for item_index, item in enumerate(profiles)
                    if item.id == profile_id
                ),
                None,
            )
            if index is None:
                raise ApiProfileNotFound(profile_id)
            if profile.id != profile_id:
                raise ApiProfileConflict(profile.id)
            normalized = profile.model_copy(update={"library_id": None})
            profiles[index] = normalized
            self._write_profiles(profiles)
            return normalized

    def delete(self, profile_id: str) -> None:
        with self._lock:
            profiles = self._read_profiles()
            remaining = [item for item in profiles if item.id != profile_id]
            if len(remaining) == len(profiles):
                raise ApiProfileNotFound(profile_id)
            self._write_profiles(remaining)


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
            marker = self.root / ".seed-v02-demo"
            if marker.exists():
                return
            demo_name = "V0.2 内容生产综合演示"
            demo_exists = False
            for path in self.root.iterdir():
                if not path.is_dir() or not (path / "project.json").exists():
                    continue
                try:
                    metadata = _read_json(path / "project.json")
                except (OSError, ValueError):
                    continue
                if metadata.get("name") == demo_name:
                    demo_exists = True
                    break
            if not demo_exists:
                self.create(demo_name, default_sdl(), default_canvas())
            _atomic_write_text(marker, "LoomLane V0.2 demo seeded.\n")

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

