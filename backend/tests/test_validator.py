from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy

from loomlane.condition import (
    ConditionReferences,
    evaluate_condition,
    validate_condition,
)
from loomlane.models import (
    AgentRule,
    AgentRulePhase,
    AggregateConfig,
    Condition,
    EdgeGuard,
    EdgeType,
    ImageGenerationConfig,
    Port,
    PortType,
    SDL,
    TransformExpression,
    TransformField,
    WorkflowOutput,
)
from loomlane.storage import default_sdl
from loomlane.validator import validate_sdl


def test_default_workflow_is_valid() -> None:
    result = validate_sdl(default_sdl())

    assert result.valid
    assert result.normalized_sdl is not None
    assert result.issues == []
    assert len(result.normalized_sdl["agents"]) == 8
    assert len(result.normalized_sdl["edges"]) == 10
    assert {edge["type"] for edge in result.normalized_sdl["edges"]} == {
        edge_type.value for edge_type in EdgeType
    }


def test_missing_workflow_output_returns_validation_issue(
    legacy_workflow: Callable[[str], SDL],
) -> None:
    sdl = legacy_workflow()
    sdl.workflow.outputs.clear()

    result = validate_sdl(sdl)

    assert not result.valid
    assert "missing_workflow_output" in {issue.code for issue in result.issues}


def test_non_feedback_cycle_is_rejected(
    legacy_workflow: Callable[[str], SDL],
) -> None:
    sdl = legacy_workflow()
    sdl.edges.append(
        sdl.edges[0].model_copy(
            update={
                "id": "review-to-draft",
                "from_": "reviewer",
                "to": "drafter",
                "type": "PIPE",
                "mappings": [
                    sdl.edges[0].mappings[0].model_copy(
                        update={"from_": "review", "to": "feedback"}
                    )
                ],
                "condition": None,
            }
        )
    )

    result = validate_sdl(sdl)

    assert not result.valid
    assert {issue.code for issue in result.issues} >= {"cycle_not_allowed"}


def test_feedback_default_guard_and_edge_order_are_stable(
    legacy_workflow: Callable[[str], SDL],
) -> None:
    sdl = legacy_workflow()
    feedback = next(edge for edge in sdl.edges if edge.id == "review-feedback")
    feedback.guard.max_traversals = None
    sdl.edges.reverse()

    result = validate_sdl(sdl)

    assert result.valid
    normalized_feedback = next(
        edge
        for edge in result.normalized_sdl["edges"]
        if edge["id"] == "review-feedback"
    )
    assert normalized_feedback["guard"]["max_traversals"] == 3


def test_feedback_must_target_an_upstream_agent(
    legacy_workflow: Callable[[str], SDL],
) -> None:
    sdl = legacy_workflow()
    sdl.edges.append(
        sdl.edges[0].model_copy(
            update={
                "id": "draft-feedback",
                "from_": "drafter",
                "to": "reviewer",
                "type": "FEEDBACK",
                "mappings": [
                    sdl.edges[0].mappings[0].model_copy(
                        update={"from_": "draft", "to": "draft"}
                    )
                ],
                "condition": None,
                "guard": EdgeGuard(max_traversals=2),
            }
        )
    )

    result = validate_sdl(sdl)

    assert not result.valid
    assert "feedback_not_upstream" in {issue.code for issue in result.issues}


def test_loop_requires_a_continuation_condition(
    legacy_workflow: Callable[[str], SDL],
) -> None:
    sdl = legacy_workflow()
    loop_edge = next(edge for edge in sdl.edges if edge.id == "review-feedback")
    loop_edge.type = EdgeType.LOOP
    loop_edge.condition = None

    result = validate_sdl(sdl)

    assert not result.valid
    assert "loop_missing_condition" in {issue.code for issue in result.issues}


def test_aggregate_rejects_mixed_modes_for_one_target(
    legacy_workflow: Callable[[str], SDL],
) -> None:
    sdl = legacy_workflow()
    first_edge = next(edge for edge in sdl.edges if edge.id == "draft-to-review")
    first_edge.type = EdgeType.AGGREGATE
    first_edge.aggregate = AggregateConfig(mode="all")
    sdl.edges.append(
        first_edge.model_copy(
            update={
                "id": "review-to-review-aggregate",
                "from_": "reviewer",
                "aggregate": AggregateConfig(mode="any"),
            }
        )
    )

    result = validate_sdl(sdl)

    assert not result.valid
    assert "mixed_aggregate_modes" in {issue.code for issue in result.issues}


