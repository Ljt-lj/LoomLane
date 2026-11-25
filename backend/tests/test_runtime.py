from __future__ import annotations

import asyncio
import base64
import json
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from loomlane.compiler import WorkflowCompiler
from loomlane.models import (
    AgentJsonOutput,
    GateDecisionRequest,
    Port,
    PortType,
    RunStartRequest,
    SDL,
    WorkflowInput,
)
from loomlane.runtime import GateConflict, RunManager
from loomlane.storage import ProjectStore


def _read_bytes(path: Path) -> bytes:
    value = str(path)
    if os.name == "nt" and not value.startswith("\\\\?\\"):
        resolved = str(path.resolve())
        value = (
            "\\\\?\\UNC\\" + resolved[2:]
            if resolved.startswith("\\\\")
            else "\\\\?\\" + resolved
        )
    return Path(value).read_bytes()


class MemoryCredentials:
    def get(self, project_id: str, profile_id: str, base_url: str) -> str | None:
        return None

    def get_library(self, profile_id: str, base_url: str) -> str | None:
        return None

    def status(self, project_id: str, profile_id: str, base_url: str):
        from loomlane.models import SecretStatus

        return SecretStatus(profile_id=profile_id, configured=False, origin=base_url)

    def put(self, project_id: str, profile_id: str, api_key: str, base_url: str):
        raise AssertionError("not used")

    def delete(self, project_id: str, profile_id: str) -> None:
        return None


class LibraryMemoryCredentials(MemoryCredentials):
    def get_library(self, profile_id: str, base_url: str) -> str | None:
        assert profile_id == "deepseek-flash"
        return "library-secret"


