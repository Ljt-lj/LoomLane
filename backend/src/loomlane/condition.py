from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .models import Condition


class ConditionError(ValueError):
    pass


_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_AGENT_REFERENCE = re.compile(
    r"^\$\.agents\.(?P<agent>[a-z][a-z0-9_-]{0,63})"
    r"\.outputs\.(?P<port>[a-z][a-z0-9_]{0,63})(?P<rest>(?:\.[A-Za-z0-9_-]+)*)$"
)
_INPUT_REFERENCE = re.compile(
    r"^\$\.workflow\.inputs\.(?P<name>[a-z][a-z0-9_]{0,63})"
    r"(?P<rest>(?:\.[A-Za-z0-9_-]+)*)$"
)
_EDGE_REFERENCE = re.compile(
    r"^\$\.edges\.(?P<edge>[a-z][a-z0-9_-]{0,63})\.traversals$"
)


@dataclass(frozen=True)
class ConditionReferences:
    agents: set[str]
    agent_ports: set[tuple[str, str]]
    workflow_inputs: set[str]
    edges: set[str]
    run_fields: set[str]


class _Missing:
    pass


MISSING = _Missing()


def _path_parts(path: str) -> list[str]:
    if not path.startswith("$."):
        raise ConditionError("condition paths must start with '$.'")
    parts = path[2:].split(".")
    if any(not part for part in parts):
        raise ConditionError(f"invalid condition path: {path}")
    return parts


def _collect_paths(condition: Condition) -> list[str]:
    paths: list[str] = []
    if condition.left:
        paths.append(condition.left)
    for child in condition.args:
        paths.extend(_collect_paths(child))
    return paths


def validate_condition(
    condition: Condition,
    references: ConditionReferences,
    path: str = "condition",
) -> list[str]:
    errors: list[str] = []

    if condition.op in {"and", "or"}:
        if len(condition.args) < 1:
            errors.append(f"{path}.{condition.op} requires at least one argument")
        if condition.left is not None:
            errors.append(f"{path}.{condition.op} cannot define 'left'")
    elif condition.op == "not":
        if len(condition.args) != 1:
            errors.append(f"{path}.not requires exactly one argument")
        if condition.left is not None:
            errors.append(f"{path}.not cannot define 'left'")
    elif condition.op == "exists":
        if not condition.left:
            errors.append(f"{path}.exists requires 'left'")
        if condition.args:
            errors.append(f"{path}.exists cannot define 'args'")
    else:
        if not condition.left:
            errors.append(f"{path}.{condition.op} requires 'left'")
        if condition.args:
            errors.append(f"{path}.{condition.op} cannot define 'args'")

    for child_index, child in enumerate(condition.args):
        errors.extend(
            validate_condition(child, references, f"{path}.args[{child_index}]")
        )

    for condition_path in _collect_paths(condition):
        try:
            parts = _path_parts(condition_path)
        except ConditionError as exc:
            errors.append(f"{path}: {exc}")
            continue

        if parts[:2] == ["workflow", "inputs"]:
            match = _INPUT_REFERENCE.match(condition_path)
            if not match:
                errors.append(f"{path}: invalid workflow input path {condition_path}")
            elif match.group("name") not in references.workflow_inputs:
                errors.append(
                    f"{path}: unknown workflow input '{match.group('name')}'"
                )
        elif parts[:1] == ["agents"]:
            match = _AGENT_REFERENCE.match(condition_path)
            if not match:
                errors.append(f"{path}: invalid agent output path {condition_path}")
                continue
            agent_id = match.group("agent")
            port = match.group("port")
            if agent_id not in references.agents:
                errors.append(f"{path}: unknown agent '{agent_id}'")
            if (agent_id, port) not in references.agent_ports:
                errors.append(f"{path}: unknown output port '{port}' on '{agent_id}'")
        elif parts[:1] == ["edges"]:
            match = _EDGE_REFERENCE.match(condition_path)
            if not match:
                errors.append(f"{path}: only edge traversals can be referenced")
            elif match.group("edge") not in references.edges:
                errors.append(f"{path}: unknown edge '{match.group('edge')}'")
        elif parts[:1] == ["run"]:
            if len(parts) != 2 or parts[1] not in references.run_fields:
                errors.append(f"{path}: invalid run field path {condition_path}")
        else:
            errors.append(f"{path}: unsupported condition root {condition_path}")

    if condition.op == "in" and not isinstance(condition.right, (list, tuple, str, dict)):
        errors.append(f"{path}.in requires a list, string, or object on the right")

    return errors


def _resolve(root: dict[str, Any], path: str) -> Any:
    current: Any = root
    for part in _path_parts(path):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return MISSING
    return current


def evaluate_condition(condition: Condition, root: dict[str, Any]) -> bool:
    op = condition.op
    if op == "and":
        return all(evaluate_condition(child, root) for child in condition.args)
    if op == "or":
        return any(evaluate_condition(child, root) for child in condition.args)
    if op == "not":
        return not evaluate_condition(condition.args[0], root)

    left = _resolve(root, condition.left or "")
    right = condition.right

    if op == "exists":
        return left is not MISSING
    if left is MISSING:
        return False
    if op == "eq":
        return left == right
    if op == "ne":
        return left != right
    if op == "lt":
        return left < right
    if op == "lte":
        return left <= right
    if op == "gt":
        return left > right
    if op == "gte":
        return left >= right
    if op == "in":
        return left in right
    if op == "contains":
        return right in left
    raise ConditionError(f"unsupported operator: {op}")


def is_valid_identifier(value: str, *, allow_dash: bool = True) -> bool:
    if allow_dash:
        return bool(re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", value))
    return bool(_IDENTIFIER.fullmatch(value))

