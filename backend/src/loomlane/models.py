from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


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
    LOOP = "LOOP"
    FAN_OUT = "FAN_OUT"
    AGGREGATE = "AGGREGATE"
    HUMAN_GATE = "HUMAN_GATE"


class AggregateMode(StrEnum):
    ALL = "all"
    ANY = "any"


class AggregateConfig(StrictModel):
    mode: AggregateMode = AggregateMode.ALL


class HumanGateConfig(StrictModel):
    prompt: str = Field(
        default="Approve this workflow step before it continues.",
        min_length=1,
        max_length=2_000,
    )
    timeout_seconds: int = Field(default=300, ge=1, le=86_400)
    on_reject: Literal["fail", "skip"] = "fail"
    on_timeout: Literal["fail", "approve", "reject"] = "fail"


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
    loop_limit_default: int = Field(default=10, ge=1, le=100)
    max_steps: int = Field(default=200, ge=1, le=10_000)
    max_total_tokens: int | None = Field(default=None, ge=1)
    max_parallel: int = Field(default=4, ge=1, le=64)


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


class ImageGenerationConfig(StrictModel):
    protocol: Literal[
        "openai_images",
        "siliconflow",
        "automatic1111",
    ] = "automatic1111"
    size: str = Field(default="1024x1024", min_length=1, max_length=32)
    quality: str | None = Field(default=None, min_length=1, max_length=32)
    output_format: Literal["png", "jpeg", "webp"] | None = None
    response_format: Literal["url", "b64_json"] | None = None

    @field_validator("size")
    @classmethod
    def normalize_size(cls, value: str) -> str:
        return re.sub(r"\s*[x\u00d7]\s*", "x", value.strip(), flags=re.IGNORECASE)


class CapabilityFlags(StrictModel):
    function_calling: bool = False
    vision: bool = False
    long_context: bool = False
    structured_output: bool = False
    image_generation: bool = False


class ApiProfile(StrictModel):
    id: str
    library_id: str | None = None
    name: str | None = None
    provider: Literal["openai_compatible"] = "openai_compatible"
    base_url: str
    model: str
    request: RequestConfig = Field(default_factory=RequestConfig)
    capabilities: CapabilityFlags = Field(default_factory=CapabilityFlags)
    image_generation: ImageGenerationConfig | None = None
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
    max_total_tokens: int | None = Field(default=None, ge=1)


class AgentJsonOutput(StrictModel):
    enabled: bool = False
    template: dict[str, Any] = Field(default_factory=dict)


class AgentRulePhase(StrEnum):
    INPUT = "input"
    OUTPUT = "output"


class AgentRule(StrictModel):
    phase: AgentRulePhase
    expression: str = Field(min_length=1, max_length=2_000)
    message: str | None = Field(default=None, max_length=500)
    enabled: bool = True

    @field_validator("expression")
    @classmethod
    def validate_expression_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("rule expression cannot be blank")
        return normalized


class TransformMode(StrEnum):
    FIELDS = "fields"
    EXPRESSION = "expression"


class TransformField(StrictModel):
    path: str = Field(min_length=1, max_length=600)
    value: str = Field(min_length=1, max_length=1_000)

    @field_validator("path")
    @classmethod
    def validate_path_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("transform field path cannot be blank")
        return normalized

    @field_validator("value")
    @classmethod
    def validate_value_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("transform field value cannot be blank")
        return normalized


class TransformExpression(StrictModel):
    mode: TransformMode | None = None
    fields: list[TransformField] = Field(default_factory=list, max_length=32)
    expression: str | None = Field(default=None, max_length=2_000)

    @model_validator(mode="after")
    def validate_config_shape(self) -> TransformExpression:
        if self.mode is None:
            if self.expression is not None and self.fields:
                raise ValueError(
                    "transform cannot define both fields and expression"
                )
            self.mode = (
                TransformMode.EXPRESSION
                if self.expression is not None
                else TransformMode.FIELDS
            )
        if self.mode == TransformMode.FIELDS:
            if not self.fields:
                raise ValueError(
                    "fields transform mode requires at least one field"
                )
            if self.expression is not None:
                raise ValueError(
                    "fields transform mode cannot define expression"
                )
        else:
            if self.expression is None:
                raise ValueError(
                    "expression transform mode requires expression"
                )
            if self.fields:
                raise ValueError(
                    "expression transform mode cannot define fields"
                )
        return self

    @field_validator("expression")
    @classmethod
    def validate_expression_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("transform expression cannot be blank")
        return normalized

    def compiled_expression(self) -> str:
        if self.mode == TransformMode.EXPRESSION:
            assert self.expression is not None
            return self.expression

        from .transform_config import build_transform_expression

        return build_transform_expression(self.fields)

    def runtime_dict(self) -> dict[str, str]:
        return {"expression": self.compiled_expression()}


