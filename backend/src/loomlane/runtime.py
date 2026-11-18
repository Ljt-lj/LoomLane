from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator
from urllib.parse import urlparse
from uuid import uuid4

from pydantic import ValidationError

from .compiler import WorkflowCompiler
from .models import ProjectDetail, RunDetail, RunStartRequest, RunSummary
from .secrets import CredentialError, CredentialStore
from .storage import ProjectStore, _atomic_write_json, _atomic_write_text, utc_now


class RunError(RuntimeError):
    pass


class RunNotFound(RunError):
    pass


class ActiveRunExists(RunError):
    pass


class RunConfigurationError(RunError):
    pass


def _profile_secret_env(profile_id: str) -> str:
    return "LOOMLANE_SECRET_" + re.sub(r"[^A-Za-z0-9]", "_", profile_id).upper()


def _is_local_url(base_url: str) -> bool:
    host = (urlparse(base_url).hostname or "").lower()
    return host in {"127.0.0.1", "localhost", "::1"}


@dataclass
class _LiveRun:
    run_id: str
    project_id: str
    run_root: Path
    events: list[dict[str, Any]] = field(default_factory=list)
    subscribers: set[asyncio.Queue[dict[str, Any] | None]] = field(
        default_factory=set
    )
    process: asyncio.subprocess.Process | None = None
    task: asyncio.Task[None] | None = None
    cancelled: bool = False
    finished: bool = False