def test_aggregate_cannot_mix_with_regular_incoming_edges(
    legacy_workflow: Callable[[str], SDL],
) -> None:
    sdl = legacy_workflow()
    aggregate_edge = next(
        edge for edge in sdl.edges if edge.id == "draft-to-review"
    )
    aggregate_edge.type = EdgeType.AGGREGATE
    aggregate_edge.aggregate = AggregateConfig(mode="all")
    sdl.edges.append(
        aggregate_edge.model_copy(
            update={
                "id": "draft-to-review-pipe",
                "type": EdgeType.PIPE,
                "aggregate": None,
            }
        )
    )

    result = validate_sdl(sdl)

    assert not result.valid
    assert "mixed_aggregate_inputs" in {issue.code for issue in result.issues}


def test_condition_rejects_unlisted_paths() -> None:
    condition = Condition(
        op="eq",
        left="$.agents.reviewer.outputs.__class__",
        right="x",
    )
    references = ConditionReferences(
        agents={"reviewer"},
        agent_ports={("reviewer", "score")},
        workflow_inputs=set(),
        edges=set(),
        run_fields={"step"},
    )

    errors = validate_condition(condition, references)

    assert any("invalid agent output path" in error for error in errors)


def test_multi_predicate_condition_validates_and_evaluates() -> None:
    condition = Condition(
        op="and",
        args=[
            Condition(
                op="lt",
                left="$.agents.reviewer.outputs.score",
                right=0.8,
            ),
            Condition(
                op="lt",
                left="$.edges.review-feedback.traversals",
                right=3,
            ),
        ],
    )
    references = ConditionReferences(
        agents={"reviewer"},
        agent_ports={("reviewer", "score")},
        workflow_inputs=set(),
        edges={"review-feedback"},
        run_fields={"step"},
    )

    errors = validate_condition(condition, references)
    root = {
        "agents": {"reviewer": {"outputs": {"score": 0.72}}},
        "edges": {"review-feedback": {"traversals": 2}},
    }

    assert errors == []
    assert evaluate_condition(condition, root)
    root["edges"]["review-feedback"]["traversals"] = 3
    assert not evaluate_condition(condition, root)


def test_agent_rules_validate_by_phase(
    legacy_workflow: Callable[[str], SDL],
) -> None:
    sdl = legacy_workflow()
    sdl.agents[0].rules = [
        AgentRule(
            phase=AgentRulePhase.INPUT,
            expression="not_empty(inputs.requirement)",
        ),
        AgentRule(
            phase=AgentRulePhase.OUTPUT,
            expression="not_empty(outputs.draft)",
        ),
    ]

    result = validate_sdl(sdl)

    assert result.valid
    assert result.issues == []


def test_agent_input_rules_cannot_reference_outputs(
    legacy_workflow: Callable[[str], SDL],
) -> None:
    sdl = legacy_workflow()
    sdl.agents[0].rules = [
        AgentRule(
            phase=AgentRulePhase.INPUT,
            expression="not_empty(outputs.draft)",
        )
    ]

    result = validate_sdl(sdl)

    assert not result.valid
    assert "invalid_agent_rule" in {issue.code for issue in result.issues}
    assert "unknown name 'outputs'" in result.issues[0].message


def test_agent_and_edge_transforms_validate(
    legacy_workflow: Callable[[str], SDL],
) -> None:
    sdl = legacy_workflow()
    drafter = sdl.agents[0]
    drafter.input_transform = TransformExpression(
        expression='{"requirement": str(inputs.requirement)}'
    )
    drafter.output_transform = TransformExpression(
        expression=(
            '{"draft": str(raw_outputs.draft), '
            '"outline": raw_outputs.outline}'
        )
    )
    sdl.edges[0].mappings[0].transform = TransformExpression(
        expression='{"text": value}'
    )

    result = validate_sdl(sdl)

    assert result.valid
    assert result.issues == []


