from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .models import SDL


CHECKPOINT_ID_PATTERN = re.compile(r"checkpoint-\d{4,}")


@dataclass(frozen=True)
class WorkflowImpact:
    affected_agent_ids: frozenset[str]
    affected_edge_ids: frozenset[str]
    pending_blocked_agent_ids: frozenset[str] = frozenset()
    global_disabled_reason: str | None = None


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _items_by_id(items: Iterable[Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in items:
        payload = item.model_dump(mode="json", by_alias=True, exclude_none=True)
        result[str(item.id)] = payload
    return result


def _changed_ids(
    previous: dict[str, dict[str, Any]],
    current: dict[str, dict[str, Any]],
) -> set[str]:
    ids = set(previous) | set(current)
    return {
        item_id
        for item_id in ids
        if _canonical(previous.get(item_id)) != _canonical(current.get(item_id))
    }


def _downstream(
    seeds: Iterable[str],
    *edge_sets: Iterable[tuple[str, str]],
) -> set[str]:
    affected = set(seeds)
    adjacency: dict[str, set[str]] = {}
    for edges in edge_sets:
        for source, target in edges:
            adjacency.setdefault(source, set()).add(target)
    stack = list(affected)
    while stack:
        source = stack.pop()
        for target in adjacency.get(source, set()):
            if target not in affected:
                affected.add(target)
                stack.append(target)
    return affected


def _safe_nonnegative_int(value: Any, default: int = 0) -> int | None:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, float) and value.is_integer():
        parsed = int(value)
    elif isinstance(value, str):
        try:
            parsed = int(value.strip())
        except ValueError:
            return None
    else:
        return None
    return parsed if parsed >= 0 else None


def _string_list(value: Any) -> list[str] | None:
    if not isinstance(value, list):
        return None
    result: set[str] = set()
    for item in value:
        if not isinstance(item, str) or not item.strip():
            return None
        result.add(item.strip())
    return sorted(result)


def _batch_refs(value: Any) -> list[tuple[str, str]] | None:
    if not isinstance(value, list):
        return None
    result: set[tuple[str, str]] = set()
    for item in value:
        if not isinstance(item, dict):
            return None
        agent_id = item.get("agent_id")
        batch_id = item.get("batch_id")
        if (
            not isinstance(agent_id, str)
            or not agent_id.strip()
            or not isinstance(batch_id, str)
            or not batch_id.strip()
        ):
            return None
        result.add((agent_id.strip(), batch_id.strip()))
    return sorted(result)


def _pending_refs(value: Any) -> list[tuple[str, str]] | None:
    if not isinstance(value, list):
        return None
    result: list[tuple[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            return None
        agent_id = item.get("agent_id")
        batch_id = item.get("batch_id")
        inputs = item.get("inputs")
        if (
            not isinstance(agent_id, str)
            or not agent_id.strip()
            or not isinstance(batch_id, str)
            or not batch_id.strip()
            or not isinstance(inputs, dict)
        ):
            return None
        result.append((agent_id.strip(), batch_id.strip()))
    return result


def _aggregate_arrivals(
    value: Any,
) -> list[dict[str, Any]] | None:
    if not isinstance(value, list):
        return None
    result: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            return None
        agent_id = item.get("agent_id")
        batch_id = item.get("batch_id")
        edge_ids = _string_list(item.get("edge_ids"))
        if (
            not isinstance(agent_id, str)
            or not agent_id.strip()
            or not isinstance(batch_id, str)
            or not batch_id.strip()
            or edge_ids is None
        ):
            return None
        result.append(
            {
                "agent_id": agent_id.strip(),
                "batch_id": batch_id.strip(),
                "edge_ids": edge_ids,
            }
        )
    return result


def _traversals(value: Any) -> dict[str, int] | None:
    if not isinstance(value, dict):
        return None
    result: dict[str, int] = {}
    for edge_id, raw_count in value.items():
        if not isinstance(edge_id, str) or not edge_id.strip():
            return None
        count = _safe_nonnegative_int(raw_count)
        if count is None:
            return None
        result[edge_id.strip()] = count
    return result


def _usage(value: Any) -> dict[str, int] | None:
    if not isinstance(value, dict):
        return None
    result: dict[str, int] = {}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        parsed = _safe_nonnegative_int(value.get(key, 0))
        if parsed is None:
            return None
        result[key] = parsed
    return result


def workflow_change_impact(previous: SDL, current: SDL) -> WorkflowImpact:
    metadata_signature = {
        "inputs": [
            item.model_dump(mode="json", by_alias=True, exclude_none=True)
            for item in previous.workflow.inputs
        ],
        "outputs": [
            item.model_dump(mode="json", by_alias=True, exclude_none=True)
            for item in previous.workflow.outputs
        ],
        "global_context": (
            previous.workflow.global_context.model_dump(
                mode="json",
                by_alias=True,
                exclude_none=True,
            )
            if previous.workflow.global_context
            else None
        ),
        "execution": previous.workflow.execution.model_dump(
            mode="json",
            by_alias=True,
            exclude_none=True,
        ),
    }
    current_metadata_signature = {
        "inputs": [
            item.model_dump(mode="json", by_alias=True, exclude_none=True)
            for item in current.workflow.inputs
        ],
        "outputs": [
            item.model_dump(mode="json", by_alias=True, exclude_none=True)
            for item in current.workflow.outputs
        ],
        "global_context": (
            current.workflow.global_context.model_dump(
                mode="json",
                by_alias=True,
                exclude_none=True,
            )
            if current.workflow.global_context
            else None
        ),
        "execution": current.workflow.execution.model_dump(
            mode="json",
            by_alias=True,
            exclude_none=True,
        ),
    }
    if _canonical(metadata_signature) != _canonical(current_metadata_signature):
        return WorkflowImpact(
            affected_agent_ids=frozenset(),
            affected_edge_ids=frozenset(),
            pending_blocked_agent_ids=frozenset(),
            global_disabled_reason=(
                "工作流输入、输出、全局上下文或执行配置已修改，所有检查点不可用"
            ),
        )

    previous_agents = _items_by_id(previous.agents)
    current_agents = _items_by_id(current.agents)
    previous_profiles = _items_by_id(previous.api_profiles)
    current_profiles = _items_by_id(current.api_profiles)
    previous_edges = _items_by_id(previous.edges)
    current_edges = _items_by_id(current.edges)

    changed_agents = _changed_ids(previous_agents, current_agents)
    seeds = set(changed_agents)
    changed_profiles = _changed_ids(previous_profiles, current_profiles)
    if changed_profiles:
        for agent_id, payload in {
            **previous_agents,
            **current_agents,
        }.items():
            if payload.get("api_profile") in changed_profiles:
                seeds.add(agent_id)
                changed_agents.add(agent_id)

    changed_edges = _changed_ids(previous_edges, current_edges)
    changed_edge_targets: set[str] = set()
    for edge_id in changed_edges:
        old_edge = previous_edges.get(edge_id)
        new_edge = current_edges.get(edge_id)
        if old_edge:
            changed_edge_targets.add(str(old_edge["to"]))
        if new_edge:
            changed_edge_targets.add(str(new_edge["to"]))
        # A new or changed edge must be evaluated again. If its source has
        # already completed, restoring after that point would never traverse
        # the edge because completed agents are not replayed.
        if new_edge and (
            old_edge is None or _canonical(old_edge) != _canonical(new_edge)
        ):
            seeds.add(str(new_edge["from"]))
    seeds.update(changed_edge_targets)

    if set(previous.start) != set(current.start):
        seeds.update(previous.start)
        seeds.update(current.start)

    old_edge_pairs = [
        (str(edge["from"]), str(edge["to"]))
        for edge in previous_edges.values()
    ]
    current_edge_pairs = [
        (str(edge["from"]), str(edge["to"]))
        for edge in current_edges.values()
    ]
    affected = _downstream(seeds, old_edge_pairs, current_edge_pairs)
    agent_pending_blocked = _downstream(
        changed_agents,
        old_edge_pairs,
        current_edge_pairs,
    ) - changed_agents
    edge_pending_blocked = _downstream(
        changed_edge_targets,
        old_edge_pairs,
        current_edge_pairs,
    )
    return WorkflowImpact(
        affected_agent_ids=frozenset(affected),
        affected_edge_ids=frozenset(changed_edges),
        pending_blocked_agent_ids=frozenset(
            agent_pending_blocked | edge_pending_blocked
        ),
    )


def checkpoint_entries(
    run_root: Path,
    source_sdl: SDL | None,
    current_sdl: SDL,
) -> tuple[list[dict[str, Any]], str | None]:
    checkpoint_dir = run_root / "checkpoints"
    if not checkpoint_dir.exists():
        return [], None

    snapshot_available = source_sdl is not None
    impact = (
        workflow_change_impact(source_sdl, current_sdl)
        if source_sdl is not None
        else WorkflowImpact(
            affected_agent_ids=frozenset(),
            affected_edge_ids=frozenset(),
            global_disabled_reason=(
                "源运行早于断点重跑能力，缺少 workflow.snapshot.json"
            ),
        )
    )
    current_names = {agent.id: agent.name for agent in current_sdl.agents}
    current_agent_ids = set(current_names)
    entries: list[dict[str, Any]] = []

    for path in sorted(checkpoint_dir.glob("checkpoint-*.json")):
        workspace_snapshot_available = (
            checkpoint_dir / f"{path.stem}.workspace"
        ).is_dir()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            payload = None
        if not isinstance(payload, dict):
            entries.append(
                {
                    "checkpoint_id": path.stem,
                    "step": 0,
                    "completed_agent_ids": [],
                    "completed_agent_names": [],
                    "pending_agent_ids": [],
                    "pending_agent_names": [],
                    "available": False,
                    "disabled_reason": "检查点文件损坏或无法读取，不能安全恢复",
                    "inherited_usage": {
                        "prompt_tokens": 0,
                        "completion_tokens": 0,
                        "total_tokens": 0,
                    },
                    "snapshot_available": snapshot_available,
                    "workspace_snapshot_available": (
                        workspace_snapshot_available
                    ),
                    "global_disabled_reason": (
                        impact.global_disabled_reason
                    ),
                }
            )
            continue

        checkpoint_id = path.stem
        metadata_error: str | None = None
        if (
            not CHECKPOINT_ID_PATTERN.fullmatch(checkpoint_id)
            or payload.get("checkpoint_id") != checkpoint_id
        ):
            metadata_error = "检查点 ID 损坏，不能安全恢复"
        if payload.get("checkpoint_version") != 1:
            metadata_error = "检查点版本不受支持，不能安全恢复"

        completed_values = _string_list(payload.get("completed_agent_ids"))
        pending_values = _string_list(payload.get("pending_agent_ids"))
        scheduled_values = _batch_refs(payload.get("scheduled_batches"))
        pending_records = _pending_refs(payload.get("pending"))
        traversals = _traversals(payload.get("traversals"))
        aggregate_arrivals = _aggregate_arrivals(
            payload.get("aggregate_arrivals")
        )
        usage = _usage(payload.get("usage"))
        step = _safe_nonnegative_int(payload.get("step"), default=-1)
        if step == -1:
            step = None

        completed = completed_values or []
        pending = pending_values or []
        scheduled = (
            sorted({agent_id for agent_id, _ in scheduled_values})
            if scheduled_values is not None
            else []
        )
        stored_names = payload.get("agent_names")
        stored_names = stored_names if isinstance(stored_names, dict) else {}

        required_state_valid = all(
            (
                completed_values is not None,
                pending_values is not None,
                scheduled_values is not None,
                pending_records is not None,
                traversals is not None,
                aggregate_arrivals is not None,
                usage is not None,
                step is not None,
                isinstance(payload.get("inputs"), dict),
                isinstance(payload.get("input_batches"), dict),
                isinstance(payload.get("outputs"), dict),
                isinstance(payload.get("agent_usage"), dict),
                isinstance(payload.get("artifacts"), list),
                isinstance(payload.get("gate_counters"), dict),
                _safe_nonnegative_int(
                    payload.get("batch_counter"),
                    default=-1,
                )
                not in (None, -1),
            )
        )
        if metadata_error is None and not required_state_valid:
            metadata_error = "检查点元数据损坏，不能安全恢复"

        if (
            completed_values is not None
            and isinstance(payload.get("outputs"), dict)
            and set(completed_values)
            != set(str(agent_id) for agent_id in payload["outputs"])
        ):
            metadata_error = (
                metadata_error
                or "检查点完成节点与产物记录不一致，不能安全恢复"
            )
        if (
            pending_values is not None
            and pending_records is not None
            and set(pending_values)
            != {agent_id for agent_id, _ in pending_records}
        ):
            metadata_error = (
                metadata_error
                or "检查点待执行节点与队列记录不一致，不能安全恢复"
            )

        def display_name(agent_id: str) -> str:
            stored_name = stored_names.get(agent_id)
            return (
                stored_name.strip()
                if isinstance(stored_name, str) and stored_name.strip()
                else current_names.get(agent_id, agent_id)
            )

        disabled_reason = metadata_error or impact.global_disabled_reason
        safe_step = step if step is not None else 0
        safe_traversals = traversals or {}
        safe_usage = usage or {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }
        if disabled_reason is None and not workspace_snapshot_available:
            disabled_reason = (
                "检查点缺少对应的 workspace 快照，不能安全恢复"
            )
        missing = sorted(
            (set(completed) | set(pending) | set(scheduled))
            - current_agent_ids
        )
        if disabled_reason is None and missing:
            disabled_reason = (
                "检查点包含当前工作流中已不存在的 Agent 或待执行任务："
                + "、".join(missing)
            )
        if disabled_reason is None:
            traversed_changes = sorted(
                edge_id
                for edge_id in impact.affected_edge_ids
                if safe_traversals.get(edge_id, 0) > 0
            )
            if traversed_changes:
                disabled_reason = (
                    "检查点已遍历已修改或删除的 Edge："
                    + "、".join(traversed_changes)
                )
        if disabled_reason is None:
            invalid = sorted(
                set(completed) & set(impact.affected_agent_ids)
            )
            if invalid:
                disabled_reason = (
                    "检查点已完成节点包含已修改 Agent/Edge 的影响对象："
                    + "、".join(display_name(agent_id) for agent_id in invalid)
                )
        if disabled_reason is None:
            unscheduled_affected = sorted(
                (
                    set(pending)
                    | (
                        set(scheduled) - set(completed)
                    )
                )
                & set(impact.pending_blocked_agent_ids)
            )
            if unscheduled_affected:
                disabled_reason = (
                    "检查点仍有已修改 Edge 后置节点或已修改 Agent 下游待执行："
                    + "、".join(
                        display_name(agent_id)
                        for agent_id in unscheduled_affected
                    )
                )
        if disabled_reason is None:
            changed_aggregate_edges = {
                edge_id
                for arrival in aggregate_arrivals or []
                for edge_id in arrival["edge_ids"]
            } & set(impact.affected_edge_ids)
            if changed_aggregate_edges:
                disabled_reason = (
                    "检查点保留了已修改 AGGREGATE Edge 的到达记录："
                    + "、".join(sorted(changed_aggregate_edges))
                )

        entries.append(
            {
                "checkpoint_id": checkpoint_id,
                "step": safe_step,
                "completed_agent_ids": completed,
                "completed_agent_names": [
                    display_name(agent_id) for agent_id in completed
                ],
                "pending_agent_ids": pending,
                "pending_agent_names": [
                    display_name(agent_id) for agent_id in pending
                ],
                "available": disabled_reason is None,
                "disabled_reason": disabled_reason,
                "inherited_usage": safe_usage,
                "snapshot_available": snapshot_available,
                "workspace_snapshot_available": (
                    workspace_snapshot_available
                ),
                "global_disabled_reason": impact.global_disabled_reason,
            }
        )
    entries.sort(key=lambda item: (item["step"], item["checkpoint_id"]), reverse=True)
    return entries, impact.global_disabled_reason