class Agent(StrictModel):
    id: str
    name: str
    api_profile: str
    mode: Literal["chat", "image"] = "chat"
    system_prompt: str
    inputs: list[Port] = Field(default_factory=list)
    outputs: list[Port] = Field(default_factory=list)
    limits: AgentLimits = Field(default_factory=AgentLimits)
    json_output: AgentJsonOutput = Field(default_factory=AgentJsonOutput)
    rules: list[AgentRule] = Field(default_factory=list)
    input_transform: TransformExpression | None = None
    output_transform: TransformExpression | None = None
    output_dir: str | None = None

    @field_validator("output_dir")
    @classmethod
    def validate_output_dir(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().replace("\\", "/")
        if not normalized or normalized.startswith("/"):
            raise ValueError("output_dir must be a relative path")
        windows_path = PureWindowsPath(value)
        if windows_path.is_absolute() or windows_path.drive:
            raise ValueError("output_dir must be a relative path")
        if ".." in PurePosixPath(normalized).parts:
            raise ValueError("output_dir cannot escape the run workspace")
        return normalized.rstrip("/")


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
        "between",
        "starts_with",
        "ends_with",
        "matches",
        "empty",
        "not_empty",
        "length_eq",
        "contains_all",
        "contains_any",
    ]
    args: list[Condition] = Field(default_factory=list)
    left: str | None = None
    right: Any = None


class EdgeMapping(StrictModel):
    from_: str = Field(alias="from")
    to: str
    transform: TransformExpression | None = None


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
    aggregate: AggregateConfig | None = None
    human_gate: HumanGateConfig | None = None


class SDL(StrictModel):
    sdl_version: Literal["0.1", "0.2", "0.3"] = "0.3"
    workflow: WorkflowMeta
    start: list[str] = Field(default_factory=list)
    api_profiles: list[ApiProfile] = Field(default_factory=list)
    agents: list[Agent] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)

    def canonical_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json", by_alias=True, exclude_none=True)

    def runtime_dict(self) -> dict[str, Any]:
        data = self.canonical_dict()
        for index, agent in enumerate(self.agents):
            for attribute in ("input_transform", "output_transform"):
                transform = getattr(agent, attribute)
                if transform is not None:
                    data["agents"][index][attribute] = transform.runtime_dict()
        for index, edge in enumerate(self.edges):
            for mapping_index, mapping in enumerate(edge.mappings):
                if mapping.transform is not None:
                    data["edges"][index]["mappings"][mapping_index][
                        "transform"
                    ] = mapping.transform.runtime_dict()
        return data


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
    scope: Literal["project", "library"] | None = None


class SecretPutRequest(StrictModel):
    api_key: str = Field(min_length=1)


class SecretImportRequest(StrictModel):
    project_id: str
    profile_id: str


class RunStartRequest(StrictModel):
    inputs: dict[str, Any] = Field(default_factory=dict)


class RunResumeRequest(StrictModel):
    source_run_id: str
    checkpoint_id: str


class RunResumeCheckpoint(StrictModel):
    checkpoint_id: str
    step: int
    completed_agent_ids: list[str] = Field(default_factory=list)
    completed_agent_names: list[str] = Field(default_factory=list)
    pending_agent_ids: list[str] = Field(default_factory=list)
    pending_agent_names: list[str] = Field(default_factory=list)
    available: bool
    disabled_reason: str | None = None
    inherited_usage: dict[str, int] = Field(default_factory=dict)
    snapshot_available: bool
    workspace_snapshot_available: bool
    global_disabled_reason: str | None = None


class RunResumeOptions(StrictModel):
    source_run_id: str
    project_id: str
    checkpoints: list[RunResumeCheckpoint] = Field(default_factory=list)
    snapshot_available: bool
    global_disabled_reason: str | None = None


class GateDecisionRequest(StrictModel):
    decision: Literal["approve", "reject"]
    comment: str | None = Field(default=None, max_length=2_000)


class GateDecisionResponse(StrictModel):
    run_id: str
    gate_id: str
    decision: Literal["approve", "reject"]
    accepted_at: datetime


class PendingHumanGate(StrictModel):
    gate_id: str
    edge_id: str
    prompt: str
    requested_at: datetime
    timeout_seconds: int | None = None


class InputFileRef(StrictModel):
    input_name: str
    name: str
    path: str
    size: int


class RunSummary(StrictModel):
    run_id: str
    project_id: str
    status: Literal[
        "queued", "running", "waiting", "succeeded", "failed", "cancelled"
    ] = "queued"
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None
    run_directory: str | None = None
    output_directory: str | None = None
    resumed_from_run_id: str | None = None
    resume_checkpoint_id: str | None = None


class RunDetail(RunSummary):
    result: dict[str, Any] | None = None
    events: list[dict[str, Any]] = Field(default_factory=list)
    pending_gates: list[PendingHumanGate] = Field(default_factory=list)
class OpenFolderResponse(StrictModel):
    path: str
    opened: bool = True

