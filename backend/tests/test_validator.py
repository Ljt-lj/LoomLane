from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy

from loomlane.condition import (
    ConditionReferences,
    evaluate_condition,
    validate_condition,
)
from loomlane.models import (
    AggregateConfig,
    Condition,
    EdgeGuard,
    EdgeType,
    SDL,
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


def test_port_type_mismatch_is_rejected(
    legacy_workflow: Callable[[str], SDL],
) -> None:
    sdl = legacy_workflow()
    sdl.edges[0].mappings[0].from_ = "outline"

    result = validate_sdl(sdl)

    assert not result.valid
    assert "port_type_mismatch" in {issue.code for issue in result.issues}
