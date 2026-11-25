from __future__ import annotations

import pytest

from loomlane.rule_sandbox import (
    AgentRuleError,
    RuleValidationError,
    evaluate_expression,
    evaluate_rule_isolated,
    validate_rule_expression,
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
