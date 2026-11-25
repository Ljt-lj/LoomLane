from __future__ import annotations

from collections import defaultdict
from typing import Any

from pydantic import ValidationError

from .condition import (
    ConditionReferences,
    is_valid_identifier,
    validate_condition,
)
from .models import (
    AggregateConfig,
    AgentRulePhase,
    EdgeType,
    HumanGateConfig,
    SDL,
    TransformExpression,
    TransformMode,
    ValidationIssue,
    ValidationResponse,
)
from .rule_sandbox import (
    AGENT_INPUT_TRANSFORM_ALLOWED_ROOTS,
    AGENT_OUTPUT_TRANSFORM_ALLOWED_ROOTS,
    DEFAULT_ALLOWED_ROOTS,
    EDGE_TRANSFORM_ALLOWED_ROOTS,
    validate_rule_expression,
    validate_transform_expression,
)
from .transform_config import transform_field_layout_errors


def _issue(
    code: str,
    message: str,
    path: str = "",
    *,
    node_id: str | None = None,
    edge_id: str | None = None,
) -> ValidationIssue:
    return ValidationIssue(
        code=code,
        message=message,
        path=path,
        node_id=node_id,
        edge_id=edge_id,
    )


def _pydantic_issues(exc: ValidationError) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for error in exc.errors():
        loc = ".".join(str(part) for part in error["loc"])
        issues.append(
            _issue(
                "schema_error",
                error["msg"],
                path=loc,
            )
        )
    return issues


def _duplicates(values: list[str]) -> set[str]:
    seen: set[str] = set()
    duplicate_values: set[str] = set()
    for value in values:
        if value in seen:
            duplicate_values.add(value)
        seen.add(value)
    return duplicate_values


