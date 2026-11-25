from __future__ import annotations

import pytest

from loomlane.rule_sandbox import (
    AgentRuleError,
    RuleValidationError,
    evaluate_expression,
    evaluate_json_expression,
    evaluate_rule_isolated,
    evaluate_transform_isolated,
    validate_rule_expression,
    validate_transform_expression,
)


def test_safe_expression_helpers_and_nested_access() -> None:
    context = {
        "inputs": {
            "brief": "A short brief",
            "items": ["a", "b"],
            "empty_items": [],
        },
        "outputs": {"draft": "A complete draft"},
    }

    assert evaluate_expression(
        "length(inputs.items) == 2 "
        "and not_empty(outputs.draft) "
        "and startswith(outputs.draft, 'A')",
        context,
    )
    assert evaluate_expression(
        "empty(inputs.empty_items) and not empty(inputs.items)",
        context,
    )
    assert evaluate_expression(
        "length(inputs.brief) == 13 and contains(inputs.items, 'b')",
        context,
    )


@pytest.mark.parametrize(
    "expression, fragment",
    [
        ("__import__('os').system('dir')", "only documented safe helper"),
        ("[item for item in inputs.items]", "ListComp"),
        ("lambda value: value", "Lambda"),
        ("inputs.__class__", "not allowed"),
        ("(lambda: True)()", "only documented safe helper"),
        ("open('x')", "only documented safe helper"),
        ("{**inputs}", "dictionary unpacking"),
    ],
)
def test_unsafe_python_syntax_is_rejected(
    expression: str,
    fragment: str,
) -> None:
    errors = validate_rule_expression(
        expression,
        allowed_roots={"inputs"},
    )

    assert errors
    assert fragment in errors[0]
    with pytest.raises(RuleValidationError):
        evaluate_expression(
            expression,
            {"inputs": {"items": [1]}},
            allowed_roots={"inputs"},
        )


def test_rule_must_return_boolean() -> None:
    with pytest.raises(AgentRuleError, match="must return a boolean"):
        evaluate_expression("1", {})


def test_expression_and_context_limits_are_enforced() -> None:
    with pytest.raises(RuleValidationError, match="2000"):
        evaluate_expression("true" + (" or true" * 400), {})

    with pytest.raises(AgentRuleError, match="context exceeds the size limit"):
        evaluate_rule_isolated(
            "true",
            {"payload": "x" * 1_000_001},
        )


def test_isolated_sandbox_evaluates_boolean() -> None:
    assert evaluate_rule_isolated(
        "not_empty(inputs.brief)",
        {"inputs": {"brief": "ready"}},
        allowed_roots={"inputs"},
    )
    assert not evaluate_rule_isolated(
        "not_empty(inputs.brief)",
        {"inputs": {"brief": ""}},
        allowed_roots={"inputs"},
    )


def test_isolated_transform_sandbox_returns_json_values() -> None:
    context = {
        "value": {"title": "A title", "items": ["alpha", "beta"]},
        "workflow": {"inputs": {"brief": "source"}},
    }
    roots = {"value", "workflow"}

    assert evaluate_transform_isolated(
        '{"text": upper(value["title"])}',
        context,
        allowed_roots=roots,
    ) == {"text": "A TITLE"}
    assert evaluate_transform_isolated(
        "value.items",
        context,
        allowed_roots=roots,
    ) == ["alpha", "beta"]
    assert evaluate_transform_isolated(
        "length(value.items)",
        context,
        allowed_roots=roots,
    ) == 2


@pytest.mark.parametrize(
    "expression, fragment",
    [
        ("(value,)", "tuple"),
        ("{value}", "set"),
        ("{1: value}", "keys must be strings"),
        ('float("nan")', "NaN or Infinity"),
        ('float("inf")', "NaN or Infinity"),
        ('"x" * 10001', "multiplication count is too large"),
        ('["x"] * 10001', "multiplication count is too large"),
    ],
)
def test_transform_rejects_non_strict_json_results(
    expression: str,
    fragment: str,
) -> None:
    with pytest.raises(AgentRuleError, match=fragment):
        evaluate_json_expression(
            expression,
            {"value": "payload"},
            allowed_roots={"value"},
        )


def test_transform_rejects_oversized_json_strings_and_documents() -> None:
    with pytest.raises(AgentRuleError, match="string length limit"):
        evaluate_json_expression(
            "value",
            {"value": "x" * 100_001},
            allowed_roots={"value"},
        )

    with pytest.raises(AgentRuleError, match="exceeds 1000000 bytes"):
        evaluate_json_expression(
            "value",
            {"value": ["x" * 100_000] * 11},
            allowed_roots={"value"},
        )


def test_transform_rejects_circular_results_and_unknown_roots() -> None:
    circular: dict[str, object] = {}
    circular["self"] = circular

    with pytest.raises(AgentRuleError, match="circular reference"):
        evaluate_json_expression(
            "value",
            {"value": circular},
            allowed_roots={"value"},
        )

    errors = validate_transform_expression(
        "outputs.draft",
        allowed_roots={"value", "raw_outputs"},
    )
    assert errors
    assert "unknown name 'outputs'" in errors[0]