def test_field_transforms_validate_and_report_field_paths(
    legacy_workflow: Callable[[str], SDL],
) -> None:
    sdl = legacy_workflow()
    drafter = sdl.agents[0]
    drafter.input_transform = TransformExpression(
        mode="fields",
        fields=[
            TransformField(
                path="requirement",
                value="inputs.requirement",
            ),
            TransformField(
                path="outline.sections",
                value="inputs.outline.sections",
            ),
        ],
    )

    valid = validate_sdl(sdl)

    assert valid.valid
    assert valid.issues == []

    drafter.input_transform = TransformExpression(
        mode="fields",
        fields=[
            TransformField(
                path="Requirement",
                value="inputs.requirement",
            ),
            TransformField(
                path="Requirement",
                value="inputs.requirement",
            ),
        ],
    )
    invalid_paths = validate_sdl(sdl)

    assert not invalid_paths.valid
    issues_by_path = {issue.path: issue for issue in invalid_paths.issues}
    assert (
        issues_by_path[
            "agents.drafter.input_transform.fields[0].path"
        ].code
        == "invalid_transform_field"
    )
    assert (
        issues_by_path[
            "agents.drafter.input_transform.fields[1].path"
        ].code
        == "invalid_transform_field"
    )

    drafter.input_transform = TransformExpression(
        mode="fields",
        fields=[
            TransformField(
                path="draft",
                value="outputs.draft",
            ),
        ],
    )
    invalid_expression = validate_sdl(sdl)

    assert not invalid_expression.valid
    issues_by_path = {
        issue.path: issue for issue in invalid_expression.issues
    }
    assert (
        issues_by_path[
            "agents.drafter.input_transform.fields[0].value"
        ].code
        == "invalid_agent_transform"
    )


def test_transform_roots_are_scoped_by_location(
    legacy_workflow: Callable[[str], SDL],
) -> None:
    sdl = legacy_workflow()
    sdl.agents[0].input_transform = TransformExpression(
        expression="outputs.draft"
    )
    sdl.agents[0].output_transform = TransformExpression(
        expression="outputs.draft"
    )
    sdl.edges[0].mappings[0].transform = TransformExpression(
        expression="inputs.requirement"
    )

    result = validate_sdl(sdl)

    assert not result.valid
    assert [issue.code for issue in result.issues] == [
        "invalid_agent_transform",
        "invalid_agent_transform",
        "invalid_edge_transform",
    ]
    assert "unknown name 'outputs'" in result.issues[0].message
    assert "unknown name 'outputs'" in result.issues[1].message
    assert "unknown name 'inputs'" in result.issues[2].message


def test_edge_transform_allows_cross_type_mapping(
    legacy_workflow: Callable[[str], SDL],
) -> None:
    sdl = legacy_workflow()
    reviewer = sdl.agents[1]
    reviewer.inputs[0].type = PortType.DATA
    mapping = sdl.edges[0].mappings[0]
    mapping.transform = TransformExpression(expression='{"text": value}')

    transformed = validate_sdl(sdl)

    assert transformed.valid
    assert transformed.issues == []

    mapping.transform = None
    untransformed = validate_sdl(sdl)

    assert not untransformed.valid
    assert "port_type_mismatch" in {
        issue.code for issue in untransformed.issues
    }


def test_new_condition_operators_validate_and_evaluate() -> None:
    references = ConditionReferences(
        agents={"reviewer"},
        agent_ports={
            ("reviewer", "text"),
            ("reviewer", "score"),
            ("reviewer", "items"),
            ("reviewer", "empty"),
        },
        workflow_inputs=set(),
        edges=set(),
        run_fields={"step"},
    )
    root = {
        "agents": {
            "reviewer": {
                "outputs": {
                    "text": "hello world",
                    "score": 0.5,
                    "items": ["alpha", "beta"],
                    "empty": "",
                }
            }
        }
    }
    conditions = [
        Condition(
            op="between",
            left="$.agents.reviewer.outputs.score",
            right=[0.1, 0.9],
        ),
        Condition(
            op="starts_with",
            left="$.agents.reviewer.outputs.text",
            right="hello",
        ),
        Condition(
            op="ends_with",
            left="$.agents.reviewer.outputs.text",
            right="world",
        ),
        Condition(
            op="matches",
            left="$.agents.reviewer.outputs.text",
            right=r"^hello\s",
        ),
        Condition(op="empty", left="$.agents.reviewer.outputs.empty"),
        Condition(op="not_empty", left="$.agents.reviewer.outputs.text"),
        Condition(
            op="length_eq",
            left="$.agents.reviewer.outputs.items",
            right=2,
        ),
        Condition(
            op="contains_all",
            left="$.agents.reviewer.outputs.items",
            right=["alpha", "beta"],
        ),
        Condition(
            op="contains_any",
            left="$.agents.reviewer.outputs.items",
            right=["missing", "beta"],
        ),
    ]

    for condition in conditions:
        assert validate_condition(condition, references) == []
        assert evaluate_condition(condition, root)


