from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from loomlane.models import RunStartRequest
from loomlane.runtime import RunManager
from loomlane.storage import ProjectStore, default_canvas, default_sdl


class MemoryCredentials:
    def get(self, project_id: str, profile_id: str, base_url: str) -> str | None:
        return None

    def status(self, project_id: str, profile_id: str, base_url: str):
        from loomlane.models import SecretStatus

        return SecretStatus(profile_id=profile_id, configured=False, origin=base_url)

    def put(self, project_id: str, profile_id: str, api_key: str, base_url: str):
        raise AssertionError("not used")

    def delete(self, project_id: str, profile_id: str) -> None:
        return None


class FakeOpenAIHandler(BaseHTTPRequestHandler):
    calls: list[dict[str, Any]] = []
    reviewer_calls = 0
    forced_status: int | None = None

    def log_message(self, format: str, *args: Any) -> None:
        return

    def do_POST(self) -> None:
        type(self).calls.append({})
        if type(self).forced_status is not None:
            self.send_response(type(self).forced_status)
            self.end_headers()
            return

        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
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
            "choices": [{"message": {"content": json.dumps(outputs, ensure_ascii=False)}}],
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


@pytest.fixture
def fake_openai() -> str:
    FakeOpenAIHandler.calls = []
    FakeOpenAIHandler.reviewer_calls = 0
    FakeOpenAIHandler.forced_status = None
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeOpenAIHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1"
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


@pytest.mark.asyncio
async def test_feedback_run_end_to_end(tmp_path: Path, fake_openai: str) -> None:
    store = ProjectStore(tmp_path / "projects")
    sdl = default_sdl()
    sdl.api_profiles[0].base_url = fake_openai
    project = store.create("feedback test", sdl, default_canvas())
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
    event_file = (
        store.project_path(project.id)
        / "runs"
        / summary.run_id
        / "events.jsonl"
    )
    assert len(event_file.read_text(encoding="utf-8").splitlines()) == len(
        detail.events
    )
    await manager.shutdown()


@pytest.mark.asyncio
async def test_non_retryable_4xx_is_not_retried(
    tmp_path: Path,
    fake_openai: str,
) -> None:
    FakeOpenAIHandler.forced_status = 400
    store = ProjectStore(tmp_path / "projects")
    sdl = default_sdl()
    sdl.api_profiles[0].base_url = fake_openai
    project = store.create("4xx test", sdl, default_canvas())
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
