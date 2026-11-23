from __future__ import annotations

import asyncio
import json
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

import pytest
from fastapi.testclient import TestClient

from loomlane.main import create_app
from loomlane.models import (
    CanvasLayout,
    EdgeType,
    PortType,
    RunResumeRequest,
    RunStartRequest,
    SDL,
)
from loomlane.runtime import RunManager, RunResumeConflict
from loomlane.storage import ProjectStore


class MemoryCredentials:
    def get(self, project_id: str, profile_id: str, base_url: str) -> str | None:
        return None

    def get_library(self, profile_id: str, base_url: str) -> str | None:
        return None

    def status(self, project_id: str, profile_id: str, base_url: str):
        from loomlane.models import SecretStatus

        return SecretStatus(
            profile_id=profile_id,
            configured=False,
            origin=base_url,
        )

    def put(
        self,
        project_id: str,
        profile_id: str,
        api_key: str,
        base_url: str,
    ):
        raise AssertionError("not used")

    def delete(self, project_id: str, profile_id: str) -> None:
        return None


class ResumeOpenAIHandler(BaseHTTPRequestHandler):
    responder: Callable[[dict[str, Any]], Any] | None = None
    calls: list[dict[str, Any]] = []

    def log_message(self, format: str, *args: Any) -> None:
        return

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).calls.append(body)
        responder = type(self).responder
        assert responder is not None
        result = responder(body)
        outputs, usage = result if isinstance(result, tuple) else (result, None)
        payload = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(outputs, ensure_ascii=False)
                    }
                }
            ],
            "usage": usage
            or {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
            },
        }
        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