class RunManager:
    MAX_EVENTS = 10_000
    MAX_EVENT_BYTES = 1_000_000
    MAX_STDERR_BYTES = 128_000
    DEFAULT_TIMEOUT_SECONDS = 3_600

    def __init__(
        self,
        store: ProjectStore,
        credentials: CredentialStore,
        compiler: WorkflowCompiler | None = None,
    ) -> None:
        self.store = store
        self.credentials = credentials
        self.compiler = compiler or WorkflowCompiler()
        self._active_by_project: dict[str, _LiveRun] = {}
        self._live: dict[str, _LiveRun] = {}
        self._lock = asyncio.Lock()

    async def start(
        self,
        project_id: str,
        request: RunStartRequest,
    ) -> RunSummary:
        project = self.store.get(project_id)
        async with self._lock:
            if project_id in self._active_by_project:
                raise ActiveRunExists("This project already has an active run")

        generated = self.compiler.compile(project.sdl, self.store.project_path(project_id))
        self.store.set_last_build(project_id, generated.compiler_key)

        run_id = uuid4().hex
        env = self._runtime_environment(project, run_id, generated)
        run_root = self.store.project_path(project_id) / "runs" / run_id
        (run_root / "workspace").mkdir(parents=True, exist_ok=False)
        _atomic_write_json(run_root / "input.json", request.inputs)

        started_at = utc_now()
        summary = RunSummary(
            run_id=run_id,
            project_id=project_id,
            status="queued",
            created_at=started_at,
        )
        _atomic_write_json(run_root / "meta.json", summary.model_dump(mode="json"))
        _atomic_write_text(run_root / "events.jsonl", "")

        live = _LiveRun(run_id=run_id, project_id=project_id, run_root=run_root)
        async with self._lock:
            if project_id in self._active_by_project:
                raise ActiveRunExists("This project already has an active run")
            self._active_by_project[project_id] = live
            self._live[run_id] = live
            live.task = asyncio.create_task(
                self._execute(live, Path(generated.generated_path), env)
            )
        return summary

    def _runtime_environment(
        self,
        project: ProjectDetail,
        run_id: str,
        generated: Any,
    ) -> dict[str, str]:
        system_root = os.environ.get("SystemRoot", r"C:\Windows")
        env = {
            "PATH": os.environ.get("PATH", ""),
            "SystemRoot": system_root,
            "WINDIR": os.environ.get("WINDIR", system_root),
            "TEMP": os.environ.get("TEMP", ""),
            "TMP": os.environ.get("TMP", ""),
            "PYTHONUTF8": "1",
            "LOOMLANE_RUN_ID": run_id,
            "LOOMLANE_WORKFLOW_HASH": generated.compiler_key,
        }
        for profile in project.sdl.api_profiles:
            try:
                api_key = self.credentials.get(
                    project.id,
                    profile.id,
                    profile.base_url,
                )
            except CredentialError as exc:
                raise RunConfigurationError(str(exc)) from exc
            if api_key:
                env[_profile_secret_env(profile.id)] = api_key
            elif not _is_local_url(profile.base_url):
                raise RunConfigurationError(
                    f"API credential is not configured for profile '{profile.id}'"
                )
        return env

    async def _execute(
        self,
        live: _LiveRun,
        generated_root: Path,
        env: dict[str, str],
    ) -> None:
        await self._set_meta(live, status="running", started_at=utc_now())
        stdout_task: asyncio.Task[None] | None = None
        stderr_task: asyncio.Task[str] | None = None
        error: str | None = None
        status = "failed"
        event_file = (live.run_root / "events.jsonl").open(
            "a",
            encoding="utf-8",
            newline="\n",
        )
        try:
            command = [
                sys.executable,
                str(generated_root / "workflow.py"),
                "--input",
                str(live.run_root / "input.json"),
                "--result",
                str(live.run_root / "result.json"),
            ]
            creation_flags = (
                subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
            )
            live.process = await asyncio.create_subprocess_exec(
                *command,
                cwd=live.run_root / "workspace",
                env=env,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                creationflags=creation_flags,
            )
            assert live.process.stdout is not None
            assert live.process.stderr is not None
            stdout_task = asyncio.create_task(
                self._read_events(live, live.process.stdout, event_file)
            )
            stderr_task = asyncio.create_task(
                self._read_stderr(live.process.stderr)
            )

            try:
                return_code = await asyncio.wait_for(
                    live.process.wait(),
                    timeout=self.DEFAULT_TIMEOUT_SECONDS,
                )
            except TimeoutError:
                live.process.kill()
                await live.process.wait()
                error = (
                    "Workflow process exceeded "
                    f"{self.DEFAULT_TIMEOUT_SECONDS} seconds"
                )
                return_code = -1

            await asyncio.gather(stdout_task, stderr_task)
            stderr_text = stderr_task.result()
            if stderr_text:
                _atomic_write_text(live.run_root / "stderr.log", stderr_text)

            result = self._read_result(live.run_root / "result.json")
            if live.cancelled:
                status = "cancelled"
                error = None
            elif return_code == 0 and result.get("status") == "succeeded":
                status = "succeeded"
            else:
                status = "failed"
                error = str(result.get("error") or f"Process exited with code {return_code}")
        except Exception as exc:
            error = str(exc) or exc.__class__.__name__
            if live.process and live.process.returncode is None:
                live.process.kill()
                await live.process.wait()
        finally:
            if live.process and live.process.returncode is None:
                live.process.kill()
                await live.process.wait()
            if stdout_task and not stdout_task.done():
                stdout_task.cancel()
            if stderr_task and not stderr_task.done():
                stderr_task.cancel()
            await asyncio.gather(
                *[
                    task
                    for task in (stdout_task, stderr_task)
                    if task is not None
                ],
                return_exceptions=True,
            )
            event_file.close()

            if live.cancelled:
                status = "cancelled"
                error = None
            await self._set_meta(
                live,
                status=status,
                finished_at=utc_now(),
                error=error,
            )
            async with self._lock:
                live.finished = True
                self._active_by_project.pop(live.project_id, None)
                for queue in list(live.subscribers):
                    queue.put_nowait(None)

    async def _set_meta(
        self,
        live: _LiveRun,
        *,
        status: str,
        started_at: Any | None = None,
        finished_at: Any | None = None,
        error: str | None = None,
    ) -> None:
        current = self._read_meta(live.run_root / "meta.json")
        payload = current.model_dump(mode="json")
        payload.update(
            {
                "status": status,
                "started_at": (
                    started_at.isoformat()
                    if started_at is not None
                    else payload.get("started_at")
                ),
                "finished_at": (
                    finished_at.isoformat()
                    if finished_at is not None
                    else payload.get("finished_at")
                ),
                "error": error,
            }
        )
        _atomic_write_json(live.run_root / "meta.json", payload)

    async def _read_events(
        self,
        live: _LiveRun,
        stream: asyncio.StreamReader,
        event_file: Any,
    ) -> None:
        previous_seq = 0
        while True:
            line = await stream.readline()
            if not line:
                break
            if len(line) > self.MAX_EVENT_BYTES:
                raise RunError("Workflow emitted an oversized event")
            text = line.decode("utf-8", errors="replace").strip()
            if not text:
                continue
            try:
                event = json.loads(text)
            except json.JSONDecodeError as exc:
                raise RunError(f"Workflow emitted invalid NDJSON: {exc}") from exc
            if not isinstance(event, dict):
                raise RunError("Workflow event must be a JSON object")
            seq = event.get("seq")
            if not isinstance(seq, int) or seq <= previous_seq:
                seq = previous_seq + 1
                event["seq"] = seq
            previous_seq = seq
            if len(live.events) >= self.MAX_EVENTS:
                raise RunError("Workflow exceeded the event count limit")
            event_file.write(json.dumps(event, ensure_ascii=False) + "\n")
            event_file.flush()
            async with self._lock:
                live.events.append(event)
                for queue in list(live.subscribers):
                    queue.put_nowait(event)

    async def _read_stderr(self, stream: asyncio.StreamReader) -> str:
        chunks: list[bytes] = []
        size = 0
        while True:
            chunk = await stream.read(4096)
            if not chunk:
                break
            remaining = self.MAX_STDERR_BYTES - size
            if remaining <= 0:
                continue
            chunks.append(chunk[:remaining])
            size += min(len(chunk), remaining)
        return b"".join(chunks).decode("utf-8", errors="replace")

    def _read_result(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        try:
            result = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return result if isinstance(result, dict) else {}

    def _read_meta(self, path: Path) -> RunSummary:
        try:
            return RunSummary.model_validate(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError, ValidationError) as exc:
            raise RunNotFound(str(path.parent.name)) from exc

    def _find_run(self, run_id: str) -> tuple[Path, ProjectDetail]:
        if not re.fullmatch(r"[0-9a-f]{32}", run_id):
            raise RunNotFound(run_id)
        for project in self.store.list():
            run_root = self.store.project_path(project.id) / "runs" / run_id
            if (run_root / "meta.json").exists():
                return run_root, self.store.get(project.id)
        raise RunNotFound(run_id)

    async def get(self, run_id: str) -> RunDetail:
        run_root, _ = self._find_run(run_id)
        summary = self._read_meta(run_root / "meta.json")
        result = self._read_result(run_root / "result.json")
        events = self._read_events_file(run_root)
        return RunDetail(
            **summary.model_dump(),
            result=result or None,
            events=events,
        )

    def _read_events_file(self, run_root: Path) -> list[dict[str, Any]]:
        path = run_root / "events.jsonl"
        if not path.exists():
            return []
        events: list[dict[str, Any]] = []
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(event, dict):
                    events.append(event)
        return events

    async def list_for_project(self, project_id: str) -> list[RunSummary]:
        runs_root = self.store.project_path(project_id) / "runs"
        if not runs_root.exists():
            return []
        summaries: list[RunSummary] = []
        for path in runs_root.iterdir():
            meta = path / "meta.json"
            if not meta.exists():
                continue
            try:
                summaries.append(self._read_meta(meta))
            except RunNotFound:
                continue
        return sorted(summaries, key=lambda run: run.created_at, reverse=True)

    async def cancel(self, run_id: str) -> RunSummary:
        task: asyncio.Task[None] | None = None
        async with self._lock:
            live = self._live.get(run_id)
            if live and not live.finished:
                live.cancelled = True
                if live.process and live.process.returncode is None:
                    live.process.kill()
                task = live.task
        if task:
            await asyncio.gather(task, return_exceptions=True)
        summary = self._read_meta(self._find_run(run_id)[0] / "meta.json")
        if summary.status in {"succeeded", "failed", "cancelled"}:
            return summary
        run_root, _ = self._find_run(run_id)
        return self._read_meta(run_root / "meta.json")

    async def is_active(self, project_id: str) -> bool:
        async with self._lock:
            return project_id in self._active_by_project

    async def events(
        self,
        run_id: str,
        after_seq: int = 0,
    ) -> AsyncIterator[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any] | None] | None = None
        live: _LiveRun | None = None
        async with self._lock:
            live = self._live.get(run_id)
            if live is not None:
                queue = asyncio.Queue()
                live.subscribers.add(queue)
                backlog = [
                    event
                    for event in live.events
                    if int(event.get("seq", 0)) > after_seq
                ]
                finished = live.finished
            else:
                backlog = []
                finished = True

        if live is None:
            run_root, _ = self._find_run(run_id)
            for event in self._read_events_file(run_root):
                if int(event.get("seq", 0)) > after_seq:
                    yield event
            return

        try:
            for event in backlog:
                yield event
            if finished:
                return
            assert queue is not None
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                except TimeoutError:
                    continue
                if event is None:
                    return
                yield event
        finally:
            if queue is not None:
                async with self._lock:
                    live.subscribers.discard(queue)

    async def shutdown(self) -> None:
        tasks: list[asyncio.Task[None]] = []
        async with self._lock:
            for live in self._active_by_project.values():
                live.cancelled = True
                if live.process and live.process.returncode is None:
                    live.process.kill()
                if live.task:
                    tasks.append(live.task)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
