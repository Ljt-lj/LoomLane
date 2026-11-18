from __future__ import annotations

from copy import deepcopy

from loomlane.condition import ConditionReferences, validate_condition
from loomlane.models import Condition, EdgeGuard
from loomlane.storage import default_sdl
from loomlane.validator import validate_sdl


def test_default_workflow_is_valid() -> None:
    result = validate_sdl(default_sdl())

    assert result.valid
    assert result.normalized_sdl is not None
    assert result.issues == []


def test_non_feedback_cycle_is_rejected() -> None:
    sdl = deepcopy(default_sdl())
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


def test_feedback_default_guard_and_edge_order_are_stable() -> None:
    sdl = deepcopy(default_sdl())
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


def test_feedback_must_target_an_upstream_agent() -> None:
    sdl = deepcopy(default_sdl())
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


def test_port_type_mismatch_is_rejected() -> None:
    sdl = deepcopy(default_sdl())
    sdl.edges[0].mappings[0].from_ = "outline"

    result = validate_sdl(sdl)

    assert not result.valid
    assert "port_type_mismatch" in {issue.code for issue in result.issues}