def _transform_issues(
    transform: TransformExpression,
    *,
    allowed_roots: frozenset[str] | set[str],
    code: str,
    path: str,
    node_id: str | None = None,
    edge_id: str | None = None,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if transform.mode == TransformMode.EXPRESSION:
        for error in validate_transform_expression(
            transform.expression or "",
            allowed_roots=allowed_roots,
        ):
            issues.append(
                _issue(
                    code,
                    error,
                    f"{path}.expression",
                    node_id=node_id,
                    edge_id=edge_id,
                )
            )
        return issues

    layout_errors = transform_field_layout_errors(transform.fields)
    for field_index, error in layout_errors:
        field_path = (
            f"{path}.fields[{field_index}].path"
            if field_index is not None
            else f"{path}.fields"
        )
        issues.append(
            _issue(
                "invalid_transform_field",
                error,
                field_path,
                node_id=node_id,
                edge_id=edge_id,
            )
        )

    field_expressions_valid = True
    for field_index, field in enumerate(transform.fields):
        expression_errors = validate_transform_expression(
            field.value,
            allowed_roots=allowed_roots,
        )
        if expression_errors:
            field_expressions_valid = False
        for error in expression_errors:
            issues.append(
                _issue(
                    code,
                    error,
                    f"{path}.fields[{field_index}].value",
                    node_id=node_id,
                    edge_id=edge_id,
                )
            )

    if layout_errors or not field_expressions_valid:
        return issues

    try:
        expression = transform.compiled_expression()
    except ValueError as exc:
        issues.append(
            _issue(
                "invalid_transform_field",
                str(exc),
                f"{path}.fields",
                node_id=node_id,
                edge_id=edge_id,
            )
        )
        return issues

    for error in validate_transform_expression(
        expression,
        allowed_roots=allowed_roots,
    ):
        issues.append(
            _issue(
                code,
                error,
                f"{path}.fields",
                node_id=node_id,
                edge_id=edge_id,
            )
        )
    return issues


def _cyclic_nodes(adjacency: dict[str, set[str]]) -> list[str]:
    visiting: set[str] = set()
    visited: set[str] = set()
    stack: list[str] = []
    cycles: set[str] = set()

    def visit(node: str) -> None:
        if node in visited:
            return
        if node in visiting:
            if node in stack:
                cycles.update(stack[stack.index(node) :])
            return
        visiting.add(node)
        stack.append(node)
        for target in adjacency.get(node, set()):
            visit(target)
        stack.pop()
        visiting.remove(node)
        visited.add(node)

    for node in adjacency:
        visit(node)
    return sorted(cycles)


def _can_reach(adjacency: dict[str, set[str]], start: str, target: str) -> bool:
    stack = [start]
    seen: set[str] = set()
    while stack:
        node = stack.pop()
        if node == target:
            return True
        if node in seen:
            continue
        seen.add(node)
        stack.extend(sorted(adjacency.get(node, set()), reverse=True))
    return False


def _semantic_issues(sdl: SDL) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    profile_ids = [profile.id for profile in sdl.api_profiles]
    agent_ids = [agent.id for agent in sdl.agents]
    edge_ids = [edge.id for edge in sdl.edges]
    profiles = {profile.id: profile for profile in sdl.api_profiles}
    agents = {agent.id: agent for agent in sdl.agents}

    for value in profile_ids:
        if not is_valid_identifier(value):
            issues.append(
                _issue(
                    "invalid_profile_id",
                    f"API Profile ID '{value}' is invalid",
                    "api_profiles",
                )
            )
    for value in agent_ids:
        if not is_valid_identifier(value):
            issues.append(
                _issue(
                    "invalid_agent_id",
                    f"Agent ID '{value}' is invalid",
                    "agents",
                    node_id=value,
                )
            )
    for value in edge_ids:
        if not is_valid_identifier(value):
            issues.append(
                _issue(
                    "invalid_edge_id",
                    f"Edge ID '{value}' is invalid",
                    "edges",
                    edge_id=value,
                )
            )

    for duplicate in sorted(_duplicates(profile_ids)):
        issues.append(
            _issue(
                "duplicate_profile_id",
                f"Duplicate API Profile ID '{duplicate}'",
                "api_profiles",
            )
        )
    for duplicate in sorted(_duplicates(agent_ids)):
        issues.append(
            _issue(
                "duplicate_agent_id",
                f"Duplicate Agent ID '{duplicate}'",
                "agents",
                node_id=duplicate,
            )
        )
    for duplicate in sorted(_duplicates(edge_ids)):
        issues.append(
            _issue(
                "duplicate_edge_id",
                f"Duplicate edge ID '{duplicate}'",
                "edges",
                edge_id=duplicate,
            )
        )

    if not sdl.agents:
        issues.append(_issue("no_agents", "Workflow must contain at least one Agent"))
    if not sdl.start:
        issues.append(_issue("missing_start", "Workflow must define at least one start Agent"))

    for duplicate in sorted(_duplicates(sdl.start)):
        issues.append(
            _issue(
                "duplicate_start",
                f"Start Agent '{duplicate}' is listed more than once",
                "start",
                node_id=duplicate,
            )
        )

    workflow_input_names = {item.name for item in sdl.workflow.inputs}
    for duplicate in sorted(_duplicates([item.name for item in sdl.workflow.inputs])):
        issues.append(
            _issue(
                "duplicate_workflow_input",
                f"Duplicate workflow input '{duplicate}'",
                "workflow.inputs",
            )
        )
    for input_port in sdl.workflow.inputs:
        if not is_valid_identifier(input_port.name, allow_dash=False):
            issues.append(
                _issue(
                    "invalid_workflow_input",
                    f"Workflow input name '{input_port.name}' is invalid",
                    "workflow.inputs",
                )
            )

    for agent in sdl.agents:
        if not agent.inputs and not agent.outputs:
            issues.append(
                _issue(
                    "empty_agent_ports",
                    f"Agent '{agent.id}' must define at least one input or output",
                    f"agents.{agent.id}",
                    node_id=agent.id,
                )
            )
        for port in [*agent.inputs, *agent.outputs]:
            if not is_valid_identifier(port.name, allow_dash=False):
                issues.append(
                    _issue(
                        "invalid_port_name",
                        f"Port name '{port.name}' on Agent '{agent.id}' is invalid",
                        f"agents.{agent.id}",
                        node_id=agent.id,
                    )
                )
        duplicate_inputs = _duplicates([port.name for port in agent.inputs])
        duplicate_outputs = _duplicates([port.name for port in agent.outputs])
        for duplicate in sorted(duplicate_inputs):
            issues.append(
                _issue(
                    "duplicate_input_port",
                    f"Duplicate input port '{duplicate}' on Agent '{agent.id}'",
                    f"agents.{agent.id}.inputs",
                    node_id=agent.id,
                )
            )
        for duplicate in sorted(duplicate_outputs):
            issues.append(
                _issue(
                    "duplicate_output_port",
                    f"Duplicate output port '{duplicate}' on Agent '{agent.id}'",
                    f"agents.{agent.id}.outputs",
                    node_id=agent.id,
                )
            )
        for rule_index, rule in enumerate(agent.rules):
            allowed_roots = (
                frozenset({"inputs", "workflow", "edges", "run"})
                if rule.phase == AgentRulePhase.INPUT
                else DEFAULT_ALLOWED_ROOTS
            )
            for error in validate_rule_expression(
                rule.expression,
                allowed_roots=allowed_roots,
            ):
                issues.append(
                    _issue(
                        "invalid_agent_rule",
                        error,
                        f"agents.{agent.id}.rules[{rule_index}].expression",
                        node_id=agent.id,
                    )
                )
        for attribute, allowed_roots in (
            ("input_transform", AGENT_INPUT_TRANSFORM_ALLOWED_ROOTS),
            ("output_transform", AGENT_OUTPUT_TRANSFORM_ALLOWED_ROOTS),
        ):
            transform = getattr(agent, attribute)
            if transform is None:
                continue
            issues.extend(
                _transform_issues(
                    transform,
                    code="invalid_agent_transform",
                    path=f"agents.{agent.id}.{attribute}",
                    node_id=agent.id,
                    allowed_roots=allowed_roots,
                )
            )
        profile = profiles.get(agent.api_profile)
        if profile is None:
            issues.append(
                _issue(
                    "unknown_api_profile",
                    f"Agent '{agent.id}' references unknown API Profile "
                    f"'{agent.api_profile}'",
                    f"agents.{agent.id}.api_profile",
                    node_id=agent.id,
                )
            )
        elif agent.mode == "image":
            if not profile.capabilities.image_generation:
                issues.append(
                    _issue(
                        "image_profile_required",
                        f"Image Agent '{agent.id}' requires an API Profile with "
                        "image_generation capability",
                        f"agents.{agent.id}.mode",
                        node_id=agent.id,
                    )
                )
            if len(agent.outputs) != 1 or agent.outputs[0].type.value != "FILE":
                issues.append(
                    _issue(
                        "image_output_contract",
                        f"Image Agent '{agent.id}' must define exactly one FILE "
                        "output port",
                        f"agents.{agent.id}.outputs",
                        node_id=agent.id,
                    )
                )
            if agent.json_output.enabled:
                issues.append(
                    _issue(
                        "image_json_output",
                        f"Image Agent '{agent.id}' cannot enable structured JSON "
                        "output",
                        f"agents.{agent.id}.json_output",
                        node_id=agent.id,
                    )
                )
        if agent.mode == "chat" and agent.json_output.enabled:
            template = agent.json_output.template
            for port in agent.outputs:
                if port.name not in template:
                    continue
                template_value = template[port.name]
                if port.type.value == "TEXT" and isinstance(
                    template_value, (dict, list)
                ):
                    issues.append(
                        _issue(
                            "json_output_port_type_mismatch",
                            f"Output port '{port.name}' on Agent '{agent.id}' is "
                            "TEXT, but its JSON template is structured. Use a "
                            "string description or change the port type to DATA.",
                            f"agents.{agent.id}.json_output.template.{port.name}",
                            node_id=agent.id,
                        )
                    )
                if port.type.value == "FILE" and not (
                    isinstance(template_value, dict)
                    and isinstance(template_value.get("path"), str)
                ):
                    issues.append(
                        _issue(
                            "json_output_port_type_mismatch",
                            f"Output port '{port.name}' on Agent '{agent.id}' is "
                            "FILE, but its JSON template is not an object with a "
                            "string 'path' field.",
                            f"agents.{agent.id}.json_output.template.{port.name}",
                            node_id=agent.id,
                        )
                    )

    for start_id in sdl.start:
        if start_id not in agents:
            issues.append(
                _issue(
                    "unknown_start_agent",
                    f"Start Agent '{start_id}' does not exist",
                    "start",
                    node_id=start_id,
                )
            )

    adjacency: dict[str, set[str]] = defaultdict(set)
    incoming: dict[str, int] = defaultdict(int)
    for edge in sdl.edges:
        if (
            edge.type not in {EdgeType.FEEDBACK, EdgeType.LOOP}
            and edge.from_ in agents
            and edge.to in agents
        ):
            adjacency[edge.from_].add(edge.to)
            incoming[edge.to] += 1

    aggregate_modes: dict[str, set[str]] = defaultdict(set)
    aggregate_targets: set[str] = set()
    non_aggregate_targets: set[str] = set()
    for edge in sdl.edges:
        if edge.from_ not in agents:
            issues.append(
                _issue(
                    "unknown_edge_source",
                    f"Edge '{edge.id}' references unknown source '{edge.from_}'",
                    f"edges.{edge.id}.from",
                    edge_id=edge.id,
                )
            )
        if edge.to not in agents:
            issues.append(
                _issue(
                    "unknown_edge_target",
                    f"Edge '{edge.id}' references unknown target '{edge.to}'",
                    f"edges.{edge.id}.to",
                    edge_id=edge.id,
                )
            )
        if not edge.mappings and edge.type in {
            EdgeType.PIPE,
            EdgeType.CONDITIONAL,
            EdgeType.FEEDBACK,
        }:
            issues.append(
                _issue(
                    "missing_mappings",
                    f"Edge '{edge.id}' must define at least one mapping",
                    f"edges.{edge.id}.mappings",
                    edge_id=edge.id,
                )
            )

        if edge.type == EdgeType.PIPE and edge.condition is not None:
            issues.append(
                _issue(
                    "pipe_condition",
                    f"PIPE edge '{edge.id}' cannot define a condition",
                    f"edges.{edge.id}.condition",
                    edge_id=edge.id,
                )
            )
        if edge.type == EdgeType.CONDITIONAL and edge.condition is None:
            issues.append(
                _issue(
                    "conditional_missing_condition",
                    f"CONDITIONAL edge '{edge.id}' requires a condition",
                    f"edges.{edge.id}.condition",
                    edge_id=edge.id,
                )
            )
        if edge.type == EdgeType.LOOP and edge.condition is None:
            issues.append(
                _issue(
                    "loop_missing_condition",
                    f"LOOP edge '{edge.id}' requires a continuation condition",
                    f"edges.{edge.id}.condition",
                    edge_id=edge.id,
                )
            )
        if edge.type in {EdgeType.FEEDBACK, EdgeType.LOOP}:
            if edge.guard.max_traversals is None:
                edge.guard.max_traversals = (
                    sdl.workflow.execution.loop_limit_default
                    if edge.type == EdgeType.LOOP
                    else sdl.workflow.execution.feedback_limit_default
                )
            if edge.from_ in agents and edge.to in agents:
                if edge.from_ == edge.to or not _can_reach(adjacency, edge.to, edge.from_):
                    edge_type = str(edge.type)
                    issues.append(
                        _issue(
                            f"{edge_type.lower()}_not_upstream",
                            f"{edge_type} edge '{edge.id}' must target an "
                            "upstream Agent",
                            f"edges.{edge.id}.to",
                            edge_id=edge.id,
                        )
                    )
        if edge.type == EdgeType.AGGREGATE:
            if edge.aggregate is None:
                edge.aggregate = AggregateConfig()
            aggregate_targets.add(edge.to)
            aggregate_modes[edge.to].add(edge.aggregate.mode.value)
        elif edge.aggregate is not None:
            issues.append(
                _issue(
                    "aggregate_config_on_non_aggregate",
                    f"Edge '{edge.id}' defines aggregate settings but is not "
                    "AGGREGATE",
                    f"edges.{edge.id}.aggregate",
                    edge_id=edge.id,
                )
            )
        if edge.to in agents and edge.type != EdgeType.AGGREGATE:
            non_aggregate_targets.add(edge.to)
        if edge.type == EdgeType.HUMAN_GATE:
            if edge.human_gate is None:
                edge.human_gate = HumanGateConfig()
        elif edge.human_gate is not None:
            issues.append(
                _issue(
                    "human_gate_config_on_non_gate",
                    f"Edge '{edge.id}' defines human_gate settings but is not "
                    "HUMAN_GATE",
                    f"edges.{edge.id}.human_gate",
                    edge_id=edge.id,
                )
            )

        source_agent = agents.get(edge.from_)
        target_agent = agents.get(edge.to)
        if source_agent and target_agent:
            source_ports = {port.name: port for port in source_agent.outputs}
            target_ports = {port.name: port for port in target_agent.inputs}
            for mapping_index, mapping in enumerate(edge.mappings):
                if mapping.transform is not None:
                    issues.extend(
                        _transform_issues(
                            mapping.transform,
                            code="invalid_edge_transform",
                            path=(
                                f"edges.{edge.id}.mappings"
                                f"[{mapping_index}].transform"
                            ),
                            edge_id=edge.id,
                            allowed_roots=EDGE_TRANSFORM_ALLOWED_ROOTS,
                        )
                    )
                source_port = source_ports.get(mapping.from_)
                target_port = target_ports.get(mapping.to)
                if source_port is None:
                    issues.append(
                        _issue(
                            "unknown_source_port",
                            f"Edge '{edge.id}' maps unknown output "
                            f"'{mapping.from_}' on '{source_agent.id}'",
                            f"edges.{edge.id}.mappings",
                            edge_id=edge.id,
                        )
                    )
                if target_port is None:
                    issues.append(
                        _issue(
                            "unknown_target_port",
                            f"Edge '{edge.id}' maps unknown input "
                            f"'{mapping.to}' on '{target_agent.id}'",
                            f"edges.{edge.id}.mappings",
                            edge_id=edge.id,
                        )
                    )
                if source_port and target_port:
                    compatible = (
                        source_port.type.value == target_port.type.value
                        or "MIXED" in {source_port.type.value, target_port.type.value}
                    )
                    if not compatible and mapping.transform is None:
                        issues.append(
                            _issue(
                                "port_type_mismatch",
                                f"Edge '{edge.id}' maps {source_port.type.value} "
                                f"to {target_port.type.value}",
                                f"edges.{edge.id}.mappings",
                                edge_id=edge.id,
                            )
                        )

    for agent_id, modes in aggregate_modes.items():
        if len(modes) > 1:
            issues.append(
                _issue(
                    "mixed_aggregate_modes",
                    f"Agent '{agent_id}' has AGGREGATE edges with mixed modes",
                    f"agents.{agent_id}.inputs",
                    node_id=agent_id,
                )
            )
    for agent_id in sorted(aggregate_targets & non_aggregate_targets):
        issues.append(
            _issue(
                "mixed_aggregate_inputs",
                f"Agent '{agent_id}' cannot mix AGGREGATE and other incoming "
                "edge types",
                f"agents.{agent_id}.inputs",
                node_id=agent_id,
            )
        )

    if not issues:
        cycles = _cyclic_nodes(adjacency)
        for node in cycles:
            issues.append(
                _issue(
                    "cycle_not_allowed",
                    f"Agent '{node}' participates in a cycle without a "
                    "LOOP or FEEDBACK edge",
                    f"agents.{node}",
                    node_id=node,
                )
            )

    if not issues and sdl.start:
        reachable: set[str] = set()
        stack = list(sdl.start)
        while stack:
            node = stack.pop()
            if node in reachable:
                continue
            reachable.add(node)
            stack.extend(sorted(adjacency.get(node, set())))
        for agent in sdl.agents:
            if agent.id not in reachable:
                issues.append(
                    _issue(
                        "unreachable_agent",
                        f"Agent '{agent.id}' is not reachable from a start Agent",
                        f"agents.{agent.id}",
                        node_id=agent.id,
                    )
                )

    if not issues:
        for start_id in sdl.start:
            agent = agents[start_id]
            for port in agent.inputs:
                if port.required and port.name not in workflow_input_names and incoming[start_id] == 0:
                    issues.append(
                        _issue(
                            "missing_start_input",
                            f"Required input '{port.name}' on start Agent "
                            f"'{start_id}' has no workflow input",
                            f"agents.{start_id}.inputs",
                            node_id=start_id,
                        )
                    )

    start_input_names = {
        port.name
        for start_id in sdl.start
        if start_id in agents
        for port in agents[start_id].inputs
    }
    for workflow_input in sdl.workflow.inputs:
        if workflow_input.name not in start_input_names:
            issues.append(
                _issue(
                    "unused_workflow_input",
                    f"Workflow input '{workflow_input.name}' is not accepted by a "
                    "start Agent",
                    "workflow.inputs",
                )
            )

    for output in sdl.workflow.outputs:
        agent = agents.get(output.agent)
        if agent is None:
            issues.append(
                _issue(
                    "unknown_workflow_output_agent",
                    f"Workflow output '{output.name}' references unknown Agent "
                    f"'{output.agent}'",
                    "workflow.outputs",
                )
            )
        elif output.port not in {port.name for port in agent.outputs}:
            issues.append(
                _issue(
                    "unknown_workflow_output_port",
                    f"Workflow output '{output.name}' references unknown port "
                    f"'{output.port}' on '{output.agent}'",
                    "workflow.outputs",
                )
            )
    if not sdl.workflow.outputs:
        issues.append(
            _issue(
                "missing_workflow_output",
                "Workflow must declare at least one output",
                "workflow.outputs",
            )
        )

    references = ConditionReferences(
        agents=set(agent_ids),
        agent_ports={
            (agent.id, port.name) for agent in sdl.agents for port in agent.outputs
        },
        workflow_inputs=workflow_input_names,
        edges=set(edge_ids),
        run_fields={"step"},
    )
    for edge in sdl.edges:
        if edge.condition is not None:
            for error in validate_condition(
                edge.condition, references, f"edges.{edge.id}.condition"
            ):
                issues.append(
                    _issue(
                        "invalid_condition",
                        error,
                        f"edges.{edge.id}.condition",
                        edge_id=edge.id,
                    )
                )

    return issues


def validate_sdl(data: SDL | dict[str, Any]) -> ValidationResponse:
    if isinstance(data, SDL):
        sdl = data
    else:
        try:
            sdl = SDL.model_validate(data)
        except ValidationError as exc:
            return ValidationResponse(valid=False, issues=_pydantic_issues(exc))

    issues = _semantic_issues(sdl)
    return ValidationResponse(
        valid=not any(issue.severity == "error" for issue in issues),
        issues=issues,
        normalized_sdl=(
            sdl.canonical_dict()
            if not any(issue.severity == "error" for issue in issues)
            else None
        ),
    )

