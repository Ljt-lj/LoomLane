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
MAX_REGEX_PATTERN_LENGTH = 256
MAX_REGEX_TARGET_LENGTH = 4_096
MAX_REGEX_REPEAT = 1_000
_UNARY_OPERATORS = frozenset({"exists", "empty", "not_empty"})
_BINARY_OPERATORS = frozenset(
    {
        "eq",
        "ne",
        "lt",
        "lte",
        "gt",
        "gte",
        "in",
        "contains",
        "between",
        "starts_with",
        "ends_with",
        "matches",
        "length_eq",
        "contains_all",
        "contains_any",
    }
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


def validate_regex_pattern(pattern: str) -> list[str]:
    errors: list[str] = []
    if len(pattern) > MAX_REGEX_PATTERN_LENGTH:
        return [
            f"regex pattern exceeds {MAX_REGEX_PATTERN_LENGTH} characters"
        ]

    index = 0
    in_character_class = False
    while index < len(pattern):
        character = pattern[index]
        if character == "\\":
            if index + 1 >= len(pattern):
                errors.append("regex pattern ends with an incomplete escape")
                break
            escaped = pattern[index + 1]
            if escaped in "123456789gk":
                errors.append("regex backreferences are not allowed")
            index += 2
            continue
        if in_character_class:
            if character == "]":
                in_character_class = False
            index += 1
            continue
        if character == "[":
            in_character_class = True
            index += 1
            continue
        if character == "(":
            errors.append(
                "regex groups, lookarounds, and inline flags are not allowed"
            )
            break
        if character == "{":
            closing = pattern.find("}", index + 1)
            if closing == -1:
                errors.append("regex repeat is missing a closing brace")
                break
            body = pattern[index + 1 : closing]
            values = body.split(",")
            if len(values) > 2 or not values:
                errors.append("regex repeat is invalid")
                break
            for value in values:
                if value and (
                    not value.isdigit() or int(value) > MAX_REGEX_REPEAT
                ):
                    errors.append(
                        f"regex repeats must be at most {MAX_REGEX_REPEAT}"
                    )
                    break
            index = closing + 1
            continue
        index += 1

    if in_character_class:
        errors.append("regex character class is missing a closing bracket")
    if errors:
        return errors
    try:
        re.compile(pattern)
    except re.error as exc:
        return [f"invalid regex pattern: {exc}"]
    return []


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
        if condition.right is not None:
            errors.append(f"{path}.{condition.op} cannot define 'right'")
    elif condition.op == "not":
        if len(condition.args) != 1:
            errors.append(f"{path}.not requires exactly one argument")
        if condition.left is not None:
            errors.append(f"{path}.not cannot define 'left'")
        if condition.right is not None:
            errors.append(f"{path}.not cannot define 'right'")
    elif condition.op in _UNARY_OPERATORS:
        if not condition.left:
            errors.append(f"{path}.{condition.op} requires 'left'")
        if condition.args:
            errors.append(f"{path}.{condition.op} cannot define 'args'")
        if condition.right is not None:
            errors.append(f"{path}.{condition.op} cannot define 'right'")
    else:
        if not condition.left:
            errors.append(f"{path}.{condition.op} requires 'left'")
        if condition.args:
            errors.append(f"{path}.{condition.op} cannot define 'args'")
        right = condition.right
        if condition.op in {"starts_with", "ends_with"} and not isinstance(
            right, str
        ):
            errors.append(
                f"{path}.{condition.op} requires a string on the right"
            )
        elif condition.op == "matches":
            if not isinstance(right, str):
                errors.append(f"{path}.matches requires a string on the right")
            else:
                errors.extend(
                    f"{path}.matches: {error}"
                    for error in validate_regex_pattern(right)
                )
        elif condition.op == "between" and (
            not isinstance(right, (list, tuple)) or len(right) != 2
        ):
            errors.append(
                f"{path}.between requires a two-item list on the right"
            )
        elif condition.op == "length_eq" and (
            not isinstance(right, int)
            or isinstance(right, bool)
            or right < 0
        ):
            errors.append(
                f"{path}.length_eq requires a non-negative integer on the right"
            )
        elif condition.op in {"contains_all", "contains_any"} and not isinstance(
            right, (list, tuple)
        ):
            errors.append(
                f"{path}.{condition.op} requires a list on the right"
            )

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

    if condition.op == "in" and not isinstance(
        condition.right, (list, tuple, str, dict)
    ):
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


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _safe_equal(left: Any, right: Any) -> bool:
    if left is MISSING or right is MISSING:
        return False
    if _is_number(left) and _is_number(right):
        return left == right
    if type(left) is not type(right):
        return False
    if isinstance(left, (list, tuple)):
        return len(left) == len(right) and all(
            _safe_equal(a, b) for a, b in zip(left, right, strict=True)
        )
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(
            _safe_equal(left[key], right[key]) for key in left
        )
    return left == right


def _safe_order(operator: str, left: Any, right: Any) -> bool:
    if left is MISSING or right is MISSING:
        return False
    if not (
        (_is_number(left) and _is_number(right))
        or (isinstance(left, str) and isinstance(right, str))
    ):
        return False
    try:
        if operator == "lt":
            return left < right
        if operator == "lte":
            return left <= right
        if operator == "gt":
            return left > right
        return left >= right
    except (TypeError, ValueError):
        return False


def _safe_member(container: Any, item: Any) -> bool:
    if container is MISSING or item is MISSING:
        return False
    if isinstance(container, str):
        return isinstance(item, str) and item in container
    if isinstance(container, dict):
        return any(_safe_equal(key, item) for key in container)
    if isinstance(container, (list, tuple, set, frozenset)):
        return any(_safe_equal(candidate, item) for candidate in container)
    return False


def _safe_length(value: Any) -> int | None:
    if isinstance(value, (str, list, tuple, dict, set, frozenset)):
        return len(value)
    return None


def _is_empty(value: Any) -> bool:
    if value is MISSING:
        return True
    if value is None:
        return True
    length = _safe_length(value)
    return length == 0 if length is not None else False


def _regex_matches(value: Any, pattern: Any) -> bool:
    if (
        not isinstance(value, str)
        or not isinstance(pattern, str)
        or len(value) > MAX_REGEX_TARGET_LENGTH
        or validate_regex_pattern(pattern)
    ):
        return False
    try:
        return re.search(pattern, value) is not None
    except re.error:
        return False


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
    if left is MISSING and op not in {"empty", "not_empty"}:
        return False
    if op == "eq":
        return _safe_equal(left, right)
    if op == "ne":
        return not _safe_equal(left, right)
    if op in {"lt", "lte", "gt", "gte"}:
        return _safe_order(op, left, right)
    if op == "in":
        return _safe_member(right, left)
    if op == "contains":
        return _safe_member(left, right)
    if op == "between":
        if not isinstance(right, (list, tuple)) or len(right) != 2:
            return False
        return _safe_order("gte", left, right[0]) and _safe_order(
            "lte", left, right[1]
        )
    if op == "starts_with":
        return isinstance(left, str) and isinstance(right, str) and left.startswith(
            right
        )
    if op == "ends_with":
        return isinstance(left, str) and isinstance(right, str) and left.endswith(
            right
        )
    if op == "matches":
        return _regex_matches(left, right)
    if op == "empty":
        return _is_empty(left)
    if op == "not_empty":
        return not _is_empty(left)
    if op == "length_eq":
        return (
            isinstance(right, int)
            and not isinstance(right, bool)
            and right >= 0
            and _safe_length(left) == right
        )
    if op in {"contains_all", "contains_any"}:
        if not isinstance(right, (list, tuple)):
            return False
        matches = (_safe_member(left, item) for item in right)
        return all(matches) if op == "contains_all" else any(matches)
    raise ConditionError(f"unsupported operator: {op}")


def is_valid_identifier(value: str, *, allow_dash: bool = True) -> bool:
    if allow_dash:
        return bool(re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", value))
    return bool(_IDENTIFIER.fullmatch(value))

