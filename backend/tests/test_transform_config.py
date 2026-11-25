from __future__ import annotations

from collections.abc import Callable

import pytest
from pydantic import ValidationError

from loomlane.models import SDL, TransformExpression, TransformField
from loomlane.transform_config import (
    build_transform_expression,
    is_valid_transform_path,
    transform_field_layout_errors,
)


def _field(path: str, value: str) -> TransformField:
    return TransformField(path=path, value=value)


def test_transform_fields_build_nested_object_expression() -> None:
    expression = build_transform_expression(
        [
            _field("brief", "inputs.brief"),
            _field("outline.sections", "raw_outputs.sections"),
            _field("outline.title", '"Draft"'),
        ]
    )

    assert expression == (
        '{"brief": inputs.brief, '
        '"outline": {"sections": raw_outputs.sections, "title": "Draft"}}'
    )


@pytest.mark.parametrize(
    ("path", "valid"),
    [
        ("brief", True),
        ("outline.sections", True),
        ("a.b.c.d.e.f.g.h", True),
        ("Brief", False),
        ("outline..sections", False),
        ("_brief", False),
        ("a.b.c.d.e.f.g.h.i", False),
    ],
)
def test_transform_field_path_validation(path: str, valid: bool) -> None:
    assert is_valid_transform_path(path) is valid


def test_transform_field_layout_reports_duplicates_and_parent_conflicts() -> None:
    errors = transform_field_layout_errors(
        [
            _field("brief", "inputs.brief"),
            _field("brief", '"duplicate"'),
            _field("outline", "raw_outputs.outline"),
            _field("outline.sections", "raw_outputs.sections"),
        ]
    )

    assert errors == [
        (1, "field path 'brief' is duplicated"),
        (
            3,
            "field path 'outline.sections' conflicts with parent field 'outline'",
        ),
    ]
    with pytest.raises(ValueError, match="conflicts with parent field"):
        build_transform_expression(
            [
                _field("outline", "raw_outputs.outline"),
                _field("outline.sections", "raw_outputs.sections"),
            ]
        )


def test_transform_fields_require_at_least_one_field() -> None:
    with pytest.raises(ValueError, match="requires at least one field"):
        build_transform_expression([])
    with pytest.raises(ValidationError, match="requires at least one field"):
        TransformExpression(mode="fields", fields=[])


def test_legacy_transform_expression_remains_supported() -> None:
    transform = TransformExpression(expression="value")

    assert transform.compiled_expression() == "value"
    assert transform.runtime_dict() == {"expression": "value"}


def test_sdl_keeps_fields_in_project_data_and_compiles_runtime_expression(
    legacy_workflow: Callable[[str], SDL],
) -> None:
    sdl = legacy_workflow()
    sdl.agents[0].input_transform = TransformExpression(
        mode="fields",
        fields=[
            _field("requirement", "inputs.requirement"),
            _field("outline.sections", "raw_outputs.outline.sections"),
        ],
    )
    sdl.edges[0].mappings[0].transform = TransformExpression(
        expression='{"text": value}'
    )

    canonical = sdl.canonical_dict()
    runtime = sdl.runtime_dict()

    assert canonical["agents"][0]["input_transform"] == {
        "mode": "fields",
        "fields": [
            {"path": "requirement", "value": "inputs.requirement"},
            {
                "path": "outline.sections",
                "value": "raw_outputs.outline.sections",
            },
        ],
    }
    assert runtime["agents"][0]["input_transform"] == {
        "expression": (
            '{"requirement": inputs.requirement, '
            '"outline": {"sections": raw_outputs.outline.sections}}'
        )
    }
    assert canonical["edges"][0]["mappings"][0]["transform"]["mode"] == (
        "expression"
    )
    assert runtime["edges"][0]["mappings"][0]["transform"] == {
        "expression": '{"text": value}'
    }