@pytest.fixture
def openai_server():
    ResumeOpenAIHandler.calls = []
    ResumeOpenAIHandler.responder = None
    server = ThreadingHTTPServer(("127.0.0.1", 0), ResumeOpenAIHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield (
            f"http://127.0.0.1:{server.server_port}/v1",
            ResumeOpenAIHandler,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@dataclass
class RunContext:
    store: ProjectStore
    project: Any
    manager: RunManager
    summary: Any
    detail: Any

    @property
    def run_root(self) -> Path:
        return (
            self.store.project_path(self.project.id)
            / "runs"
            / self.summary.run_directory
        )


def _agent_marker(body: dict[str, Any]) -> str:
    system = body["messages"][0]["content"]
    for line in system.splitlines():
        if line.startswith("AGENT:"):
            return line.removeprefix("AGENT:").strip()
    raise AssertionError(f"missing agent marker: {system}")


def _default_responder(body: dict[str, Any]) -> dict[str, str]:
    return {"value": f"{_agent_marker(body)}-output"}


def _chain_sdl(
    base_url: str,
    *,
    count: int = 2,
    first_input_type: PortType = PortType.DATA,
    max_total_tokens: int | None = None,
) -> SDL:
    agent_ids = [chr(ord("a") + index) for index in range(count)]
    agents: list[dict[str, Any]] = []
    for index, agent_id in enumerate(agent_ids):
        input_name = "seed" if index == 0 else "value"
        input_type = first_input_type if index == 0 else PortType.DATA
        agents.append(
            {
                "id": agent_id,
                "name": agent_id.upper(),
                "api_profile": "local",
                "system_prompt": f"AGENT: {agent_id}",
                "inputs": [
                    {
                        "name": input_name,
                        "type": input_type.value,
                        "required": True,
                    }
                ],
                "outputs": [{"name": "value", "type": "DATA"}],
            }
        )
    edges = [
        {
            "id": f"{agent_ids[index]}-to-{agent_ids[index + 1]}",
            "from": agent_ids[index],
            "to": agent_ids[index + 1],
            "type": "PIPE",
            "mappings": [{"from": "value", "to": "value"}],
        }
        for index in range(count - 1)
    ]
    return SDL.model_validate(
        {
            "sdl_version": "0.2",
            "workflow": {
                "id": "resume-test",
                "name": "resume test",
                "inputs": [
                    {
                        "name": "seed",
                        "type": first_input_type.value,
                        "required": True,
                    }
                ],
                "outputs": [
                    {
                        "name": "final",
                        "agent": agent_ids[-1],
                        "port": "value",
                    }
                ],
                "execution": {
                    "feedback_limit_default": 3,
                    "loop_limit_default": 5,
                    "max_steps": 50,
                    "max_total_tokens": max_total_tokens,
                    "max_parallel": 4,
                },
            },
            "start": [agent_ids[0]],
            "api_profiles": [
                {
                    "id": "local",
                    "name": "Local",
                    "base_url": base_url,
                    "model": "test-model",
                }
            ],
            "agents": agents,
            "edges": edges,
        }
    )


async def _wait_for_run(manager: RunManager, run_id: str):
    for _ in range(240):
        detail = await manager.get(run_id)
        if detail.status in {"succeeded", "failed", "cancelled"}:
            return detail
        await asyncio.sleep(0.05)
    raise AssertionError("run did not finish")


@asynccontextmanager
async def _run_chain(
    tmp_path: Path,
    base_url: str,
    handler: type[ResumeOpenAIHandler],
    *,
    count: int = 2,
    first_input_type: PortType = PortType.DATA,
    max_total_tokens: int | None = None,
    inputs: dict[str, Any] | None = None,
    before_start: Callable[[ProjectStore, Any, RunManager], Any] | None = None,
):
    handler.calls = []
    handler.responder = _default_responder
    store = ProjectStore(tmp_path / "projects")
    sdl = _chain_sdl(
        base_url,
        count=count,
        first_input_type=first_input_type,
        max_total_tokens=max_total_tokens,
    )
    project = store.create("resume project", sdl)
    manager = RunManager(store, MemoryCredentials())
    try:
        prepared_inputs = (
            before_start(store, project, manager)
            if before_start is not None
            else inputs
        )
        summary = await manager.start(
            project.id,
            RunStartRequest(inputs=prepared_inputs or {"seed": "start"}),
        )
        detail = await _wait_for_run(manager, summary.run_id)
        yield RunContext(store, project, manager, summary, detail)
    finally:
        await manager.shutdown()


def _checkpoint(options: Any, checkpoint_id: str):
    return next(
        item
        for item in options.checkpoints
        if item.checkpoint_id == checkpoint_id
    )


def _save_sdl(
    store: ProjectStore,
    project_id: str,
    mutate: Callable[[SDL], None],
) -> Any:
    current = store.get(project_id)
    sdl = SDL.model_validate(current.sdl.model_dump(mode="json"))
    mutate(sdl)
    return store.save(
        project_id,
        sdl,
        current.canvas,
        current.revision,
    )


@pytest.mark.asyncio
async def test_modifying_completed_upstream_agent_disables_later_checkpoint(
    tmp_path: Path,
    openai_server,
) -> None:
    base_url, handler = openai_server
    async with _run_chain(tmp_path, base_url, handler) as context:
        assert context.detail.status == "succeeded"

        _save_sdl(
            context.store,
            context.project.id,
            lambda sdl: setattr(sdl.agents[0], "system_prompt", "AGENT: a\nchanged"),
        )
        options = await context.manager.resume_options(context.summary.run_id)
        initial = _checkpoint(options, "checkpoint-0001")
        after_a = _checkpoint(options, "checkpoint-0002")

        assert initial.available is True
        assert after_a.available is False
        assert "已修改 Agent/Edge" in (after_a.disabled_reason or "")


@pytest.mark.asyncio
async def test_modifying_pending_agent_allows_resume_before_that_agent(
    tmp_path: Path,
    openai_server,
) -> None:
    base_url, handler = openai_server
    async with _run_chain(
        tmp_path,
        base_url,
        handler,
        count=3,
    ) as context:
        assert context.detail.status == "succeeded"

        _save_sdl(
            context.store,
            context.project.id,
            lambda sdl: setattr(sdl.agents[1], "system_prompt", "AGENT: b\nchanged"),
        )
        options = await context.manager.resume_options(context.summary.run_id)
        before_b = _checkpoint(options, "checkpoint-0002")
        after_b = _checkpoint(options, "checkpoint-0003")

        assert before_b.available is True
        assert before_b.pending_agent_ids == ["b"]
        assert after_b.available is False
        assert "已修改 Agent/Edge" in (after_b.disabled_reason or "")


@pytest.mark.asyncio
async def test_resume_reruns_modified_pending_agent_and_inherits_usage(
    tmp_path: Path,
    openai_server,
) -> None:
    base_url, handler = openai_server
    async with _run_chain(tmp_path, base_url, handler) as context:
        _save_sdl(
            context.store,
            context.project.id,
            lambda sdl: setattr(sdl.agents[1], "system_prompt", "AGENT: b\nchanged"),
        )
        handler.calls = []

        resumed = await context.manager.resume(
            context.project.id,
            RunResumeRequest(
                source_run_id=context.summary.run_id,
                checkpoint_id="checkpoint-0002",
            ),
        )
        detail = await _wait_for_run(context.manager, resumed.run_id)

        assert detail.status == "succeeded"
        assert detail.result["workflow_outputs"]["final"] == "b-output"
        assert detail.result["usage"]["total_tokens"] == 30
        assert [_agent_marker(body) for body in handler.calls] == ["b"]
        resumed_event = next(
            event for event in detail.events if event["type"] == "run_resumed"
        )
        assert resumed_event["data"]["inherited_usage"]["total_tokens"] == 15
        assert detail.resumed_from_run_id == context.summary.run_id
        assert detail.resume_checkpoint_id == "checkpoint-0002"


@pytest.mark.asyncio
async def test_traversed_modified_edge_disables_checkpoint(
    tmp_path: Path,
    openai_server,
) -> None:
    base_url, handler = openai_server
    async with _run_chain(tmp_path, base_url, handler) as context:
        _save_sdl(
            context.store,
            context.project.id,
            lambda sdl: setattr(
                sdl.edges[0],
                "type",
                EdgeType.CONDITIONAL,
            ),
        )
        options = await context.manager.resume_options(context.summary.run_id)

        assert _checkpoint(options, "checkpoint-0001").available is True
        after_a = _checkpoint(options, "checkpoint-0002")
        assert after_a.available is False
        assert "已遍历已修改或删除的 Edge" in (after_a.disabled_reason or "")


@pytest.mark.asyncio
async def test_new_edge_from_completed_source_disables_later_checkpoint(
    tmp_path: Path,
    openai_server,
) -> None:
    base_url, handler = openai_server
    async with _run_chain(
        tmp_path,
        base_url,
        handler,
        count=3,
    ) as context:
        def add_shortcut(sdl: SDL) -> None:
            sdl.edges.append(
                SDL.model_validate(
                    {
                        "sdl_version": "0.2",
                        "workflow": {
                            "id": "edge-template",
                            "name": "edge template",
                        },
                        "agents": [],
                        "edges": [
                            {
                                "id": "a-to-c-shortcut",
                                "from": "a",
                                "to": "c",
                                "type": "PIPE",
                                "mappings": [
                                    {"from": "value", "to": "value"}
                                ],
                            }
                        ],
                    }
                ).edges[0]
            )

        _save_sdl(context.store, context.project.id, add_shortcut)
        options = await context.manager.resume_options(context.summary.run_id)

        assert _checkpoint(options, "checkpoint-0001").available is True
        after_a = _checkpoint(options, "checkpoint-0002")
        assert after_a.available is False
        assert "已修改 Agent/Edge" in (after_a.disabled_reason or "")


@pytest.mark.asyncio
async def test_canvas_only_change_does_not_disable_checkpoints(
    tmp_path: Path,
    openai_server,
) -> None:
    base_url, handler = openai_server
    async with _run_chain(tmp_path, base_url, handler) as context:
        current = context.store.get(context.project.id)
        canvas = CanvasLayout.model_validate(
            current.canvas.model_dump(mode="json")
        )
        canvas.nodes[0].x += 40
        context.store.save(
            context.project.id,
            current.sdl,
            canvas,
            current.revision,
        )

        options = await context.manager.resume_options(context.summary.run_id)
        assert all(checkpoint.available for checkpoint in options.checkpoints)


@pytest.mark.asyncio
async def test_file_input_is_preserved_when_resuming(
    tmp_path: Path,
    openai_server,
) -> None:
    base_url, handler = openai_server

    def prepare_file(
        store: ProjectStore,
        project: Any,
        manager: RunManager,
    ) -> dict[str, Any]:
        uploaded = manager.stage_input_file(
            project.id,
            "seed",
            "source.txt",
            b"resume reference",
        )
        return {
            "seed": {
                "path": uploaded.path,
                "name": uploaded.name,
                "size": uploaded.size,
            }
        }

    async with _run_chain(
        tmp_path,
        base_url,
        handler,
        first_input_type=PortType.FILE,
        before_start=prepare_file,
    ) as context:
        assert context.summary.run_directory is not None
        resumed = await context.manager.resume(
            context.project.id,
            RunResumeRequest(
                source_run_id=context.summary.run_id,
                checkpoint_id="checkpoint-0001",
            ),
        )
        detail = await _wait_for_run(context.manager, resumed.run_id)
        resumed_root = (
            context.store.project_path(context.project.id)
            / "runs"
            / resumed.run_directory
        )

        assert detail.status == "succeeded"
        assert list((resumed_root / "workspace" / "uploads").rglob("*.txt"))


@pytest.mark.asyncio
async def test_resume_inherits_usage_and_still_enforces_token_limit(
    tmp_path: Path,
    openai_server,
) -> None:
    base_url, handler = openai_server
    async with _run_chain(
        tmp_path,
        base_url,
        handler,
        max_total_tokens=20,
    ) as context:
        assert context.detail.status == "failed"
        assert "max_total_tokens=20" in (context.detail.error or "")

        resumed = await context.manager.resume(
            context.project.id,
            RunResumeRequest(
                source_run_id=context.summary.run_id,
                checkpoint_id="checkpoint-0002",
            ),
        )
        detail = await _wait_for_run(context.manager, resumed.run_id)
        resumed_event = next(
            event for event in detail.events if event["type"] == "run_resumed"
        )

        assert detail.status == "failed"
        assert "max_total_tokens=20" in (detail.error or "")
        assert resumed_event["data"]["inherited_usage"]["total_tokens"] == 15


@pytest.mark.asyncio
async def test_missing_snapshot_and_corrupt_checkpoint_fail_closed(
    tmp_path: Path,
    openai_server,
) -> None:
    base_url, handler = openai_server
    async with _run_chain(tmp_path, base_url, handler) as context:
        (context.run_root / "workflow.snapshot.json").unlink()
        checkpoint_path = (
            context.run_root / "checkpoints" / "checkpoint-0001.json"
        )
        checkpoint_path.write_text("{not-json", encoding="utf-8")

        options = await context.manager.resume_options(context.summary.run_id)

        assert options.snapshot_available is False
        assert options.global_disabled_reason
        assert all(not checkpoint.available for checkpoint in options.checkpoints)
        corrupt = _checkpoint(options, "checkpoint-0001")
        assert "损坏" in (corrupt.disabled_reason or "")


@pytest.mark.asyncio
async def test_resume_uses_checkpoint_workspace_not_final_workspace(
    tmp_path: Path,
    openai_server,
) -> None:
    base_url, handler = openai_server
    async with _run_chain(tmp_path, base_url, handler) as context:
        assert context.summary.run_directory is not None
        late_file = context.run_root / "workspace" / "late.txt"
        late_file.write_text("must not be copied", encoding="utf-8")

        resumed = await context.manager.resume(
            context.project.id,
            RunResumeRequest(
                source_run_id=context.summary.run_id,
                checkpoint_id="checkpoint-0001",
            ),
        )
        detail = await _wait_for_run(context.manager, resumed.run_id)
        resumed_root = (
            context.store.project_path(context.project.id)
            / "runs"
            / resumed.run_directory
        )

        assert detail.status == "succeeded"
        assert not (resumed_root / "workspace" / "late.txt").exists()


@pytest.mark.asyncio
async def test_only_latest_terminal_run_and_valid_checkpoint_can_resume(
    tmp_path: Path,
    openai_server,
) -> None:
    base_url, handler = openai_server
    async with _run_chain(tmp_path, base_url, handler) as context:
        second = await context.manager.start(
            context.project.id,
            RunStartRequest(inputs={"seed": "second"}),
        )
        await _wait_for_run(context.manager, second.run_id)

        with pytest.raises(RunResumeConflict, match="most recent"):
            await context.manager.resume_options(context.summary.run_id)

        with pytest.raises(RunResumeConflict, match="not found|available"):
            await context.manager.resume(
                context.project.id,
                RunResumeRequest(
                    source_run_id=second.run_id,
                    checkpoint_id="checkpoint-9999",
                ),
            )


def _wait_for_api_run(client: TestClient, run_id: str) -> dict[str, Any]:
    for _ in range(240):
        response = client.get(f"/api/runs/{run_id}")
        assert response.status_code == 200
        detail = response.json()
        if detail["status"] in {"succeeded", "failed", "cancelled"}:
            return detail
        time.sleep(0.05)
    raise AssertionError("API run did not finish")


@pytest.mark.asyncio
async def test_resume_api_returns_options_and_accepts_checkpoint(
    tmp_path: Path,
    openai_server,
) -> None:
    base_url, handler = openai_server
    async with _run_chain(
        tmp_path,
        base_url,
        handler,
        count=3,
    ) as context:
        _save_sdl(
            context.store,
            context.project.id,
            lambda sdl: setattr(
                sdl.agents[1],
                "system_prompt",
                "AGENT: b\nchanged through API",
            ),
        )
        app = create_app(
            store=context.store,
            credentials=MemoryCredentials(),
            manager=context.manager,
        )

        with TestClient(app) as client:
            options_response = client.get(
                f"/api/runs/{context.summary.run_id}/resume-options"
            )
            assert options_response.status_code == 200
            options = options_response.json()
            before_b = next(
                item
                for item in options["checkpoints"]
                if item["checkpoint_id"] == "checkpoint-0002"
            )
            assert before_b["available"] is True
            assert before_b["pending_agent_ids"] == ["b"]

            resumed_response = client.post(
                f"/api/projects/{context.project.id}/runs/resume",
                json={
                    "source_run_id": context.summary.run_id,
                    "checkpoint_id": "checkpoint-0002",
                },
            )
            assert resumed_response.status_code == 202
            resumed = resumed_response.json()
            detail = _wait_for_api_run(client, resumed["run_id"])

        assert detail["status"] == "succeeded"
        assert detail["result"]["workflow_outputs"]["final"] == "c-output"
        assert detail["resumed_from_run_id"] == context.summary.run_id
        assert detail["resume_checkpoint_id"] == "checkpoint-0002"


@pytest.mark.asyncio
async def test_resume_api_returns_409_for_non_latest_or_unknown_checkpoint(
    tmp_path: Path,
    openai_server,
) -> None:
    base_url, handler = openai_server
    async with _run_chain(tmp_path, base_url, handler) as context:
        second = await context.manager.start(
            context.project.id,
            RunStartRequest(inputs={"seed": "second"}),
        )
        await _wait_for_run(context.manager, second.run_id)
        app = create_app(
            store=context.store,
            credentials=MemoryCredentials(),
            manager=context.manager,
        )

        with TestClient(app) as client:
            stale = client.get(
                f"/api/runs/{context.summary.run_id}/resume-options"
            )
            unknown = client.post(
                f"/api/projects/{context.project.id}/runs/resume",
                json={
                    "source_run_id": second.run_id,
                    "checkpoint_id": "checkpoint-9999",
                },
            )

        assert stale.status_code == 409
        assert "most recent" in stale.json()["detail"]
        assert unknown.status_code == 409
        assert "not found" in unknown.json()["detail"]
