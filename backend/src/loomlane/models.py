from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class PortType(StrEnum):
    TEXT = "TEXT"
    DATA = "DATA"
    FILE = "FILE"
    MIXED = "MIXED"


class EdgeType(StrEnum):
    PIPE = "PIPE"
    CONDITIONAL = "CONDITIONAL"
    FEEDBACK = "FEEDBACK"


class GlobalContext(StrictModel):
    text: str
    injection: Literal["prefix", "suffix"] = "prefix"
    applies_to: str | list[str] = "all"


class WorkflowInput(StrictModel):
    name: str
    type: PortType
    required: bool = True
    schema_hint: dict[str, Any] | None = None


class WorkflowOutput(StrictModel):
    name: str
    agent: str
    port: str


class ExecutionConfig(StrictModel):
    feedback_limit_default: int = Field(default=3, ge=1, le=100)
    max_steps: int = Field(default=200, ge=1, le=10_000)


class WorkflowMeta(StrictModel):
    id: str
    name: str
    global_context: GlobalContext | None = None
    inputs: list[WorkflowInput] = Field(default_factory=list)
    outputs: list[WorkflowOutput] = Field(default_factory=list)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)


class RequestConfig(StrictModel):
    timeout_seconds: float = Field(default=60, gt=0, le=600)
    max_retries: int = Field(default=2, ge=0, le=8)
    temperature: float = Field(default=0.2, ge=0, le=2)


class CapabilityFlags(StrictModel):
    function_calling: bool = False
    vision: bool = False
    long_context: bool = False
    structured_output: bool = False


class ApiProfile(StrictModel):
    id: str
    name: str | None = None
    provider: Literal["openai_compatible"] = "openai_compatible"
    base_url: str
    model: str
    request: RequestConfig = Field(default_factory=RequestConfig)
    capabilities: CapabilityFlags = Field(default_factory=CapabilityFlags)
    note: str | None = None

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("base_url must use http:// or https://")
        return value.rstrip("/")


class Port(StrictModel):
    name: str
    type: PortType
    required: bool = True
    schema_hint: dict[str, Any] | None = None


class AgentLimits(StrictModel):
    max_tokens: int | None = Field(default=None, ge=1)


class AgentJsonOutput(StrictModel):
    enabled: bool = False
    template: dict[str, Any] = Field(default_factory=dict)


class Agent(StrictModel):
    id: str
    name: str
    api_profile: str
    system_prompt: str
    inputs: list[Port] = Field(default_factory=list)
    outputs: list[Port] = Field(default_factory=list)
    limits: AgentLimits = Field(default_factory=AgentLimits)
    json_output: AgentJsonOutput = Field(default_factory=AgentJsonOutput)
    output_dir: str | None = None


class Condition(StrictModel):
    op: Literal[
        "and",
        "or",
        "not",
        "eq",
        "ne",
        "lt",
        "lte",
        "gt",
        "gte",
        "in",
        "contains",
        "exists",
    ]
    args: list[Condition] = Field(default_factory=list)
    left: str | None = None
    right: Any = None


class EdgeMapping(StrictModel):
    from_: str = Field(alias="from")
    to: str


class EdgeGuard(StrictModel):
    max_traversals: int | None = Field(default=None, ge=1, le=100)


class Edge(StrictModel):
    id: str
    from_: str = Field(alias="from")
    to: str
    type: EdgeType
    mappings: list[EdgeMapping] = Field(default_factory=list)
    condition: Condition | None = None
    guard: EdgeGuard = Field(default_factory=EdgeGuard)


class SDL(StrictModel):
    sdl_version: Literal["0.1"] = "0.1"
    workflow: WorkflowMeta
    start: list[str] = Field(default_factory=list)
    api_profiles: list[ApiProfile] = Field(default_factory=list)
    agents: list[Agent] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)

    def canonical_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json", by_alias=True, exclude_none=True)


class CanvasPoint(StrictModel):
    x: float
    y: float


class CanvasNode(StrictModel):
    id: str
    x: float
    y: float
    width: float = 220
    height: float = 112


class CanvasEdge(StrictModel):
    id: str
    bend_points: list[CanvasPoint] = Field(default_factory=list)


class CanvasLayout(StrictModel):
    canvas_version: Literal["0.1"] = "0.1"
    viewport: dict[str, float] = Field(
        default_factory=lambda: {"x": 0, "y": 0, "zoom": 1}
    )
    nodes: list[CanvasNode] = Field(default_factory=list)
    edges: list[CanvasEdge] = Field(default_factory=list)


class ValidationIssue(StrictModel):
    code: str
    message: str
    path: str = ""
    severity: Literal["error", "warning"] = "error"
    node_id: str | None = None
    edge_id: str | None = None


class ValidationResponse(StrictModel):
    valid: bool
    issues: list[ValidationIssue] = Field(default_factory=list)
    normalized_sdl: dict[str, Any] | None = None


class ProjectCreateRequest(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    sdl: SDL | None = None
    canvas: CanvasLayout | None = None


class ProjectUpdateRequest(StrictModel):
    sdl: SDL
    canvas: CanvasLayout
    base_revision: int | None = None


class ImportSDLRequest(StrictModel):
    text: str = Field(min_length=1)


class ValidateWorkflowRequest(StrictModel):
    sdl: SDL


class ProjectSummary(StrictModel):
    id: str
    name: str
    revision: int
    created_at: datetime
    updated_at: datetime
    last_build_hash: str | None = None


class ProjectDetail(ProjectSummary):
    sdl: SDL
    canvas: CanvasLayout


class GenerateResponse(StrictModel):
    compiler_key: str
    generated_path: str
    cached: bool
    files: list[str]


class SecretStatus(StrictModel):
    profile_id: str
    configured: bool
    origin: str | None = None


class SecretPutRequest(StrictModel):
    api_key: str = Field(min_length=1)


class RunStartRequest(StrictModel):
    inputs: dict[str, Any] = Field(default_factory=dict)


class RunSummary(StrictModel):
    run_id: str
    project_id: str
    status: Literal[
        "queued", "running", "succeeded", "failed", "cancelled"
    ] = "queued"
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None


class RunDetail(RunSummary):
    result: dict[str, Any] | None = None
    events: list[dict[str, Any]] = Field(default_factory=list)