def test_condition_matches_rejects_unsafe_regex() -> None:
    references = ConditionReferences(
        agents={"reviewer"},
        agent_ports={("reviewer", "text")},
        workflow_inputs=set(),
        edges=set(),
        run_fields={"step"},
    )
    condition = Condition(
        op="matches",
        left="$.agents.reviewer.outputs.text",
        right="(hello)",
    )

    errors = validate_condition(condition, references)

    assert any("groups, lookarounds" in error for error in errors)


def test_empty_condition_treats_missing_reference_as_empty() -> None:
    references = ConditionReferences(
        agents={"reviewer"},
        agent_ports={("reviewer", "text")},
        workflow_inputs=set(),
        edges=set(),
        run_fields={"step"},
    )
    empty = Condition(op="empty", left="$.agents.reviewer.outputs.text")
    not_empty = Condition(
        op="not_empty",
        left="$.agents.reviewer.outputs.text",
    )
    root = {"agents": {"reviewer": {"outputs": {}}}}

    assert validate_condition(empty, references) == []
    assert validate_condition(not_empty, references) == []
    assert evaluate_condition(empty, root)
    assert not evaluate_condition(not_empty, root)


def test_port_type_mismatch_is_rejected(
    legacy_workflow: Callable[[str], SDL],
) -> None:
    sdl = legacy_workflow()
    sdl.edges[0].mappings[0].from_ = "outline"

    result = validate_sdl(sdl)

    assert not result.valid
    assert "port_type_mismatch" in {issue.code for issue in result.issues}


def test_json_output_template_rejects_structured_text_port(
    legacy_workflow: Callable[[str], SDL],
) -> None:
    sdl = legacy_workflow()
    sdl.agents[1].json_output.template["review"] = {
        "style": "画风修改建议",
        "background": "背景修改建议",
    }

    result = validate_sdl(sdl)

    assert not result.valid
    issue = next(
        issue
        for issue in result.issues
        if issue.code == "json_output_port_type_mismatch"
    )
    assert issue.node_id == "reviewer"
    assert issue.path == "agents.reviewer.json_output.template.review"


def test_json_output_template_accepts_string_text_port(
    legacy_workflow: Callable[[str], SDL],
) -> None:
    sdl = legacy_workflow()
    sdl.agents[1].json_output.template["review"] = (
        "综合角色、背景、风格和质量维度的完整修改建议文本"
    )

    result = validate_sdl(sdl)

    assert result.valid
    assert result.issues == []


def test_json_output_template_rejects_non_reference_file_port(
    legacy_workflow: Callable[[str], SDL],
) -> None:
    sdl = legacy_workflow()
    sdl.agents[1].outputs.append(Port(name="picture", type="FILE"))
    sdl.agents[1].json_output.template["picture"] = "生成的图片路径"

    result = validate_sdl(sdl)

    assert not result.valid
    issue = next(
        issue
        for issue in result.issues
        if issue.code == "json_output_port_type_mismatch"
    )
    assert issue.node_id == "reviewer"
    assert issue.path == "agents.reviewer.json_output.template.picture"


def test_image_agent_requires_capability_file_output_and_chat_json_disabled(
    legacy_workflow: Callable[[str], SDL],
) -> None:
    sdl = legacy_workflow()
    agent = sdl.agents[0]
    agent.mode = "image"
    agent.outputs = []
    agent.json_output.enabled = True

    result = validate_sdl(sdl)

    assert not result.valid
    assert {issue.code for issue in result.issues} >= {
        "image_profile_required",
        "image_output_contract",
        "image_json_output",
    }


def test_image_agent_accepts_compatible_profile_and_file_output(
    legacy_workflow: Callable[[str], SDL],
) -> None:
    sdl = legacy_workflow()
    profile = sdl.api_profiles[0]
    profile.capabilities.image_generation = True
    profile.image_generation = ImageGenerationConfig(size="1024x1024")
    assert profile.image_generation.protocol == "automatic1111"
    agent = sdl.agents[0]
    agent.mode = "image"
    agent.outputs = [Port(name="picture", type="FILE")]
    agent.json_output.enabled = False
    sdl.edges.clear()
    sdl.agents = [agent]
    sdl.workflow.outputs = [
        WorkflowOutput(name="picture", agent=agent.id, port="picture")
    ]

    result = validate_sdl(sdl)

    assert result.valid
    assert result.issues == []