class FakeOpenAIHandler(BaseHTTPRequestHandler):
    calls: list[dict[str, Any]] = []
    reviewer_calls = 0
    forced_status: int | None = None
    forced_finish_reason: str | None = None

    def log_message(self, format: str, *args: Any) -> None:
        return

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).calls.append(body)
        if type(self).forced_status is not None:
            self.send_response(type(self).forced_status)
            self.end_headers()
            return

        system = body["messages"][0]["content"]
        if "审查初稿" in system:
            type(self).reviewer_calls += 1
            score = 0.72 if type(self).reviewer_calls == 1 else 0.91
            outputs = {
                "review": "补充来源并收紧结论" if score < 0.8 else "可以发布",
                "score": score,
            }
        else:
            outputs = {
                "draft": "第二版草稿" if "feedback" in body["messages"][1]["content"] else "第一版草稿",
                "outline": {"sections": ["intro", "body"]},
            }
        payload = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(outputs, ensure_ascii=False)
                    },
                    "finish_reason": type(self).forced_finish_reason or "stop",
                }
            ],
            "usage": {
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


class ScriptedOpenAIHandler(BaseHTTPRequestHandler):
    responder: Any = None
    calls: list[dict[str, Any]] = []
    image_calls: list[dict[str, Any]] = []
    image_paths: list[str] = []
    image_response: dict[str, Any] | None = None
    image_status: int = 200
    active = 0
    max_active = 0
    lock = threading.Lock()

    def log_message(self, format: str, *args: Any) -> None:
        return

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path.endswith(
            ("/images/generations", "/sdapi/v1/txt2img")
        ):
            with type(self).lock:
                type(self).image_calls.append(body)
                type(self).image_paths.append(self.path)
                type(self).active += 1
                type(self).max_active = max(
                    type(self).max_active,
                    type(self).active,
                )
            try:
                payload = type(self).image_response or {
                    "data": [{"b64_json": ""}]
                }
                encoded = json.dumps(payload).encode("utf-8")
                self.send_response(type(self).image_status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)
            finally:
                with type(self).lock:
                    type(self).active -= 1
            return

        with type(self).lock:
            type(self).calls.append(body)
            type(self).active += 1
            type(self).max_active = max(type(self).max_active, type(self).active)
        try:
            result = type(self).responder(body)
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
        finally:
            with type(self).lock:
                type(self).active -= 1


@pytest.fixture
def fake_openai() -> str:
    FakeOpenAIHandler.calls = []
    FakeOpenAIHandler.reviewer_calls = 0
    FakeOpenAIHandler.forced_status = None
    FakeOpenAIHandler.forced_finish_reason = None
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeOpenAIHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.fixture
def scripted_openai():
    ScriptedOpenAIHandler.calls = []
    ScriptedOpenAIHandler.image_calls = []
    ScriptedOpenAIHandler.image_paths = []
    ScriptedOpenAIHandler.image_response = None
    ScriptedOpenAIHandler.image_status = 200
    ScriptedOpenAIHandler.active = 0
    ScriptedOpenAIHandler.max_active = 0
    ScriptedOpenAIHandler.responder = None
    server = ThreadingHTTPServer(("127.0.0.1", 0), ScriptedOpenAIHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def configure(responder: Any) -> None:
        ScriptedOpenAIHandler.responder = responder

    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", configure
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


async def _wait_for_run(manager: RunManager, run_id: str):
    for _ in range(200):
        detail = await manager.get(run_id)
        if detail.status in {"succeeded", "failed", "cancelled"}:
            return detail
        await asyncio.sleep(0.05)
    raise AssertionError("run did not finish")


async def _wait_until(predicate: Any, timeout: float = 10) -> Any:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = await predicate()
        if value:
            return value
        await asyncio.sleep(0.05)
    raise AssertionError("condition was not reached")


def _agent_marker(body: dict[str, Any]) -> str:
    system = body["messages"][0]["content"]
    for line in system.splitlines():
        if line.startswith("AGENT:"):
            return line.removeprefix("AGENT:").strip()
    raise AssertionError(f"missing agent marker: {system}")


def _agent_inputs(body: dict[str, Any]) -> dict[str, Any]:
    return json.loads(body["messages"][1]["content"])


def _graph_sdl(
    base_url: str,
    *,
    agents: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    start: list[str],
    workflow_inputs: list[dict[str, Any]] | None = None,
    workflow_outputs: list[dict[str, Any]] | None = None,
    execution: dict[str, Any] | None = None,
    api_profiles: list[dict[str, Any]] | None = None,
) -> SDL:
    return SDL.model_validate(
        {
            "sdl_version": "0.2",
            "workflow": {
                "id": "v02-test",
                "name": "v0.2 test",
                "inputs": workflow_inputs or [],
                "outputs": workflow_outputs or [],
                "execution": {
                    "feedback_limit_default": 3,
                    "loop_limit_default": 5,
                    "max_steps": 50,
                    "max_total_tokens": 100_000,
                    "max_parallel": 4,
                    **(execution or {}),
                },
            },
            "start": start,
            "api_profiles": api_profiles
            or [
                {
                    "id": "local",
                    "name": "Local",
                    "base_url": base_url,
                    "model": "test-model",
                }
            ],
            "agents": [
                {
                    "api_profile": "local",
                    "limits": {
                        "max_tokens": None,
                        "max_total_tokens": None,
                    },
                    "json_output": {"enabled": False, "template": {}},
                    **agent,
                }
                for agent in agents
            ],
            "edges": edges,
        }
    )


def _image_sdl(
    base_url: str,
    *,
    protocol: str | None = "openai_images",
    response_format: str = "b64_json",
    size: str = "512x512",
    model: str = "image-test-model",
) -> SDL:
    return _graph_sdl(
        base_url,
        agents=[
            {
                "id": "painter",
                "name": "Painter",
                "api_profile": "local",
                "mode": "image",
                "system_prompt": "Draw a polished editorial illustration.",
                "inputs": [{"name": "brief", "type": "TEXT", "required": True}],
                "outputs": [{"name": "picture", "type": "FILE"}],
            }
        ],
        edges=[],
        start=["painter"],
        workflow_inputs=[
            {"name": "brief", "type": "TEXT", "required": True}
        ],
        workflow_outputs=[
            {"name": "picture", "agent": "painter", "port": "picture"}
        ],
        api_profiles=[
            {
                "id": "local",
                "name": "Image service",
                "base_url": base_url,
                "model": model,
                "capabilities": {"image_generation": True},
                "image_generation": {
                    **({"protocol": protocol} if protocol is not None else {}),
                    "size": size,
                    "quality": "high",
                    "output_format": "png",
                    "response_format": response_format,
                },
            }
        ],
    )


@pytest.mark.asyncio
async def test_feedback_run_end_to_end(
    tmp_path: Path,
    fake_openai: str,
    monkeypatch: pytest.MonkeyPatch,
    legacy_workflow: Callable[[str], SDL],
) -> None:
    store = ProjectStore(tmp_path / "projects")
    sdl = legacy_workflow(fake_openai)
    project = store.create("feedback test", sdl)
    manager = RunManager(store, MemoryCredentials())

    summary = await manager.start(
        project.id,
        RunStartRequest(inputs={"requirement": "写一篇有来源的短文"}),
    )
    detail = await _wait_for_run(manager, summary.run_id)

    assert detail.status == "succeeded"
    assert detail.result["workflow_outputs"]["approved_draft"] == "第二版草稿"
    assert detail.result["edge_traversals"]["review-feedback"] == 1
    assert [event["type"] for event in detail.events][0] == "run_started"
    assert [event["type"] for event in detail.events][-1] == "run_finished"
    assert summary.run_directory is not None
    assert summary.run_directory.startswith("feedback test__")
    artifacts = [
        event["data"]
        for event in detail.events
        if event["type"] == "artifact_written"
    ]
    assert artifacts
    run_root = store.project_path(project.id) / "runs" / summary.run_directory
    assert all((run_root / "workspace" / item["path"]).is_file() for item in artifacts)
    event_file = (
        store.project_path(project.id)
        / "runs"
        / summary.run_directory
        / "events.jsonl"
    )
    assert len(event_file.read_text(encoding="utf-8").splitlines()) == len(
        detail.events
    )
    assert any(Path(detail.output_directory).iterdir())

    launched: list[Path] = []
    monkeypatch.setattr(
        "loomlane.runtime._launch_directory",
        lambda path: launched.append(path),
    )
    opened = await manager.open_output_folder(summary.run_id)
    assert opened == detail.output_directory
    assert launched == [Path(opened)]
    await manager.shutdown()


def test_run_succeeds_on_windows_selector_event_loop(
    tmp_path: Path,
    fake_openai: str,
    legacy_workflow: Callable[[str], SDL],
) -> None:
    if os.name != "nt":
        pytest.skip("Windows SelectorEventLoop compatibility check")

    async def scenario():
        store = ProjectStore(tmp_path / "projects")
        project = store.create("selector loop", legacy_workflow(fake_openai))
        manager = RunManager(store, MemoryCredentials())
        summary = await manager.start(
            project.id,
            RunStartRequest(inputs={"requirement": "selector compatibility"}),
        )
        detail = await _wait_for_run(manager, summary.run_id)
        await manager.shutdown()
        return detail

    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        detail = runner.run(scenario())

    assert detail.status == "succeeded"
    assert any(event["type"] == "run_started" for event in detail.events)
    assert detail.events[-1]["type"] == "run_finished"


@pytest.mark.asyncio
async def test_process_start_failure_is_persisted_and_streamed(
    tmp_path: Path,
    fake_openai: str,
    legacy_workflow: Callable[[str], SDL],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ProjectStore(tmp_path / "projects")
    project = store.create("process start failure", legacy_workflow(fake_openai))
    manager = RunManager(store, MemoryCredentials())

    def fail_to_start(*args: Any, **kwargs: Any):
        raise NotImplementedError("subprocess creation unavailable")

    monkeypatch.setattr("loomlane.runtime.subprocess.Popen", fail_to_start)
    summary = await manager.start(
        project.id,
        RunStartRequest(inputs={"requirement": "trigger start failure"}),
    )
    detail = await _wait_for_run(manager, summary.run_id)

    assert detail.status == "failed"
    assert detail.error == "NotImplementedError: subprocess creation unavailable"
    assert [event["type"] for event in detail.events[-2:]] == [
        "error",
        "run_finished",
    ]
    assert detail.events[-1]["data"]["error"] == detail.error
    assert summary.run_directory is not None
    run_root = store.project_path(project.id) / "runs" / summary.run_directory
    assert "NotImplementedError" in (run_root / "stderr.log").read_text(
        encoding="utf-8"
    )
    await manager.shutdown()


@pytest.mark.asyncio
async def test_file_input_is_copied_to_readable_run_workspace(
    tmp_path: Path,
    fake_openai: str,
    legacy_workflow: Callable[[str], SDL],
) -> None:
    store = ProjectStore(tmp_path / "projects")
    sdl = legacy_workflow(fake_openai)
    sdl.workflow.inputs.append(
        WorkflowInput(name="source", type=PortType.FILE, required=True)
    )
    sdl.agents[0].inputs.append(
        Port(name="source", type=PortType.FILE, required=True)
    )
    project = store.create("file input test", sdl)
    manager = RunManager(store, MemoryCredentials())

    uploaded = manager.stage_input_file(
        project.id,
        "source",
        "source notes.txt",
        b"reference material",
    )
    source_path = store.project_path(project.id) / uploaded.path
    assert source_path.read_bytes() == b"reference material"

    summary = await manager.start(
        project.id,
        RunStartRequest(
            inputs={
                "requirement": "根据附件写一段摘要",
                "source": {
                    "path": uploaded.path,
                    "name": uploaded.name,
                    "size": uploaded.size,
                },
            }
        ),
    )
    detail = await _wait_for_run(manager, summary.run_id)

    assert detail.status == "succeeded"
    assert summary.run_directory is not None
    assert summary.run_directory.startswith("file input test__")
    run_root = (
        store.project_path(project.id)
        / "runs"
        / summary.run_directory
    )
    assert (run_root / "workspace" / uploaded.path).read_bytes() == (
        b"reference material"
    )
    assert not source_path.exists()
    await manager.shutdown()


@pytest.mark.asyncio
async def test_non_retryable_4xx_is_not_retried(
    tmp_path: Path,
    fake_openai: str,
    legacy_workflow: Callable[[str], SDL],
) -> None:
    FakeOpenAIHandler.forced_status = 400
    store = ProjectStore(tmp_path / "projects")
    sdl = legacy_workflow(fake_openai)
    project = store.create("4xx test", sdl)
    manager = RunManager(store, MemoryCredentials())

    summary = await manager.start(
        project.id,
        RunStartRequest(inputs={"requirement": "test"}),
    )
    detail = await _wait_for_run(manager, summary.run_id)

    assert detail.status == "failed"
    assert len(FakeOpenAIHandler.calls) == 1
    assert any(event["type"] == "error" for event in detail.events)
    await manager.shutdown()


@pytest.mark.asyncio
async def test_max_tokens_finish_reason_is_reported_as_truncation(
    tmp_path: Path,
    fake_openai: str,
    legacy_workflow: Callable[[str], SDL],
) -> None:
    FakeOpenAIHandler.forced_finish_reason = "length"
    store = ProjectStore(tmp_path / "projects")
    sdl = legacy_workflow(fake_openai)
    sdl.agents[0].limits.max_tokens = 1_600
    project = store.create("truncation test", sdl)
    manager = RunManager(store, MemoryCredentials())

    summary = await manager.start(
        project.id,
        RunStartRequest(inputs={"requirement": "test"}),
    )
    detail = await _wait_for_run(manager, summary.run_id)

    assert detail.status == "failed"
    error_event = next(event for event in detail.events if event["type"] == "error")
    message = error_event["data"]["message"]
    assert "max_tokens was reached" in message
    assert "completion_tokens=5" in message
    assert "max_tokens=1600" in message
    assert "invalid JSON" not in message
    await manager.shutdown()


@pytest.mark.asyncio
async def test_agent_json_output_enables_response_format(
    tmp_path: Path,
    fake_openai: str,
    legacy_workflow: Callable[[str], SDL],
) -> None:
    store = ProjectStore(tmp_path / "projects")
    sdl = legacy_workflow(fake_openai)
    sdl.api_profiles[0].capabilities.structured_output = False
    for agent in sdl.agents:
        agent.json_output.enabled = False
    sdl.agents[0].json_output = AgentJsonOutput(
        enabled=True,
        template={
            "draft": "完整的 Markdown 草稿文本",
            "outline": {"sections": "章节标题字符串数组"},
        },
    )
    project = store.create("json output test", sdl)
    manager = RunManager(store, MemoryCredentials())

    summary = await manager.start(
        project.id,
        RunStartRequest(inputs={"requirement": "test"}),
    )
    detail = await _wait_for_run(manager, summary.run_id)

    assert detail.status == "succeeded"
    drafter_calls = [
        call
        for call in FakeOpenAIHandler.calls
        if "Use the following JSON template" in call["messages"][0]["content"]
    ]
    reviewer_calls = [
        call
        for call in FakeOpenAIHandler.calls
        if "审查初稿" in call["messages"][0]["content"]
    ]
    assert len(drafter_calls) == 2
    assert all(call["response_format"] == {"type": "json_object"} for call in drafter_calls)
    assert all("response_format" not in call for call in reviewer_calls)
    assert '"sections": "章节标题字符串数组"' in drafter_calls[0]["messages"][0][
        "content"
    ]
    await manager.shutdown()


@pytest.mark.asyncio
async def test_image_agent_saves_base64_image_and_file_artifact(
    tmp_path: Path,
    scripted_openai,
) -> None:
    base_url, _ = scripted_openai
    image_bytes = b"\x89PNG\r\n\x1a\nimage-test"
    ScriptedOpenAIHandler.image_response = {
        "data": [{"b64_json": base64.b64encode(image_bytes).decode("ascii")}],
        "usage": {
            "prompt_tokens": 12,
            "completion_tokens": 0,
            "total_tokens": 12,
        },
    }
    store = ProjectStore(tmp_path / "projects")
    project = store.create("image generation", _image_sdl(base_url))
    manager = RunManager(store, MemoryCredentials())

    summary = await manager.start(
        project.id,
        RunStartRequest(inputs={"brief": "a quiet editorial cover"}),
    )
    detail = await _wait_for_run(manager, summary.run_id)

    assert detail.status == "succeeded"
    assert len(ScriptedOpenAIHandler.calls) == 0
    assert len(ScriptedOpenAIHandler.image_calls) == 1
    request = ScriptedOpenAIHandler.image_calls[0]
    assert request["model"] == "image-test-model"
    assert request["size"] == "512x512"
    assert request["quality"] == "high"
    assert request["output_format"] == "png"
    assert request["response_format"] == "b64_json"
    assert "a quiet editorial cover" in request["prompt"]
    assert "temperature" not in request
    assert "max_tokens" not in request

    reference = detail.result["workflow_outputs"]["picture"]
    assert reference["media_type"] == "image/png"
    assert reference["path"].startswith("generated/images/painter/")
    assert reference["path"].endswith(".png")
    assert summary.run_directory is not None
    run_workspace = (
        store.project_path(project.id)
        / "runs"
        / summary.run_directory
        / "workspace"
    )
    assert _read_bytes(run_workspace / reference["path"]) == image_bytes
    artifact = next(
        event["data"]
        for event in detail.events
        if event["type"] == "artifact_written"
    )
    assert artifact["port"] == "picture"
    assert artifact["type"] == "FILE"
    assert _read_bytes(run_workspace / artifact["path"]) == image_bytes
    await manager.shutdown()


@pytest.mark.asyncio
async def test_chat_agent_normalizes_file_path_string_to_reference(
    tmp_path: Path,
    scripted_openai,
) -> None:
    base_url, configure = scripted_openai
    image_bytes = b"\x89PNG\r\n\x1a\npassthrough-test"
    ScriptedOpenAIHandler.image_response = {
        "data": [{"b64_json": base64.b64encode(image_bytes).decode("ascii")}]
    }

    def respond(body: dict[str, Any]) -> dict[str, Any]:
        inputs = _agent_inputs(body)
        return {
            "picture": inputs["picture"]["path"],
            "result": "图片已转交",
        }

    configure(respond)
    sdl = _graph_sdl(
        base_url,
        agents=[
            {
                "id": "painter",
                "name": "Painter",
                "api_profile": "local",
                "mode": "image",
                "system_prompt": "Draw a polished illustration.",
                "inputs": [{"name": "brief", "type": "TEXT"}],
                "outputs": [{"name": "picture", "type": "FILE"}],
            },
            {
                "id": "reporter",
                "name": "Reporter",
                "api_profile": "local",
                "system_prompt": "Return the supplied picture path and a short report.",
                "inputs": [{"name": "picture", "type": "FILE"}],
                "outputs": [
                    {"name": "picture", "type": "FILE"},
                    {"name": "result", "type": "TEXT"},
                ],
            },
        ],
        edges=[
            {
                "id": "painter-to-reporter",
                "from": "painter",
                "to": "reporter",
                "type": "PIPE",
                "mappings": [{"from": "picture", "to": "picture"}],
            }
        ],
        start=["painter"],
        workflow_inputs=[{"name": "brief", "type": "TEXT"}],
        workflow_outputs=[
            {"name": "picture", "agent": "reporter", "port": "picture"},
            {"name": "result", "agent": "reporter", "port": "result"},
        ],
        api_profiles=[
            {
                "id": "local",
                "name": "Image service",
                "base_url": base_url,
                "model": "image-test-model",
                "capabilities": {"image_generation": True},
                "image_generation": {
                    "protocol": "openai_images",
                    "size": "512x512",
                    "response_format": "b64_json",
                },
            }
        ],
    )
    store = ProjectStore(tmp_path / "projects")
    project = store.create("file passthrough", sdl)
    manager = RunManager(store, MemoryCredentials())

    summary = await manager.start(
        project.id,
        RunStartRequest(inputs={"brief": "a test image"}),
    )
    detail = await _wait_for_run(manager, summary.run_id)

    assert detail.status == "succeeded"
    painter_reference = detail.result["agent_outputs"]["painter"]["picture"]
    reporter_reference = detail.result["agent_outputs"]["reporter"]["picture"]
    assert reporter_reference == {"path": painter_reference["path"]}
    assert detail.result["workflow_outputs"]["picture"] == reporter_reference
    await manager.shutdown()


@pytest.mark.asyncio
async def test_siliconflow_image_agent_uses_protocol_specific_request_and_response(
    tmp_path: Path,
    scripted_openai,
) -> None:
    base_url, _ = scripted_openai
    image_bytes = b"\x89PNG\r\n\x1a\nsiliconflow-test"
    encoded = base64.b64encode(image_bytes).decode("ascii")
    ScriptedOpenAIHandler.image_response = {
        "images": [{"url": f"data:image/png;base64,{encoded}"}]
    }
    store = ProjectStore(tmp_path / "projects")
    project = store.create(
        "siliconflow image generation",
        _image_sdl(
            base_url,
            protocol="siliconflow",
            size="1024\u00d71024",
            model="Kwai-Kolors/Kolors",
        ),
    )
    manager = RunManager(store, MemoryCredentials())

    summary = await manager.start(
        project.id,
        RunStartRequest(inputs={"brief": "a calm product illustration"}),
    )
    detail = await _wait_for_run(manager, summary.run_id)

    assert detail.status == "succeeded"
    assert len(ScriptedOpenAIHandler.calls) == 0
    assert len(ScriptedOpenAIHandler.image_calls) == 1
    request = ScriptedOpenAIHandler.image_calls[0]
    assert request["model"] == "Kwai-Kolors/Kolors"
    assert request["image_size"] == "1024x1024"
    assert request["batch_size"] == 1
    assert "a calm product illustration" in request["prompt"]
    for field in ("size", "quality", "output_format", "response_format"):
        assert field not in request

    reference = detail.result["workflow_outputs"]["picture"]
    assert reference["media_type"] == "image/png"
    assert reference["path"].startswith("generated/images/painter/")
    run_workspace = (
        store.project_path(project.id)
        / "runs"
        / summary.run_directory
        / "workspace"
    )
    assert _read_bytes(run_workspace / reference["path"]) == image_bytes
    artifact = next(
        event["data"]
        for event in detail.events
        if event["type"] == "artifact_written"
    )
    assert artifact["port"] == "picture"
    assert artifact["type"] == "FILE"
    await manager.shutdown()


@pytest.mark.asyncio
async def test_siliconflow_kolors_rejects_unsupported_image_size(
    tmp_path: Path,
    scripted_openai,
) -> None:
    base_url, _ = scripted_openai
    store = ProjectStore(tmp_path / "projects")
    project = store.create(
        "unsupported siliconflow image size",
        _image_sdl(
            base_url,
            protocol="siliconflow",
            size="512\u00d7512",
            model="Kwai-Kolors/Kolors",
        ),
    )
    manager = RunManager(store, MemoryCredentials())

    summary = await manager.start(
        project.id,
        RunStartRequest(inputs={"brief": "an unsupported image size"}),
    )
    detail = await _wait_for_run(manager, summary.run_id)

    assert detail.status == "failed"
    assert "does not support image size '512x512'" in detail.error
    assert len(ScriptedOpenAIHandler.image_calls) == 0
    await manager.shutdown()


@pytest.mark.asyncio
async def test_image_agent_defaults_to_automatic1111_local_txt2img_api(
    tmp_path: Path,
    scripted_openai,
) -> None:
    base_url, _ = scripted_openai
    local_base_url = base_url.removesuffix("/v1")
    image_bytes = b"\x89PNG\r\n\x1a\nautomatic1111-test"
    ScriptedOpenAIHandler.image_response = {
        "images": [base64.b64encode(image_bytes).decode("ascii")]
    }
    store = ProjectStore(tmp_path / "projects")
    project = store.create(
        "automatic1111 image generation",
        _image_sdl(local_base_url, protocol=None),
    )
    manager = RunManager(store, MemoryCredentials())

    summary = await manager.start(
        project.id,
        RunStartRequest(inputs={"brief": "a local editorial illustration"}),
    )
    detail = await _wait_for_run(manager, summary.run_id)

    assert detail.status == "succeeded"
    assert len(ScriptedOpenAIHandler.calls) == 0
    assert ScriptedOpenAIHandler.image_paths == ["/sdapi/v1/txt2img"]
    assert len(ScriptedOpenAIHandler.image_calls) == 1
    request = ScriptedOpenAIHandler.image_calls[0]
    assert request["width"] == 512
    assert request["height"] == 512
    assert request["steps"] == 28
    assert request["cfg_scale"] == 7
    assert request["sampler_name"] == "DPM++ 2M Karras"
    assert request["batch_size"] == 1
    assert "a local editorial illustration" in request["prompt"]
    for field in (
        "model",
        "size",
        "quality",
        "output_format",
        "response_format",
    ):
        assert field not in request

    reference = detail.result["workflow_outputs"]["picture"]
    assert reference["media_type"] == "image/png"
    assert reference["path"].startswith("generated/images/painter/")
    run_workspace = (
        store.project_path(project.id)
        / "runs"
        / summary.run_directory
        / "workspace"
    )
    assert _read_bytes(run_workspace / reference["path"]) == image_bytes
    artifact = next(
        event["data"]
        for event in detail.events
        if event["type"] == "artifact_written"
    )
    assert artifact["port"] == "picture"
    assert artifact["type"] == "FILE"
    await manager.shutdown()


@pytest.mark.asyncio
async def test_image_agent_rejects_non_image_bytes(
    tmp_path: Path,
    scripted_openai,
) -> None:
    base_url, _ = scripted_openai
    ScriptedOpenAIHandler.image_response = {
        "data": [
            {
                "b64_json": base64.b64encode(b"not an image").decode("ascii")
            }
        ]
    }
    store = ProjectStore(tmp_path / "projects")
    project = store.create("invalid image", _image_sdl(base_url))
    manager = RunManager(store, MemoryCredentials())

    summary = await manager.start(
        project.id,
        RunStartRequest(inputs={"brief": "invalid image"}),
    )
    detail = await _wait_for_run(manager, summary.run_id)

    assert detail.status == "failed"
    assert "not a supported PNG, JPEG, or WebP image" in detail.error
    await manager.shutdown()


@pytest.mark.asyncio
async def test_image_agent_rejects_non_public_image_url(
    tmp_path: Path,
    scripted_openai,
) -> None:
    base_url, _ = scripted_openai
    ScriptedOpenAIHandler.image_response = {
        "data": [{"url": "http://127.0.0.1/image.png"}]
    }
    store = ProjectStore(tmp_path / "projects")
    project = store.create(
        "private image url",
        _image_sdl(base_url, response_format="url"),
    )
    manager = RunManager(store, MemoryCredentials())

    summary = await manager.start(
        project.id,
        RunStartRequest(inputs={"brief": "private url"}),
    )
    detail = await _wait_for_run(manager, summary.run_id)

    assert detail.status == "failed"
    assert "non-public address" in detail.error
    await manager.shutdown()


def test_runtime_environment_uses_linked_library_credential(
    tmp_path: Path,
    legacy_workflow: Callable[[str], SDL],
) -> None:
    store = ProjectStore(tmp_path / "projects")
    sdl = legacy_workflow()
    profile = sdl.api_profiles[0]
    previous_id = profile.id
    profile.id = "deepseek-flash"
    profile.library_id = "deepseek-flash"
    for agent in sdl.agents:
        if agent.api_profile == previous_id:
            agent.api_profile = profile.id
    project = store.create("library credential test", sdl)
    manager = RunManager(store, LibraryMemoryCredentials())

    env = manager._runtime_environment(
        project,
        "run-id",
        SimpleNamespace(compiler_key="test-hash"),
        tmp_path / "run",
    )

    assert env["LOOMLANE_SECRET_DEEPSEEK_FLASH"] == "library-secret"


@pytest.mark.asyncio
async def test_loop_condition_stops_after_convergence(
    tmp_path: Path,
    scripted_openai,
) -> None:
    base_url, configure = scripted_openai

    def respond(body: dict[str, Any]) -> dict[str, Any]:
        marker = _agent_marker(body)
        value = _agent_inputs(body)["count"]
        if marker == "loop-a":
            return {"count": value + 1}
        return {"count": value, "result": f"done-{value}"}

    configure(respond)
    sdl = _graph_sdl(
        base_url,
        agents=[
            {
                "id": "a",
                "name": "A",
                "system_prompt": "AGENT: loop-a",
                "inputs": [{"name": "count", "type": "DATA", "required": True}],
                "outputs": [{"name": "count", "type": "DATA"}],
            },
            {
                "id": "b",
                "name": "B",
                "system_prompt": "AGENT: loop-b",
                "inputs": [{"name": "count", "type": "DATA", "required": True}],
                "outputs": [
                    {"name": "count", "type": "DATA"},
                    {"name": "result", "type": "TEXT"},
                ],
            },
        ],
        edges=[
            {
                "id": "a-to-b",
                "from": "a",
                "to": "b",
                "type": "PIPE",
                "mappings": [{"from": "count", "to": "count"}],
            },
            {
                "id": "b-to-a-loop",
                "from": "b",
                "to": "a",
                "type": "LOOP",
                "mappings": [{"from": "count", "to": "count"}],
                "condition": {
                    "op": "lt",
                    "left": "$.agents.b.outputs.count",
                    "right": 2,
                },
                "guard": {"max_traversals": 5},
            },
        ],
        start=["a"],
        workflow_inputs=[
            {"name": "count", "type": "DATA", "required": True}
        ],
        workflow_outputs=[
            {"name": "final", "agent": "b", "port": "result"}
        ],
    )
    store = ProjectStore(tmp_path / "projects")
    project = store.create("loop", sdl)
    manager = RunManager(store, MemoryCredentials())

    summary = await manager.start(
        project.id,
        RunStartRequest(inputs={"count": 0}),
    )
    detail = await _wait_for_run(manager, summary.run_id)

    assert detail.status == "succeeded"
    assert detail.result["workflow_outputs"]["final"] == "done-2"
    assert detail.result["edge_traversals"]["b-to-a-loop"] == 1
    assert len(
        [
            event
            for event in detail.events
            if event["type"] == "node_enter" and event["node_id"] == "a"
        ]
    ) == 2
    await manager.shutdown()


@pytest.mark.asyncio
async def test_loop_guard_fails_run(tmp_path: Path, scripted_openai) -> None:
    base_url, configure = scripted_openai
    configure(
        lambda body: (
            {"value": _agent_inputs(body)["value"] + 1}
            if _agent_marker(body) == "guard-a"
            else {"value": _agent_inputs(body)["value"]}
        )
    )
    sdl = _graph_sdl(
        base_url,
        agents=[
            {
                "id": "a",
                "name": "A",
                "system_prompt": "AGENT: guard-a",
                "inputs": [{"name": "value", "type": "DATA", "required": True}],
                "outputs": [{"name": "value", "type": "DATA"}],
            },
            {
                "id": "b",
                "name": "B",
                "system_prompt": "AGENT: guard-b",
                "inputs": [{"name": "value", "type": "DATA", "required": True}],
                "outputs": [{"name": "value", "type": "DATA"}],
            },
        ],
        edges=[
            {
                "id": "a-to-b",
                "from": "a",
                "to": "b",
                "type": "PIPE",
                "mappings": [{"from": "value", "to": "value"}],
            },
            {
                "id": "guard-loop",
                "from": "b",
                "to": "a",
                "type": "LOOP",
                "mappings": [{"from": "value", "to": "value"}],
                "condition": {
                    "op": "gte",
                    "left": "$.agents.b.outputs.value",
                    "right": 0,
                },
                "guard": {"max_traversals": 1},
            },
        ],
        start=["a"],
        workflow_inputs=[
            {"name": "value", "type": "DATA", "required": True}
        ],
        workflow_outputs=[
            {"name": "final", "agent": "b", "port": "value"}
        ],
    )
    store = ProjectStore(tmp_path / "projects")
    project = store.create("guard", sdl)
    manager = RunManager(store, MemoryCredentials())

    summary = await manager.start(
        project.id,
        RunStartRequest(inputs={"value": 0}),
    )
    detail = await _wait_for_run(manager, summary.run_id)

    assert detail.status == "failed"
    assert "exceeded max_traversals=1" in detail.error
    await manager.shutdown()


@pytest.mark.asyncio
async def test_fan_out_runs_agents_in_parallel_and_aggregate_waits_for_all(
    tmp_path: Path,
    scripted_openai,
) -> None:
    base_url, configure = scripted_openai
    join_calls = 0
    count_lock = threading.Lock()

    def respond(body: dict[str, Any]) -> dict[str, Any]:
        nonlocal join_calls
        marker = _agent_marker(body)
        if marker == "split":
            return {"seed": "seed"}
        if marker in {"left", "right"}:
            time.sleep(0.25)
            return {"token": f"{marker}-token"}
        with count_lock:
            join_calls += 1
        inputs = _agent_inputs(body)
        return {"summary": f"{inputs['left']}+{inputs['right']}"}

    configure(respond)
    sdl = _graph_sdl(
        base_url,
        agents=[
            {
                "id": "split",
                "name": "Split",
                "system_prompt": "AGENT: split",
                "inputs": [{"name": "seed", "type": "DATA", "required": True}],
                "outputs": [{"name": "seed", "type": "DATA"}],
            },
            {
                "id": "left",
                "name": "Left",
                "system_prompt": "AGENT: left",
                "inputs": [{"name": "seed", "type": "DATA", "required": True}],
                "outputs": [{"name": "token", "type": "DATA"}],
            },
            {
                "id": "right",
                "name": "Right",
                "system_prompt": "AGENT: right",
                "inputs": [{"name": "seed", "type": "DATA", "required": True}],
                "outputs": [{"name": "token", "type": "DATA"}],
            },
            {
                "id": "join",
                "name": "Join",
                "system_prompt": "AGENT: join",
                "inputs": [
                    {"name": "left", "type": "DATA", "required": True},
                    {"name": "right", "type": "DATA", "required": True},
                ],
                "outputs": [{"name": "summary", "type": "TEXT"}],
            },
        ],
        edges=[
            {
                "id": "fan-left",
                "from": "split",
                "to": "left",
                "type": "FAN_OUT",
                "mappings": [{"from": "seed", "to": "seed"}],
            },
            {
                "id": "fan-right",
                "from": "split",
                "to": "right",
                "type": "FAN_OUT",
                "mappings": [{"from": "seed", "to": "seed"}],
            },
            {
                "id": "aggregate-left",
                "from": "left",
                "to": "join",
                "type": "AGGREGATE",
                "aggregate": {"mode": "all"},
                "mappings": [{"from": "token", "to": "left"}],
            },
            {
                "id": "aggregate-right",
                "from": "right",
                "to": "join",
                "type": "AGGREGATE",
                "aggregate": {"mode": "all"},
                "mappings": [{"from": "token", "to": "right"}],
            },
        ],
        start=["split"],
        workflow_inputs=[
            {"name": "seed", "type": "DATA", "required": True}
        ],
        workflow_outputs=[
            {"name": "summary", "agent": "join", "port": "summary"}
        ],
        execution={"max_parallel": 2},
    )
    store = ProjectStore(tmp_path / "projects")
    project = store.create("fanout", sdl)
    manager = RunManager(store, MemoryCredentials())

    summary = await manager.start(
        project.id,
        RunStartRequest(inputs={"seed": "start"}),
    )
    detail = await _wait_for_run(manager, summary.run_id)

    assert detail.status == "succeeded"
    assert detail.result["workflow_outputs"]["summary"] == "left-token+right-token"
    assert ScriptedOpenAIHandler.max_active >= 2
    assert join_calls == 1
    fanout_batches = {
        event["data"]["batch_id"]
        for event in detail.events
        if event["type"] == "edge_traversed"
        and event["edge_id"] in {"fan-left", "fan-right"}
    }
    assert len(fanout_batches) == 1
    await manager.shutdown()


@pytest.mark.asyncio
async def test_aggregate_any_executes_once_per_batch(
    tmp_path: Path,
    scripted_openai,
) -> None:
    base_url, configure = scripted_openai
    join_calls = 0

    def respond(body: dict[str, Any]) -> dict[str, Any]:
        nonlocal join_calls
        marker = _agent_marker(body)
        if marker == "split":
            return {"seed": "seed"}
        if marker == "left":
            time.sleep(0.05)
            return {"token": "left-token"}
        if marker == "right":
            time.sleep(0.3)
            return {"token": "right-token"}
        join_calls += 1
        return {"summary": _agent_inputs(body)["token"]}

    configure(respond)
    sdl = _graph_sdl(
        base_url,
        agents=[
            {
                "id": "split",
                "name": "Split",
                "system_prompt": "AGENT: split",
                "inputs": [{"name": "seed", "type": "DATA", "required": True}],
                "outputs": [{"name": "seed", "type": "DATA"}],
            },
            {
                "id": "left",
                "name": "Left",
                "system_prompt": "AGENT: left",
                "inputs": [{"name": "seed", "type": "DATA", "required": True}],
                "outputs": [{"name": "token", "type": "DATA"}],
            },
            {
                "id": "right",
                "name": "Right",
                "system_prompt": "AGENT: right",
                "inputs": [{"name": "seed", "type": "DATA", "required": True}],
                "outputs": [{"name": "token", "type": "DATA"}],
            },
            {
                "id": "join",
                "name": "Join",
                "system_prompt": "AGENT: join",
                "inputs": [{"name": "token", "type": "DATA", "required": True}],
                "outputs": [{"name": "summary", "type": "TEXT"}],
            },
        ],
        edges=[
            {
                "id": "fan-left",
                "from": "split",
                "to": "left",
                "type": "FAN_OUT",
                "mappings": [{"from": "seed", "to": "seed"}],
            },
            {
                "id": "fan-right",
                "from": "split",
                "to": "right",
                "type": "FAN_OUT",
                "mappings": [{"from": "seed", "to": "seed"}],
            },
            {
                "id": "any-left",
                "from": "left",
                "to": "join",
                "type": "AGGREGATE",
                "aggregate": {"mode": "any"},
                "mappings": [{"from": "token", "to": "token"}],
            },
            {
                "id": "any-right",
                "from": "right",
                "to": "join",
                "type": "AGGREGATE",
                "aggregate": {"mode": "any"},
                "mappings": [{"from": "token", "to": "token"}],
            },
        ],
        start=["split"],
        workflow_inputs=[
            {"name": "seed", "type": "DATA", "required": True}
        ],
        workflow_outputs=[
            {"name": "summary", "agent": "join", "port": "summary"}
        ],
        execution={"max_parallel": 2},
    )
    store = ProjectStore(tmp_path / "projects")
    project = store.create("aggregate-any", sdl)
    manager = RunManager(store, MemoryCredentials())

    summary = await manager.start(
        project.id,
        RunStartRequest(inputs={"seed": "start"}),
    )
    detail = await _wait_for_run(manager, summary.run_id)

    assert detail.status == "succeeded"
    assert join_calls == 1
    assert detail.result["workflow_outputs"]["summary"] == "left-token"
    await manager.shutdown()


@pytest.mark.asyncio
async def test_multiple_gates_keep_run_waiting_until_all_are_resolved(
    tmp_path: Path,
    scripted_openai,
) -> None:
    base_url, configure = scripted_openai
    def respond(body: dict[str, Any]) -> dict[str, Any]:
        marker = _agent_marker(body)
        seed = _agent_inputs(body)["seed"]
        if marker == "split":
            return {"seed": seed}
        if marker == "gate-a":
            return {"seed": seed, "done_a": "A"}
        return {"seed": seed, "done_b": "B"}

    configure(respond)
    sdl = _graph_sdl(
        base_url,
        agents=[
            {
                "id": "split",
                "name": "Split",
                "system_prompt": "AGENT: split",
                "inputs": [{"name": "seed", "type": "TEXT", "required": True}],
                "outputs": [{"name": "seed", "type": "TEXT"}],
            },
            {
                "id": "gate-a",
                "name": "Gate A",
                "system_prompt": "AGENT: gate-a",
                "inputs": [{"name": "seed", "type": "TEXT", "required": True}],
                "outputs": [
                    {"name": "seed", "type": "TEXT"},
                    {"name": "done_a", "type": "TEXT"},
                ],
            },
            {
                "id": "gate-b",
                "name": "Gate B",
                "system_prompt": "AGENT: gate-b",
                "inputs": [{"name": "seed", "type": "TEXT", "required": True}],
                "outputs": [
                    {"name": "seed", "type": "TEXT"},
                    {"name": "done_b", "type": "TEXT"},
                ],
            },
        ],
        edges=[
            {
                "id": "gate-a-edge",
                "from": "split",
                "to": "gate-a",
                "type": "HUMAN_GATE",
                "mappings": [{"from": "seed", "to": "seed"}],
                "human_gate": {
                    "prompt": "Approve A",
                    "timeout_seconds": 10,
                },
            },
            {
                "id": "gate-b-edge",
                "from": "split",
                "to": "gate-b",
                "type": "HUMAN_GATE",
                "mappings": [{"from": "seed", "to": "seed"}],
                "human_gate": {
                    "prompt": "Approve B",
                    "timeout_seconds": 10,
                },
            },
        ],
        start=["split"],
        workflow_inputs=[
            {"name": "seed", "type": "TEXT", "required": True}
        ],
        workflow_outputs=[
            {"name": "a", "agent": "gate-a", "port": "done_a"},
            {"name": "b", "agent": "gate-b", "port": "done_b"},
        ],
    )
    store = ProjectStore(tmp_path / "projects")
    project = store.create("gates", sdl)
    manager = RunManager(store, MemoryCredentials())
    summary = await manager.start(
        project.id,
        RunStartRequest(inputs={"seed": "start"}),
    )

    detail = await _wait_until(
        lambda: _pending_gate_detail(manager, summary.run_id, count=2)
    )
    first_gate, second_gate = detail.pending_gates
    await manager.decide_gate(
        summary.run_id,
        first_gate.gate_id,
        GateDecisionRequest(decision="approve"),
    )
    detail = await _wait_until(
        lambda: _pending_gate_detail(manager, summary.run_id, count=1)
    )
    assert detail.status == "waiting"
    await manager.decide_gate(
        summary.run_id,
        second_gate.gate_id,
        GateDecisionRequest(decision="approve", comment="looks good"),
    )
    detail = await _wait_for_run(manager, summary.run_id)

    assert detail.status == "succeeded"
    assert detail.result["workflow_outputs"] == {"a": "A", "b": "B"}
    resolved = [
        event
        for event in detail.events
        if event["type"] == "human_gate_resolved"
    ]
    assert len(resolved) == 2
    assert all(event["data"]["decision"] == "approve" for event in resolved)
    await manager.shutdown()


async def _pending_gate_detail(
    manager: RunManager,
    run_id: str,
    *,
    count: int,
):
    detail = await manager.get(run_id)
    if detail.status == "waiting" and len(detail.pending_gates) == count:
        return detail
    return None


@pytest.mark.asyncio
async def test_gate_timeout_marks_decision_and_rejects_late_approval(
    tmp_path: Path,
    scripted_openai,
) -> None:
    base_url, configure = scripted_openai
    configure(
        lambda body: (
            {"seed": _agent_inputs(body)["seed"], "final": "never"}
            if _agent_marker(body) == "source"
            else {"final": "unexpected"}
        )
    )
    sdl = _graph_sdl(
        base_url,
        agents=[
            {
                "id": "source",
                "name": "Source",
                "system_prompt": "AGENT: source",
                "inputs": [{"name": "seed", "type": "TEXT", "required": True}],
                "outputs": [
                    {"name": "seed", "type": "TEXT"},
                    {"name": "final", "type": "TEXT"},
                ],
            },
            {
                "id": "target",
                "name": "Target",
                "system_prompt": "AGENT: target",
                "inputs": [{"name": "seed", "type": "TEXT", "required": True}],
                "outputs": [{"name": "final", "type": "TEXT"}],
            },
        ],
        edges=[
            {
                "id": "timeout-gate",
                "from": "source",
                "to": "target",
                "type": "HUMAN_GATE",
                "mappings": [{"from": "seed", "to": "seed"}],
                "human_gate": {
                    "prompt": "Will time out",
                    "timeout_seconds": 1,
                    "on_timeout": "fail",
                },
            }
        ],
        start=["source"],
        workflow_inputs=[
            {"name": "seed", "type": "TEXT", "required": True}
        ],
        workflow_outputs=[
            {"name": "final", "agent": "target", "port": "final"}
        ],
    )
    store = ProjectStore(tmp_path / "projects")
    project = store.create("gate-timeout", sdl)
    manager = RunManager(store, MemoryCredentials())
    summary = await manager.start(
        project.id,
        RunStartRequest(inputs={"seed": "start"}),
    )
    waiting = await _wait_until(
        lambda: _pending_gate_detail(manager, summary.run_id, count=1)
    )
    gate_id = waiting.pending_gates[0].gate_id
    detail = await _wait_for_run(manager, summary.run_id)

    assert detail.status == "failed"
    assert "timed out" in detail.error
    with pytest.raises(GateConflict):
        await manager.decide_gate(
            summary.run_id,
            gate_id,
            GateDecisionRequest(decision="approve"),
        )
    await manager.shutdown()


@pytest.mark.asyncio
async def test_agent_and_workflow_token_limits_fail_runs(
    tmp_path: Path,
    fake_openai: str,
    legacy_workflow: Callable[[str], SDL],
) -> None:
    store = ProjectStore(tmp_path / "projects")
    agent_limited = legacy_workflow(fake_openai)
    agent_limited.agents[0].limits.max_total_tokens = 10
    project = store.create("agent token limit", agent_limited)
    manager = RunManager(store, MemoryCredentials())
    summary = await manager.start(
        project.id,
        RunStartRequest(inputs={"requirement": "test"}),
    )
    detail = await _wait_for_run(manager, summary.run_id)
    assert detail.status == "failed"
    assert "Agent 'drafter' exceeded max_total_tokens=10" in detail.error
    await manager.shutdown()

    workflow_limited = legacy_workflow(fake_openai)
    workflow_limited.workflow.execution.max_total_tokens = 10
    project = store.create("workflow token limit", workflow_limited)
    manager = RunManager(store, MemoryCredentials())
    summary = await manager.start(
        project.id,
        RunStartRequest(inputs={"requirement": "test"}),
    )
    detail = await _wait_for_run(manager, summary.run_id)
    assert detail.status == "failed"
    assert "Workflow exceeded max_total_tokens=10" in detail.error
    await manager.shutdown()


def test_file_artifact_is_copied_under_custom_output_dir(
    tmp_path: Path,
    scripted_openai,
) -> None:
    base_url, configure = scripted_openai
    configure(
        lambda _: {
            "text_value": "hello",
            "data_value": {"ok": True},
            "file_value": {"path": "uploads/source.txt"},
        }
    )
    sdl = _graph_sdl(
        base_url,
        agents=[
            {
                "id": "writer",
                "name": "Writer",
                "system_prompt": "AGENT: writer",
                "inputs": [
                    {"name": "source", "type": "FILE", "required": True}
                ],
                "outputs": [
                    {"name": "text_value", "type": "TEXT"},
                    {"name": "data_value", "type": "DATA"},
                    {"name": "file_value", "type": "FILE"},
                ],
                "output_dir": "custom-output",
            }
        ],
        edges=[],
        start=["writer"],
        workflow_inputs=[
            {"name": "source", "type": "FILE", "required": True}
        ],
        workflow_outputs=[
            {"name": "text", "agent": "writer", "port": "text_value"}
        ],
    )
    project_dir = tmp_path / "project"
    compiler = WorkflowCompiler()
    generated = compiler.compile(sdl, project_dir)
    workspace = tmp_path / "workspace"
    (workspace / "uploads").mkdir(parents=True)
    (workspace / "uploads" / "source.txt").write_text("source", encoding="utf-8")
    input_path = tmp_path / "input.json"
    result_path = tmp_path / "result.json"
    input_path.write_text(
        json.dumps({"source": {"path": "uploads/source.txt"}}),
        encoding="utf-8",
    )
    env = {
        **os.environ,
        "LOOMLANE_GATE_DIR": str(workspace / "gates"),
        "PYTHONUTF8": "1",
    }
    completed = subprocess.run(
        [
            sys.executable,
            str(Path(generated.generated_path) / "workflow.py"),
            "--input",
            str(input_path),
            "--result",
            str(result_path),
        ],
        cwd=workspace,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    result = json.loads(result_path.read_text(encoding="utf-8"))
    artifacts = {item["port"]: item for item in result["artifacts"]}
    assert artifacts["text_value"]["path"].endswith(".txt")
    assert artifacts["data_value"]["path"].endswith(".json")
    assert artifacts["file_value"]["path"].endswith(".txt")
    for artifact in artifacts.values():
        assert artifact["path"].startswith("custom-output/")
        assert (workspace / artifact["path"]).is_file()
    events = [
        json.loads(line)
        for line in completed.stdout.splitlines()
        if line.strip()
    ]
    assert len(
        [event for event in events if event["type"] == "artifact_written"]
    ) == 3


def test_file_artifact_rejects_workspace_escape(
    tmp_path: Path,
    scripted_openai,
) -> None:
    base_url, configure = scripted_openai
    configure(lambda _: {"file_value": {"path": "../outside.txt"}})
    sdl = _graph_sdl(
        base_url,
        agents=[
            {
                "id": "writer",
                "name": "Writer",
                "system_prompt": "AGENT: writer",
                "inputs": [],
                "outputs": [{"name": "file_value", "type": "FILE"}],
            }
        ],
        edges=[],
        start=["writer"],
        workflow_outputs=[
            {"name": "file", "agent": "writer", "port": "file_value"}
        ],
    )
    project_dir = tmp_path / "project"
    generated = WorkflowCompiler().compile(sdl, project_dir)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    input_path = tmp_path / "input.json"
    result_path = tmp_path / "result.json"
    input_path.write_text("{}", encoding="utf-8")
    completed = subprocess.run(
        [
            sys.executable,
            str(Path(generated.generated_path) / "workflow.py"),
            "--input",
            str(input_path),
            "--result",
            str(result_path),
        ],
        cwd=workspace,
        env={
            **os.environ,
            "LOOMLANE_GATE_DIR": str(workspace / "gates"),
            "PYTHONUTF8": "1",
        },
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 1
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert "escaped the run workspace" in result["error"]


@pytest.mark.asyncio
async def test_agent_rules_run_only_in_their_configured_phase(
    tmp_path: Path,
    scripted_openai,
) -> None:
    base_url, configure = scripted_openai
    configure(lambda _: {"draft": "finished"})
    sdl = _graph_sdl(
        base_url,
        agents=[
            {
                "id": "worker",
                "name": "Worker",
                "system_prompt": "AGENT: worker",
                "inputs": [{"name": "brief", "type": "TEXT", "required": True}],
                "outputs": [{"name": "draft", "type": "TEXT"}],
                "rules": [
                    {
                        "phase": "input",
                        "expression": "not_empty(inputs.brief)",
                    },
                    {
                        "phase": "output",
                        "expression": "not_empty(outputs.draft)",
                    },
                ],
            }
        ],
        edges=[],
        start=["worker"],
        workflow_inputs=[
            {"name": "brief", "type": "TEXT", "required": True}
        ],
        workflow_outputs=[
            {"name": "draft", "agent": "worker", "port": "draft"}
        ],
    )
    store = ProjectStore(tmp_path / "projects")
    project = store.create("rules", sdl)
    manager = RunManager(store, MemoryCredentials())

    summary = await manager.start(
        project.id,
        RunStartRequest(inputs={"brief": "write it"}),
    )
    detail = await _wait_for_run(manager, summary.run_id)

    assert detail.status == "succeeded"
    rule_events = [
        (event["type"], event["data"]["phase"])
        for event in detail.events
        if event["type"] in {"rule_started", "rule_passed", "rule_failed"}
    ]
    assert rule_events == [
        ("rule_started", "input"),
        ("rule_passed", "input"),
        ("rule_started", "output"),
        ("rule_passed", "output"),
    ]
    await manager.shutdown()


@pytest.mark.asyncio
async def test_input_rule_failure_prevents_model_call(
    tmp_path: Path,
    scripted_openai,
) -> None:
    base_url, configure = scripted_openai
    configure(lambda _: {"draft": "should not run"})
    sdl = _graph_sdl(
        base_url,
        agents=[
            {
                "id": "worker",
                "name": "Worker",
                "system_prompt": "AGENT: worker",
                "inputs": [{"name": "brief", "type": "TEXT", "required": True}],
                "outputs": [{"name": "draft", "type": "TEXT"}],
                "rules": [
                    {
                        "phase": "input",
                        "expression": "not_empty(inputs.brief)",
                        "message": "Brief is required",
                    }
                ],
            }
        ],
        edges=[],
        start=["worker"],
        workflow_inputs=[
            {"name": "brief", "type": "TEXT", "required": True}
        ],
        workflow_outputs=[
            {"name": "draft", "agent": "worker", "port": "draft"}
        ],
    )
    store = ProjectStore(tmp_path / "projects")
    project = store.create("input rule failure", sdl)
    manager = RunManager(store, MemoryCredentials())

    summary = await manager.start(
        project.id,
        RunStartRequest(inputs={"brief": ""}),
    )
    detail = await _wait_for_run(manager, summary.run_id)

    assert detail.status == "failed"
    assert detail.error is not None
    assert "Brief is required" in detail.error
    assert ScriptedOpenAIHandler.calls == []
    assert any(
        event["type"] == "rule_failed"
        and event["data"]["phase"] == "input"
        for event in detail.events
    )
    assert not any(event["type"] == "token_usage" for event in detail.events)
    await manager.shutdown()


@pytest.mark.asyncio
async def test_output_rule_failure_writes_no_artifacts_after_token_usage(
    tmp_path: Path,
    scripted_openai,
) -> None:
    base_url, configure = scripted_openai
    configure(lambda _: {"draft": ""})
    sdl = _graph_sdl(
        base_url,
        agents=[
            {
                "id": "worker",
                "name": "Worker",
                "system_prompt": "AGENT: worker",
                "inputs": [{"name": "brief", "type": "TEXT", "required": True}],
                "outputs": [{"name": "draft", "type": "TEXT"}],
                "rules": [
                    {
                        "phase": "output",
                        "expression": "not_empty(outputs.draft)",
                        "message": "Draft is empty",
                    }
                ],
            }
        ],
        edges=[],
        start=["worker"],
        workflow_inputs=[
            {"name": "brief", "type": "TEXT", "required": True}
        ],
        workflow_outputs=[
            {"name": "draft", "agent": "worker", "port": "draft"}
        ],
    )
    store = ProjectStore(tmp_path / "projects")
    project = store.create("output rule failure", sdl)
    manager = RunManager(store, MemoryCredentials())

    summary = await manager.start(
        project.id,
        RunStartRequest(inputs={"brief": "write it"}),
    )
    detail = await _wait_for_run(manager, summary.run_id)

    assert detail.status == "failed"
    assert detail.error is not None
    assert "Draft is empty" in detail.error
    assert len(ScriptedOpenAIHandler.calls) == 1
    assert any(event["type"] == "token_usage" for event in detail.events)
    assert not any(event["type"] == "artifact_written" for event in detail.events)
    assert any(
        event["type"] == "rule_failed"
        and event["data"]["phase"] == "output"
        for event in detail.events
    )
    await manager.shutdown()


@pytest.mark.asyncio
async def test_agent_transforms_run_before_rules_and_models(
    tmp_path: Path,
    scripted_openai,
) -> None:
    base_url, configure = scripted_openai
    configure(lambda _: {"draft": "ready"})
    sdl = _graph_sdl(
        base_url,
        agents=[
            {
                "id": "worker",
                "name": "Worker",
                "system_prompt": "AGENT: worker",
                "inputs": [{"name": "brief", "type": "TEXT", "required": True}],
                "outputs": [{"name": "draft", "type": "TEXT"}],
                "input_transform": {
                    "expression": '{"brief": upper(inputs.brief)}',
                },
                "output_transform": {
                    "expression": '{"draft": upper(raw_outputs.draft)}',
                },
                "rules": [
                    {
                        "phase": "input",
                        "expression": 'startswith(inputs.brief, "URGENT")',
                    },
                    {
                        "phase": "output",
                        "expression": 'outputs.draft == "READY"',
                    },
                ],
            }
        ],
        edges=[],
        start=["worker"],
        workflow_inputs=[
            {"name": "brief", "type": "TEXT", "required": True}
        ],
        workflow_outputs=[
            {"name": "draft", "agent": "worker", "port": "draft"}
        ],
    )
    store = ProjectStore(tmp_path / "projects")
    project = store.create("agent transforms", sdl)
    manager = RunManager(store, MemoryCredentials())

    summary = await manager.start(
        project.id,
        RunStartRequest(inputs={"brief": "urgent request"}),
    )
    detail = await _wait_for_run(manager, summary.run_id)

    assert detail.status == "succeeded"
    assert detail.result["workflow_outputs"]["draft"] == "READY"
    assert _agent_inputs(ScriptedOpenAIHandler.calls[0]) == {
        "brief": "URGENT REQUEST"
    }
    transform_events = [
        (
            event["type"],
            event["data"]["scope"],
            event["data"]["phase"],
        )
        for event in detail.events
        if event["type"].startswith("transform_")
    ]
    assert transform_events == [
        ("transform_started", "agent", "input"),
        ("transform_passed", "agent", "input"),
        ("transform_started", "agent", "output"),
        ("transform_passed", "agent", "output"),
    ]
    await manager.shutdown()


@pytest.mark.asyncio
async def test_input_transform_can_remove_a_required_input_before_model_call(
    tmp_path: Path,
    scripted_openai,
) -> None:
    base_url, configure = scripted_openai
    configure(lambda _: {"draft": "should not run"})
    sdl = _graph_sdl(
        base_url,
        agents=[
            {
                "id": "worker",
                "name": "Worker",
                "system_prompt": "AGENT: worker",
                "inputs": [{"name": "brief", "type": "TEXT", "required": True}],
                "outputs": [{"name": "draft", "type": "TEXT"}],
                "input_transform": {"expression": "{}"},
            }
        ],
        edges=[],
        start=["worker"],
        workflow_inputs=[
            {"name": "brief", "type": "TEXT", "required": True}
        ],
        workflow_outputs=[
            {"name": "draft", "agent": "worker", "port": "draft"}
        ],
    )
    store = ProjectStore(tmp_path / "projects")
    project = store.create("input transform failure", sdl)
    manager = RunManager(store, MemoryCredentials())

    summary = await manager.start(
        project.id,
        RunStartRequest(inputs={"brief": "write it"}),
    )
    detail = await _wait_for_run(manager, summary.run_id)

    assert detail.status == "failed"
    assert detail.error is not None
    assert "missing required input port 'brief'" in detail.error
    assert ScriptedOpenAIHandler.calls == []
    assert any(
        event["type"] == "transform_passed"
        and event["data"]["scope"] == "agent"
        and event["data"]["phase"] == "input"
        for event in detail.events
    )
    assert not any(event["type"] == "transform_failed" for event in detail.events)
    assert not any(event["type"] == "token_usage" for event in detail.events)
    await manager.shutdown()


@pytest.mark.asyncio
async def test_agent_input_transform_must_return_an_object(
    tmp_path: Path,
    scripted_openai,
) -> None:
    base_url, configure = scripted_openai
    configure(lambda _: {"draft": "should not run"})
    sdl = _graph_sdl(
        base_url,
        agents=[
            {
                "id": "worker",
                "name": "Worker",
                "system_prompt": "AGENT: worker",
                "inputs": [{"name": "brief", "type": "TEXT", "required": True}],
                "outputs": [{"name": "draft", "type": "TEXT"}],
                "input_transform": {"expression": "inputs.brief"},
            }
        ],
        edges=[],
        start=["worker"],
        workflow_inputs=[
            {"name": "brief", "type": "TEXT", "required": True}
        ],
        workflow_outputs=[
            {"name": "draft", "agent": "worker", "port": "draft"}
        ],
    )
    store = ProjectStore(tmp_path / "projects")
    project = store.create("input transform object", sdl)
    manager = RunManager(store, MemoryCredentials())

    summary = await manager.start(
        project.id,
        RunStartRequest(inputs={"brief": "write it"}),
    )
    detail = await _wait_for_run(manager, summary.run_id)

    assert detail.status == "failed"
    assert detail.error is not None
    assert "input_transform must return an object" in detail.error
    assert ScriptedOpenAIHandler.calls == []
    failure = next(
        event
        for event in detail.events
        if event["type"] == "transform_failed"
    )
    assert failure["data"]["scope"] == "agent"
    assert failure["data"]["phase"] == "input"
    await manager.shutdown()


@pytest.mark.asyncio
async def test_output_transform_must_return_an_object_before_artifacts(
    tmp_path: Path,
    scripted_openai,
) -> None:
    base_url, configure = scripted_openai
    configure(lambda _: {"draft": "raw draft"})
    sdl = _graph_sdl(
        base_url,
        agents=[
            {
                "id": "worker",
                "name": "Worker",
                "system_prompt": "AGENT: worker",
                "inputs": [{"name": "brief", "type": "TEXT", "required": True}],
                "outputs": [{"name": "draft", "type": "TEXT"}],
                "output_transform": {"expression": "raw_outputs.draft"},
                "rules": [
                    {
                        "phase": "output",
                        "expression": "not_empty(outputs.draft)",
                    }
                ],
            }
        ],
        edges=[],
        start=["worker"],
        workflow_inputs=[
            {"name": "brief", "type": "TEXT", "required": True}
        ],
        workflow_outputs=[
            {"name": "draft", "agent": "worker", "port": "draft"}
        ],
    )
    store = ProjectStore(tmp_path / "projects")
    project = store.create("output transform object", sdl)
    manager = RunManager(store, MemoryCredentials())

    summary = await manager.start(
        project.id,
        RunStartRequest(inputs={"brief": "write it"}),
    )
    detail = await _wait_for_run(manager, summary.run_id)

    assert detail.status == "failed"
    assert detail.error is not None
    assert "output_transform must return an object" in detail.error
    assert len(ScriptedOpenAIHandler.calls) == 1
    assert any(event["type"] == "token_usage" for event in detail.events)
    assert any(
        event["type"] == "transform_failed"
        and event["data"]["scope"] == "agent"
        and event["data"]["phase"] == "output"
        for event in detail.events
    )
    assert not any(event["type"] == "artifact_written" for event in detail.events)
    assert not any(event["type"].startswith("rule_") for event in detail.events)
    await manager.shutdown()


@pytest.mark.asyncio
async def test_edge_mapping_transform_can_convert_text_to_data(
    tmp_path: Path,
    scripted_openai,
) -> None:
    base_url, configure = scripted_openai

    def respond(body: dict[str, Any]) -> dict[str, Any]:
        if _agent_marker(body) == "source":
            return {"payload": "hello"}
        return {"result": "done"}

    configure(respond)
    sdl = _graph_sdl(
        base_url,
        agents=[
            {
                "id": "source",
                "name": "Source",
                "system_prompt": "AGENT: source",
                "inputs": [{"name": "brief", "type": "TEXT", "required": True}],
                "outputs": [{"name": "payload", "type": "TEXT"}],
            },
            {
                "id": "target",
                "name": "Target",
                "system_prompt": "AGENT: target",
                "inputs": [{"name": "payload", "type": "DATA", "required": True}],
                "outputs": [{"name": "result", "type": "TEXT"}],
            },
        ],
        edges=[
            {
                "id": "source-target",
                "from": "source",
                "to": "target",
                "type": "PIPE",
                "mappings": [
                    {
                        "from": "payload",
                        "to": "payload",
                        "transform": {
                            "expression": (
                                '{"text": value, "brief": workflow.inputs.brief, '
                                '"source": outputs.payload, "step": run.step}'
                            )
                        },
                    }
                ],
            }
        ],
        start=["source"],
        workflow_inputs=[
            {"name": "brief", "type": "TEXT", "required": True}
        ],
        workflow_outputs=[
            {"name": "result", "agent": "target", "port": "result"}
        ],
    )
    store = ProjectStore(tmp_path / "projects")
    project = store.create("edge transform", sdl)
    manager = RunManager(store, MemoryCredentials())

    summary = await manager.start(
        project.id,
        RunStartRequest(inputs={"brief": "original brief"}),
    )
    detail = await _wait_for_run(manager, summary.run_id)

    assert detail.status == "succeeded"
    target_call = next(
        call
        for call in ScriptedOpenAIHandler.calls
        if _agent_marker(call) == "target"
    )
    assert _agent_inputs(target_call) == {
        "payload": {
            "text": "hello",
            "brief": "original brief",
            "source": "hello",
            "step": 1,
        }
    }
    transform_events = [
        event
        for event in detail.events
        if event["type"].startswith("transform_")
    ]
    assert [event["type"] for event in transform_events] == [
        "transform_started",
        "transform_passed",
    ]
    for event in transform_events:
        assert event["edge_id"] == "source-target"
        assert event["data"]["scope"] == "edge"
        assert event["data"]["phase"] == "mapping"
        assert event["data"]["mapping_index"] == 0
    await manager.shutdown()


@pytest.mark.asyncio
async def test_edge_mapping_transform_failure_does_not_schedule_downstream(
    tmp_path: Path,
    scripted_openai,
) -> None:
    base_url, configure = scripted_openai

    def respond(body: dict[str, Any]) -> dict[str, Any]:
        assert _agent_marker(body) == "source"
        return {"payload": "hello"}

    configure(respond)
    sdl = _graph_sdl(
        base_url,
        agents=[
            {
                "id": "source",
                "name": "Source",
                "system_prompt": "AGENT: source",
                "inputs": [{"name": "brief", "type": "TEXT", "required": True}],
                "outputs": [{"name": "payload", "type": "TEXT"}],
            },
            {
                "id": "target",
                "name": "Target",
                "system_prompt": "AGENT: target",
                "inputs": [{"name": "payload", "type": "DATA", "required": True}],
                "outputs": [{"name": "result", "type": "TEXT"}],
            },
        ],
        edges=[
            {
                "id": "source-target",
                "from": "source",
                "to": "target",
                "type": "PIPE",
                "mappings": [
                    {
                        "from": "payload",
                        "to": "payload",
                        "transform": {"expression": 'float("nan")'},
                    }
                ],
            }
        ],
        start=["source"],
        workflow_inputs=[
            {"name": "brief", "type": "TEXT", "required": True}
        ],
        workflow_outputs=[
            {"name": "result", "agent": "target", "port": "result"}
        ],
    )
    store = ProjectStore(tmp_path / "projects")
    project = store.create("edge transform failure", sdl)
    manager = RunManager(store, MemoryCredentials())

    summary = await manager.start(
        project.id,
        RunStartRequest(inputs={"brief": "original brief"}),
    )
    detail = await _wait_for_run(manager, summary.run_id)

    assert detail.status == "failed"
    assert detail.error is not None
    assert "NaN or Infinity" in detail.error
    assert len(ScriptedOpenAIHandler.calls) == 1
    assert not any(event["type"] == "edge_traversed" for event in detail.events)
    failure = next(
        event
        for event in detail.events
        if event["type"] == "transform_failed"
    )
    assert failure["edge_id"] == "source-target"
    assert failure["data"]["scope"] == "edge"
    assert failure["data"]["phase"] == "mapping"
    assert failure["data"]["mapping_index"] == 0
    await manager.shutdown()
