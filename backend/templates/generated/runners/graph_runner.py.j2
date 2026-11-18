from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from config import SDL


class WorkflowError(RuntimeError):
    pass


class _Missing:
    pass


MISSING = _Missing()


def _now() -> str:
    return datetime.now(UTC).isoformat()


class EventWriter:
    def __init__(self) -> None:
        self.seq = 0
        self.run_id = os.getenv("LOOMLANE_RUN_ID", "run")

    def emit(
        self,
        event_type: str,
        *,
        node_id: str | None = None,
        edge_id: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> None:
        self.seq += 1
        event = {
            "seq": self.seq,
            "run_id": self.run_id,
            "ts": _now(),
            "type": event_type,
            "node_id": node_id,
            "edge_id": edge_id,
            "data": data or {},
        }
        sys.stdout.write(json.dumps(event, ensure_ascii=False) + "\n")
        sys.stdout.flush()


def _path_parts(path: str) -> list[str]:
    if not path.startswith("$."):
        raise WorkflowError(f"Invalid condition path: {path}")
    parts = path[2:].split(".")
    if any(not part for part in parts):
        raise WorkflowError(f"Invalid condition path: {path}")
    return parts


def _resolve(root: dict[str, Any], path: str) -> Any:
    current: Any = root
    for part in _path_parts(path):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return MISSING
    return current


def evaluate_condition(condition: dict[str, Any], root: dict[str, Any]) -> bool:
    op = condition["op"]
    if op == "and":
        return all(evaluate_condition(item, root) for item in condition.get("args", []))
    if op == "or":
        return any(evaluate_condition(item, root) for item in condition.get("args", []))
    if op == "not":
        return not evaluate_condition(condition["args"][0], root)

    left = _resolve(root, condition.get("left", ""))
    right = condition.get("right")
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
    raise WorkflowError(f"Unsupported condition operator: {op}")


def _profile_secret_env(profile_id: str) -> str:
    return "LOOMLANE_SECRET_" + re.sub(r"[^A-Za-z0-9]", "_", profile_id).upper()


def _is_local_url(base_url: str) -> bool:
    host = (urlparse(base_url).hostname or "").lower()
    return host in {"127.0.0.1", "localhost", "::1"}


def _extract_json(content: str) -> dict[str, Any]:
    stripped = content.strip()
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start < 0 or end <= start:
            raise WorkflowError("Agent response did not contain a JSON object")
        try:
            value = json.loads(stripped[start : end + 1])
        except json.JSONDecodeError as exc:
            raise WorkflowError(f"Agent response contained invalid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise WorkflowError("Agent response must be a JSON object")
    return value


def _validate_value(value: Any, port: dict[str, Any], workspace: Path) -> None:
    port_type = port["type"]
    if port_type == "MIXED":
        return
    if port_type == "TEXT" and not isinstance(value, str):
        raise WorkflowError(f"Port '{port['name']}' requires TEXT")
    if port_type == "DATA" and not isinstance(
        value, (dict, list, str, int, float, bool, type(None))
    ):
        raise WorkflowError(f"Port '{port['name']}' requires DATA")
    if port_type == "FILE":
        if not isinstance(value, dict) or not isinstance(value.get("path"), str):
            raise WorkflowError(f"Port '{port['name']}' requires a FILE reference")
        target = (workspace / value["path"]).resolve()
        if workspace.resolve() not in {target, *target.parents}:
            raise WorkflowError(f"File port '{port['name']}' escaped the workspace")

    hint = port.get("schema_hint")
    if not hint:
        return
    expected = hint.get("type")
    type_checks = {
        "string": (str,),
        "number": (int, float),
        "integer": (int,),
        "boolean": (bool,),
        "object": (dict,),
        "array": (list,),
    }
    if expected in type_checks and not isinstance(value, type_checks[expected]):
        raise WorkflowError(f"Port '{port['name']}' does not satisfy schema_hint")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in hint and value < hint["minimum"]:
            raise WorkflowError(f"Port '{port['name']}' is below schema minimum")
        if "maximum" in hint and value > hint["maximum"]:
            raise WorkflowError(f"Port '{port['name']}' is above schema maximum")


def _output_contract(agent: dict[str, Any]) -> str:
    parts = [f'"{port["name"]}" ({port["type"]})' for port in agent["outputs"]]
    return (
        "Return exactly one JSON object and no surrounding prose. "
        f"It must contain these keys: {', '.join(parts)}."
    )


def _system_prompt(agent: dict[str, Any]) -> str:
    global_chunk: str | None = None
    global_context = SDL["workflow"].get("global_context")
    if global_context:
        applies = global_context.get("applies_to", "all")
        applies_to_agent = applies == "all" or agent["id"] in applies
        if applies_to_agent:
            global_chunk = global_context["text"]
    chunks: list[str] = []
    if global_chunk and global_context and global_context.get("injection") != "suffix":
        chunks.append(global_chunk)
    chunks.append(agent["system_prompt"])
    if global_chunk and global_context and global_context.get("injection") == "suffix":
        chunks.append(global_chunk)
    chunks.append(_output_contract(agent))
    return "\n\n".join(chunks)


def _call_agent(
    agent: dict[str, Any],
    profile: dict[str, Any],
    inputs: dict[str, Any],
    events: EventWriter,
) -> tuple[dict[str, Any], dict[str, int]]:
    api_key = os.getenv(_profile_secret_env(profile["id"]), "")
    if not api_key and not _is_local_url(profile["base_url"]):
        raise WorkflowError(
            f"API credential is not configured for profile '{profile['id']}'"
        )

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    request_config = profile.get("request", {})
    body: dict[str, Any] = {
        "model": profile["model"],
        "messages": [
            {"role": "system", "content": _system_prompt(agent)},
            {
                "role": "user",
                "content": json.dumps(inputs, ensure_ascii=False, indent=2),
            },
        ],
        "temperature": request_config.get("temperature", 0.2),
    }
    max_tokens = agent.get("limits", {}).get("max_tokens")
    if max_tokens:
        body["max_tokens"] = max_tokens
    if profile.get("capabilities", {}).get("structured_output"):
        body["response_format"] = {"type": "json_object"}

    url = profile["base_url"].rstrip("/") + "/chat/completions"
    retries = int(request_config.get("max_retries", 2))
    timeout = float(request_config.get("timeout_seconds", 60))
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            with httpx.Client(timeout=timeout, follow_redirects=False) as client:
                response = client.post(url, headers=headers, json=body)
        except httpx.TransportError as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(min(2**attempt, 5))
                continue
            break

        if response.status_code == 429 or response.status_code >= 500:
            last_error = httpx.HTTPStatusError(
                f"Agent service returned {response.status_code}",
                request=response.request,
                response=response,
            )
            if attempt < retries:
                time.sleep(min(2**attempt, 5))
                continue
            break
        try:
            response.raise_for_status()
            payload = response.json()
            content = payload["choices"][0]["message"]["content"]
            if not isinstance(content, str):
                raise WorkflowError("Agent response content is not text")
            outputs = _extract_json(content)
            usage = payload.get("usage") or {}
            normalized_usage = {
                "prompt_tokens": int(usage.get("prompt_tokens", 0) or 0),
                "completion_tokens": int(usage.get("completion_tokens", 0) or 0),
                "total_tokens": int(usage.get("total_tokens", 0) or 0),
            }
            events.emit("token_usage", node_id=agent["id"], data=normalized_usage)
            return outputs, normalized_usage
        except (httpx.HTTPStatusError, KeyError, TypeError, ValueError, WorkflowError) as exc:
            last_error = exc
            break
    raise WorkflowError(f"Agent request failed: {last_error}") from last_error


class WorkflowRuntime:
    def __init__(self, run_inputs: dict[str, Any], workspace: Path) -> None:
        self.run_inputs = run_inputs
        self.workspace = workspace
        self.events = EventWriter()
        self.agents = {agent["id"]: agent for agent in SDL["agents"]}
        self.profiles = {
            profile["id"]: profile for profile in SDL["api_profiles"]
        }
        self.inputs: dict[str, dict[str, Any]] = {
            agent_id: {} for agent_id in self.agents
        }
        self.dirty: dict[str, set[str]] = {
            agent_id: set() for agent_id in self.agents
        }
        self.outputs: dict[str, dict[str, Any]] = {}
        self.traversals: dict[str, int] = {
            edge["id"]: 0 for edge in SDL["edges"]
        }
        self.usage = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }
        self.step = 0
        self.current_agent: str | None = None

    def _initialize_inputs(self) -> None:
        for agent_id in SDL["start"]:
            agent = self.agents[agent_id]
            for port in agent["inputs"]:
                if port["name"] in self.run_inputs:
                    self.inputs[agent_id][port["name"]] = self.run_inputs[port["name"]]
                    self.dirty[agent_id].add(port["name"])
            for port in agent["inputs"]:
                if port.get("required", True) and port["name"] not in self.inputs[agent_id]:
                    raise WorkflowError(
                        f"Missing workflow input '{port['name']}' for '{agent_id}'"
                    )

    def _ready_agent(self) -> str | None:
        for agent in SDL["agents"]:
            agent_id = agent["id"]
            if not self.dirty[agent_id]:
                continue
            required = [
                port["name"]
                for port in agent["inputs"]
                if port.get("required", True)
            ]
            if all(name in self.inputs[agent_id] for name in required):
                return agent_id
        return None

    def _condition_root(self) -> dict[str, Any]:
        return {
            "workflow": {"inputs": self.run_inputs},
            "agents": {
                agent_id: {"outputs": outputs}
                for agent_id, outputs in self.outputs.items()
            },
            "edges": {
                edge_id: {"traversals": count}
                for edge_id, count in self.traversals.items()
            },
            "run": {"step": self.step},
        }

    def _run_agent(self, agent_id: str) -> dict[str, Any]:
        agent = self.agents[agent_id]
        profile = self.profiles[agent["api_profile"]]
        self.current_agent = agent_id
        self.dirty[agent_id].clear()
        self.events.emit("node_enter", node_id=agent_id)
        agent_outputs, usage = _call_agent(
            agent,
            profile,
            self.inputs[agent_id],
            self.events,
        )
        for port in agent["outputs"]:
            if port["name"] not in agent_outputs:
                raise WorkflowError(
                    f"Agent '{agent_id}' omitted output port '{port['name']}'"
                )
            _validate_value(agent_outputs[port["name"]], port, self.workspace)
        extra_keys = set(agent_outputs) - {port["name"] for port in agent["outputs"]}
        if extra_keys:
            raise WorkflowError(
                f"Agent '{agent_id}' returned unexpected ports: "
                + ", ".join(sorted(extra_keys))
            )
        for key, value in usage.items():
            self.usage[key] += value
        self.outputs[agent_id] = agent_outputs
        self.events.emit(
            "node_exit",
            node_id=agent_id,
            data={"status": "succeeded"},
        )
        return agent_outputs

    def _traverse_edges(self, agent_id: str, agent_outputs: dict[str, Any]) -> None:
        root = self._condition_root()
        for edge in SDL["edges"]:
            if edge["from"] != agent_id:
                continue
            condition = edge.get("condition")
            if condition is not None and not evaluate_condition(condition, root):
                continue
            if edge["type"] == "FEEDBACK":
                self.traversals[edge["id"]] += 1
                limit = edge.get("guard", {}).get("max_traversals")
                if limit is None:
                    limit = SDL["workflow"]["execution"]["feedback_limit_default"]
                if self.traversals[edge["id"]] > limit:
                    raise WorkflowError(
                        f"Feedback edge '{edge['id']}' exceeded max_traversals={limit}"
                    )

            mapped: dict[str, Any] = {}
            for mapping in edge["mappings"]:
                source_value = agent_outputs[mapping["from"]]
                self.inputs[edge["to"]][mapping["to"]] = source_value
                self.dirty[edge["to"]].add(mapping["to"])
                mapped[mapping["to"]] = source_value
            self.events.emit(
                "edge_traversed",
                edge_id=edge["id"],
                data={
                    "from": edge["from"],
                    "to": edge["to"],
                    "mapped_ports": list(mapped),
                },
            )

    def _workflow_outputs(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for output in SDL["workflow"]["outputs"]:
            agent_outputs = self.outputs.get(output["agent"])
            if agent_outputs is None or output["port"] not in agent_outputs:
                raise WorkflowError(
                    f"Workflow output '{output['name']}' was not produced"
                )
            result[output["name"]] = agent_outputs[output["port"]]
        return result

    def run(self) -> dict[str, Any]:
        self.events.emit("run_started", data={"workflow": SDL["workflow"]["id"]})
        self._initialize_inputs()
        max_steps = int(SDL["workflow"]["execution"].get("max_steps", 200))
        while True:
            agent_id = self._ready_agent()
            if agent_id is None:
                break
            if self.step >= max_steps:
                raise WorkflowError(f"Workflow exceeded max_steps={max_steps}")
            self.step += 1
            agent_outputs = self._run_agent(agent_id)
            self._traverse_edges(agent_id, agent_outputs)
        self.current_agent = None
        workflow_outputs = self._workflow_outputs()
        result = {
            "status": "succeeded",
            "workflow_outputs": workflow_outputs,
            "agent_outputs": self.outputs,
            "usage": self.usage,
            "steps": self.step,
            "edge_traversals": self.traversals,
            "error": None,
        }
        self.events.emit("run_finished", data={"status": "succeeded"})
        return result


def _write_result(path: Path, result: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument(
        "--result",
        default=os.getenv("LOOMLANE_RESULT_PATH", "result.json"),
    )
    args = parser.parse_args()
    input_path = Path(args.input)
    result_path = Path(args.result)
    workspace = Path.cwd()

    try:
        run_inputs = json.loads(input_path.read_text(encoding="utf-8"))
        runtime = WorkflowRuntime(run_inputs, workspace)
        result = runtime.run()
        result.update(
            {
                "run_id": os.getenv("LOOMLANE_RUN_ID", "run"),
                "workflow_hash": os.getenv("LOOMLANE_WORKFLOW_HASH", ""),
            }
        )
        _write_result(result_path, result)
    except Exception as exc:
        current_agent = locals().get("runtime").current_agent if "runtime" in locals() else None
        events = locals().get("runtime").events if "runtime" in locals() else EventWriter()
        events.emit(
            "error",
            node_id=current_agent,
            data={"type": exc.__class__.__name__, "message": str(exc)},
        )
        result = {
            "run_id": os.getenv("LOOMLANE_RUN_ID", "run"),
            "workflow_hash": os.getenv("LOOMLANE_WORKFLOW_HASH", ""),
            "status": "failed",
            "workflow_outputs": {},
            "agent_outputs": {},
            "usage": {},
            "steps": 0,
            "edge_traversals": {},
            "error": str(exc),
        }
        _write_result(result_path, result)
        events.emit("run_finished", data={"status": "failed", "error": str(exc)})
        raise SystemExit(1)

