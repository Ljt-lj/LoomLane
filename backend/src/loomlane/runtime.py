from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, BinaryIO
from urllib.parse import urlparse
from uuid import uuid4

from pydantic import ValidationError

from .compiler import WorkflowCompiler
from .models import (
    GateDecisionRequest,
    GateDecisionResponse,
    InputFileRef,
    PendingHumanGate,
    ProjectDetail,
    RunDetail,
    RunResumeCheckpoint,
    RunResumeOptions,
    RunResumeRequest,
    RunStartRequest,
    RunSummary,
    SDL,
)
from .resume import checkpoint_entries
from .secrets import CredentialError, CredentialStore
from .storage import ProjectStore, _atomic_write_json, _atomic_write_text, utc_now


class RunError(RuntimeError):
    pass


class RunNotFound(RunError):
    pass


class ActiveRunExists(RunError):
    pass


class RunResumeConflict(RunError):
    pass


class RunConfigurationError(RunError):
    pass


class InputFileError(RunError):
    pass


class RunFolderError(RunError):
    pass


_INVALID_PATH_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


def _safe_path_component(value: str, fallback: str) -> str:
    cleaned = _INVALID_PATH_CHARS.sub("-", value).strip(" .")
    if not cleaned:
        cleaned = fallback
    if cleaned.split(".", 1)[0].upper() in _WINDOWS_RESERVED_NAMES:
        cleaned = f"_{cleaned}"
    return cleaned[:96].rstrip(" .")


def _safe_file_name(value: str) -> str:
    candidate = Path(value).name
    cleaned = _safe_path_component(candidate, "input-file")
    if "." in cleaned:
        stem, suffix = cleaned.rsplit(".", 1)
        suffix = re.sub(r"[^A-Za-z0-9._-]", "", suffix)[:16]
        cleaned = f"{stem[:72]}.{suffix}" if suffix else stem[:72]
    return cleaned or "input-file"


def _launch_directory(path: Path) -> None:
    if os.name == "nt":
        subprocess.Popen(
            ["explorer.exe", str(path)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=(
                subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
            ),
        )
        return
    command = ["open", str(path)] if sys.platform == "darwin" else ["xdg-open", str(path)]
    subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


class GateNotFound(RunError):
    pass


class GateConflict(RunError):
    pass


def _profile_secret_env(profile_id: str) -> str:
    return "LOOMLANE_SECRET_" + re.sub(r"[^A-Za-z0-9]", "_", profile_id).upper()


def _is_local_url(base_url: str) -> bool:
    host = (urlparse(base_url).hostname or "").lower()
    return host in {"127.0.0.1", "localhost", "::1"}


def _exception_message(exc: BaseException) -> str:
    type_name = exc.__class__.__name__
    message = str(exc).strip()
    return type_name if not message or message == type_name else f"{type_name}: {message}"


def _wait_for_process(
    process: subprocess.Popen[bytes],
    timeout: float | None = None,
) -> int:
    return process.wait(timeout=timeout)


@dataclass
class _LiveRun:
    run_id: str
    project_id: str
    run_root: Path
    events: list[dict[str, Any]] = field(default_factory=list)
    subscribers: set[asyncio.Queue[dict[str, Any] | None]] = field(
        default_factory=set
    )
    process: subprocess.Popen[bytes] | None = None
    task: asyncio.Task[None] | None = None
    cancelled: bool = False
    finished: bool = False
    waiting_gate_ids: set[str] = field(default_factory=set)

    @property
    def gates_dir(self) -> Path:
        return self.run_root / "gates"


class RunManager:
    MAX_EVENTS = 10_000
    MAX_EVENT_BYTES = 1_000_000
    MAX_STDERR_BYTES = 128_000
    MAX_INPUT_FILE_BYTES = 50 * 1024 * 1024
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
        started_at = utc_now()
        project_name = _safe_path_component(project.name, "project")
        timestamp = started_at.strftime("%Y%m%d-%H%M%S")
        run_directory = f"{project_name}__{timestamp}__{run_id[:8]}"
        run_root = self.store.project_path(project_id) / "runs" / run_directory
        workspace = run_root / "workspace"
        artifacts = workspace / "artifacts"
        env = self._runtime_environment(project, run_id, generated, run_root)
        staged_sources: list[Path] = []
        try:
            artifacts.mkdir(parents=True, exist_ok=False)
            (run_root / "gates").mkdir(parents=True, exist_ok=False)
            prepared_inputs, staged_sources = self._prepare_inputs(
                project,
                request.inputs,
                workspace,
            )
            _atomic_write_json(run_root / "input.json", prepared_inputs)
            _atomic_write_json(
                run_root / "workflow.snapshot.json",
                project.sdl.canonical_dict(),
            )
            (run_root / "checkpoints").mkdir(parents=True, exist_ok=False)
        except Exception:
            if run_root.exists():
                shutil.rmtree(run_root, ignore_errors=True)
            raise

        summary = RunSummary(
            run_id=run_id,
            project_id=project_id,
            status="queued",
            created_at=started_at,
            run_directory=run_directory,
            output_directory=str(artifacts),
        )
        _atomic_write_json(run_root / "meta.json", summary.model_dump(mode="json"))
        _atomic_write_text(run_root / "events.jsonl", "")
        for source in staged_sources:
            source.unlink(missing_ok=True)

        live = _LiveRun(run_id=run_id, project_id=project_id, run_root=run_root)
        async with self._lock:
            if project_id in self._active_by_project:
                raise ActiveRunExists("This project already has an active run")
            self._active_by_project[project_id] = live
            self._live[run_id] = live
            live.task = asyncio.create_task(
                self._execute(
                    live,
                    Path(generated.generated_path),
                    env,
                    resume_checkpoint=None,
                )
            )
        return summary

    async def resume_options(self, source_run_id: str) -> RunResumeOptions:
        source_root, project = self._find_run(source_run_id)
        async with self._lock:
            if project.id in self._active_by_project:
                raise ActiveRunExists("This project already has an active run")
        return await self._build_resume_options(source_root, project)

    async def _build_resume_options(
        self,
        source_root: Path,
        project: ProjectDetail,
    ) -> RunResumeOptions:
        source = self._read_meta(source_root / "meta.json")
        if source.status not in {"succeeded", "failed", "cancelled"}:
            raise RunResumeConflict(
                "Only a terminal run can be used as a resume source"
            )
        summaries = await self.list_for_project(project.id)
        latest = summaries[0] if summaries else None
        if latest is None or latest.run_id != source.run_id:
            raise RunResumeConflict(
                "Only the project's most recent run can be resumed"
            )

        source_snapshot: SDL | None = None
        snapshot_path = source_root / "workflow.snapshot.json"
        if snapshot_path.exists():
            try:
                source_snapshot = SDL.model_validate(
                    json.loads(snapshot_path.read_text(encoding="utf-8"))
                )
            except (OSError, ValueError, TypeError, ValidationError):
                source_snapshot = None

        entries, global_reason = checkpoint_entries(
            source_root,
            source_snapshot,
            project.sdl,
        )
        checkpoints = [
            RunResumeCheckpoint.model_validate(item) for item in entries
        ]
        if not checkpoints and global_reason is None:
            global_reason = "该运行没有可用的检查点"
        return RunResumeOptions(
            source_run_id=source.run_id,
            project_id=project.id,
            checkpoints=checkpoints,
            snapshot_available=source_snapshot is not None,
            global_disabled_reason=global_reason,
        )

    async def resume(
        self,
        project_id: str,
        request: RunResumeRequest,
    ) -> RunSummary:
        project = self.store.get(project_id)
        source_root, source_project = self._find_run(request.source_run_id)
        if source_project.id != project_id:
            raise RunResumeConflict("Source run belongs to a different project")
        async with self._lock:
            if project_id in self._active_by_project:
                raise ActiveRunExists("This project already has an active run")

        options = await self._build_resume_options(source_root, project)
        checkpoint = next(
            (
                item
                for item in options.checkpoints
                if item.checkpoint_id == request.checkpoint_id
            ),
            None,
        )
        if checkpoint is None:
            raise RunResumeConflict("Resume checkpoint was not found")
        if not checkpoint.available:
            raise RunResumeConflict(
                checkpoint.disabled_reason or "Resume checkpoint is not available"
            )
        if not re.fullmatch(r"checkpoint-\d{4,}", request.checkpoint_id):
            raise RunResumeConflict("Invalid resume checkpoint id")

        source_checkpoint = (
            source_root / "checkpoints" / f"{request.checkpoint_id}.json"
        )
        source_workspace = (
            source_root
            / "checkpoints"
            / f"{request.checkpoint_id}.workspace"
        )
        source_input = source_root / "input.json"
        if (
            not source_checkpoint.is_file()
            or not source_workspace.is_dir()
            or not source_input.is_file()
        ):
            raise RunResumeConflict("Source run data is incomplete")

        generated = self.compiler.compile(
            project.sdl,
            self.store.project_path(project_id),
        )
        self.store.set_last_build(project_id, generated.compiler_key)

        run_id = uuid4().hex
        started_at = utc_now()
        project_name = _safe_path_component(project.name, "project")
        timestamp = started_at.strftime("%Y%m%d-%H%M%S")
        run_directory = f"{project_name}__{timestamp}__{run_id[:8]}"
        run_root = self.store.project_path(project_id) / "runs" / run_directory
        workspace = run_root / "workspace"
        artifacts = workspace / "artifacts"
        checkpoint_dir = run_root / "checkpoints"
        env = self._runtime_environment(project, run_id, generated, run_root)
        env.update(
            {
                "LOOMLANE_RESUME_SOURCE_RUN_ID": request.source_run_id,
                "LOOMLANE_RESUME_CHECKPOINT_ID": request.checkpoint_id,
            }
        )
        try:
            run_root.mkdir(parents=True, exist_ok=False)
            shutil.copytree(source_workspace, workspace)
            artifacts.mkdir(parents=True, exist_ok=True)
            (run_root / "gates").mkdir(parents=True, exist_ok=False)
            checkpoint_dir.mkdir(parents=True, exist_ok=False)
            shutil.copy2(source_input, run_root / "input.json")
            shutil.copy2(
                source_checkpoint,
                checkpoint_dir / source_checkpoint.name,
            )
            _atomic_write_json(
                run_root / "workflow.snapshot.json",
                project.sdl.canonical_dict(),
            )
        except Exception:
            if run_root.exists():
                shutil.rmtree(run_root, ignore_errors=True)
            raise

        summary = RunSummary(
            run_id=run_id,
            project_id=project_id,
            status="queued",
            created_at=started_at,
            run_directory=run_directory,
            output_directory=str(artifacts),
            resumed_from_run_id=request.source_run_id,
            resume_checkpoint_id=request.checkpoint_id,
        )
        _atomic_write_json(run_root / "meta.json", summary.model_dump(mode="json"))
        _atomic_write_text(run_root / "events.jsonl", "")

        live = _LiveRun(run_id=run_id, project_id=project_id, run_root=run_root)
        async with self._lock:
            if project_id in self._active_by_project:
                shutil.rmtree(run_root, ignore_errors=True)
                raise ActiveRunExists("This project already has an active run")
            self._active_by_project[project_id] = live
            self._live[run_id] = live
            live.task = asyncio.create_task(
                self._execute(
                    live,
                    Path(generated.generated_path),
                    env,
                    resume_checkpoint=checkpoint_dir / source_checkpoint.name,
                )
            )
        return summary

    def stage_input_file(
        self,
        project_id: str,
        input_name: str,
        original_name: str,
        content: bytes,
    ) -> InputFileRef:
        project = self.store.get(project_id)
        workflow_input = next(
            (
                item
                for item in project.sdl.workflow.inputs
                if item.name == input_name
            ),
            None,
        )
        if workflow_input is None:
            raise InputFileError(f"Unknown workflow input '{input_name}'")
        if workflow_input.type.value != "FILE":
            raise InputFileError(f"Workflow input '{input_name}' is not a FILE port")
        if not content:
            raise InputFileError("The selected file is empty")
        if len(content) > self.MAX_INPUT_FILE_BYTES:
            raise InputFileError(
                f"Input files must be smaller than {self.MAX_INPUT_FILE_BYTES // (1024 * 1024)} MB"
            )

        safe_name = _safe_file_name(original_name)
        stored_name = f"{uuid4().hex[:12]}-{safe_name}"
        relative_path = f"uploads/{stored_name}"
        upload_root = self.store.project_path(project_id) / "uploads"
        upload_root.mkdir(parents=True, exist_ok=True)
        target = upload_root / stored_name
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_bytes(content)
        os.replace(temporary, target)
        return InputFileRef(
            input_name=input_name,
            name=original_name,
            path=relative_path,
            size=len(content),
        )

    def _prepare_inputs(
        self,
        project: ProjectDetail,
        inputs: dict[str, Any],
        workspace: Path,
    ) -> tuple[dict[str, Any], list[Path]]:
        prepared = dict(inputs)
        staged_sources: list[Path] = []
        project_root = self.store.project_path(project.id).resolve()
        upload_root = (project_root / "uploads").resolve()
        workspace_root = workspace.resolve()
        for workflow_input in project.sdl.workflow.inputs:
            if workflow_input.type.value != "FILE" or workflow_input.name not in inputs:
                continue
            value = inputs[workflow_input.name]
            if not isinstance(value, dict) or not isinstance(value.get("path"), str):
                raise InputFileError(
                    f"Workflow input '{workflow_input.name}' requires an uploaded file reference"
                )
            relative_path = Path(value["path"])
            if (
                relative_path.is_absolute()
                or ".." in relative_path.parts
                or not relative_path.parts
                or relative_path.parts[0] != "uploads"
            ):
                raise InputFileError("Input file path must stay inside the project uploads area")
            source = (project_root / relative_path).resolve()
            try:
                source.relative_to(upload_root)
            except ValueError as exc:
                raise InputFileError("Input file path escaped the uploads area") from exc
            if not source.is_file():
                raise InputFileError(
                    f"Uploaded file for input '{workflow_input.name}' no longer exists"
                )
            target = (workspace_root / relative_path).resolve()
            try:
                target.relative_to(workspace_root)
            except ValueError as exc:
                raise InputFileError("Input file path escaped the run workspace") from exc
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            staged_sources.append(source)
            prepared[workflow_input.name] = {
                "path": relative_path.as_posix(),
                "name": str(value.get("name") or source.name),
                "size": target.stat().st_size,
            }
        return prepared, staged_sources

    def _runtime_environment(
        self,
        project: ProjectDetail,
        run_id: str,
        generated: Any,
        run_root: Path,
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
            "LOOMLANE_GATE_DIR": str(run_root / "gates"),
            "LOOMLANE_CHECKPOINT_DIR": str(run_root / "checkpoints"),
        }
        for profile in project.sdl.api_profiles:
            try:
                api_key = self.credentials.get(
                    project.id,
                    profile.id,
                    profile.base_url,
                )
                if not api_key and profile.library_id:
                    api_key = self.credentials.get_library(
                        profile.library_id,
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
        resume_checkpoint: Path | None,
    ) -> None:
        await self._set_meta(live, status="running", started_at=utc_now())
        stdout_task: asyncio.Task[None] | None = None
        stderr_task: asyncio.Task[str] | None = None
        error: str | None = None
        error_type = "RuntimeError"
        status = "failed"
        child_stderr = ""
        process_traceback: str | None = None
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
            if resume_checkpoint is not None:
                command.extend(["--resume", str(resume_checkpoint)])
            creation_flags = (
                subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
            )
            live.process = subprocess.Popen(
                command,
                cwd=live.run_root / "workspace",
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
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
                return_code = await asyncio.to_thread(
                    _wait_for_process,
                    live.process,
                    self.DEFAULT_TIMEOUT_SECONDS,
                )
            except subprocess.TimeoutExpired:
                live.process.kill()
                await asyncio.to_thread(live.process.wait)
                error = (
                    "Workflow process exceeded "
                    f"{self.DEFAULT_TIMEOUT_SECONDS} seconds"
                )
                return_code = -1

            await asyncio.gather(stdout_task, stderr_task)
            child_stderr = stderr_task.result()

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
            error = _exception_message(exc)
            error_type = exc.__class__.__name__
            process_traceback = traceback.format_exc()
            await self._stop_process(live.process)
        finally:
            await self._stop_process(live.process)
            pending_tasks = [
                task
                for task in (stdout_task, stderr_task)
                if task is not None and not task.done()
            ]
            if pending_tasks:
                _, still_pending = await asyncio.wait(
                    pending_tasks,
                    timeout=2,
                )
                for task in still_pending:
                    task.cancel()
            await asyncio.gather(
                *[
                    task
                    for task in (stdout_task, stderr_task)
                    if task is not None
                ],
                return_exceptions=True,
            )
            if (
                stderr_task
                and stderr_task.done()
                and not stderr_task.cancelled()
            ):
                try:
                    child_stderr = stderr_task.result()
                except Exception:
                    pass

            stderr_parts = []
            if child_stderr:
                stderr_parts.append(child_stderr.rstrip())
            if process_traceback:
                stderr_parts.append(process_traceback.rstrip())
            if stderr_parts:
                _atomic_write_text(
                    live.run_root / "stderr.log",
                    "\n\n".join(stderr_parts) + "\n",
                )

            if live.cancelled:
                status = "cancelled"
                error = None

            if not self._has_event(live, "run_finished"):
                if status == "failed":
                    if not self._has_event(live, "error"):
                        await self._emit_synthetic_event(
                            live,
                            event_file,
                            "error",
                            {
                                "type": error_type,
                                "message": error or "Workflow run failed",
                            },
                        )
                    await self._emit_synthetic_event(
                        live,
                        event_file,
                        "run_finished",
                        {
                            "status": "failed",
                            "error": error or "Workflow run failed",
                        },
                    )
                elif status == "cancelled":
                    await self._emit_synthetic_event(
                        live,
                        event_file,
                        "run_finished",
                        {"status": "cancelled"},
                    )
                elif status == "succeeded":
                    await self._emit_synthetic_event(
                        live,
                        event_file,
                        "run_finished",
                        {"status": "succeeded"},
                    )
            event_file.close()

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

    async def _stop_process(self, process: subprocess.Popen[bytes] | None) -> None:
        if process is None or process.poll() is not None:
            return
        try:
            process.kill()
        except OSError:
            pass
        await asyncio.to_thread(process.wait)

    def _has_event(self, live: _LiveRun, event_type: str) -> bool:
        return any(event.get("type") == event_type for event in live.events)

    async def _emit_synthetic_event(
        self,
        live: _LiveRun,
        event_file: Any,
        event_type: str,
        data: dict[str, Any],
    ) -> None:
        await self._publish_event(
            live,
            event_file,
            {
                "run_id": live.run_id,
                "ts": utc_now().isoformat(),
                "type": event_type,
                "node_id": None,
                "edge_id": None,
                "data": data,
            },
        )

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
        stream: BinaryIO,
        event_file: Any,
    ) -> None:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[bytes | BaseException | None] = asyncio.Queue()

        def enqueue(item: bytes | BaseException | None) -> None:
            try:
                loop.call_soon_threadsafe(queue.put_nowait, item)
            except RuntimeError:
                pass

        def pump() -> None:
            try:
                while line := stream.readline(self.MAX_EVENT_BYTES + 1):
                    enqueue(line)
            except BaseException as exc:
                enqueue(exc)
            finally:
                enqueue(None)

        pump_task = asyncio.create_task(asyncio.to_thread(pump))
        try:
            while True:
                line = await queue.get()
                if line is None:
                    break
                if isinstance(line, BaseException):
                    raise line
                if len(line) > self.MAX_EVENT_BYTES:
                    raise RunError("Workflow emitted an oversized event")
                text = line.decode("utf-8", errors="replace").strip()
                if not text:
                    continue
                try:
                    event = json.loads(text)
                except json.JSONDecodeError as exc:
                    raise RunError(
                        f"Workflow emitted invalid NDJSON: {exc}"
                    ) from exc
                if not isinstance(event, dict):
                    raise RunError("Workflow event must be a JSON object")
                await self._publish_event(live, event_file, event)
        finally:
            if not pump_task.done():
                try:
                    stream.close()
                except OSError:
                    pass
            await asyncio.gather(pump_task, return_exceptions=True)

    async def _publish_event(
        self,
        live: _LiveRun,
        event_file: Any,
        event: dict[str, Any],
    ) -> None:
        previous_seq = max(
            (
                sequence
                for item in live.events
                if isinstance((sequence := item.get("seq")), int)
            ),
            default=0,
        )
        seq = event.get("seq")
        if not isinstance(seq, int) or seq <= previous_seq:
            seq = previous_seq + 1
        event["seq"] = seq
        if len(live.events) >= self.MAX_EVENTS:
            raise RunError("Workflow exceeded the event count limit")
        event_file.write(json.dumps(event, ensure_ascii=False) + "\n")
        event_file.flush()
        event_type = event.get("type")
        event_data = event.get("data")
        gate_id = (
            event_data.get("gate_id")
            if isinstance(event_data, dict)
            else None
        )
        if event_type == "human_gate_waiting" and isinstance(gate_id, str):
            live.waiting_gate_ids.add(gate_id)
        elif event_type == "human_gate_resolved" and isinstance(gate_id, str):
            live.waiting_gate_ids.discard(gate_id)
        if event_type in {"human_gate_waiting", "human_gate_resolved"}:
            await self._set_meta(
                live,
                status="waiting" if live.waiting_gate_ids else "running",
            )
        async with self._lock:
            live.events.append(event)
            for queue in list(live.subscribers):
                queue.put_nowait(event)

    async def _read_stderr(self, stream: BinaryIO) -> str:
        return await asyncio.to_thread(self._read_stderr_sync, stream)

    def _read_stderr_sync(self, stream: BinaryIO) -> str:
        chunks: list[bytes] = []
        size = 0
        while True:
            chunk = stream.read(4096)
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

    def _with_output_directory(
        self,
        summary: RunSummary,
        run_root: Path,
    ) -> RunSummary:
        artifacts = run_root / "workspace" / "artifacts"
        artifacts.mkdir(parents=True, exist_ok=True)
        return summary.model_copy(
            update={
                "run_directory": run_root.name,
                "output_directory": str(artifacts.resolve()),
            }
        )

    def _find_run(self, run_id: str) -> tuple[Path, ProjectDetail]:
        if not re.fullmatch(r"[0-9a-f]{32}", run_id):
            raise RunNotFound(run_id)
        for project in self.store.list():
            runs_root = self.store.project_path(project.id) / "runs"
            if not runs_root.exists():
                continue
            for run_root in runs_root.iterdir():
                meta_path = run_root / "meta.json"
                if not run_root.is_dir() or not meta_path.exists():
                    continue
                try:
                    summary = self._read_meta(meta_path)
                except RunNotFound:
                    continue
                if summary.run_id == run_id:
                    return run_root, self.store.get(project.id)
        raise RunNotFound(run_id)

    async def get(self, run_id: str) -> RunDetail:
        run_root, _ = self._find_run(run_id)
        summary = self._with_output_directory(
            self._read_meta(run_root / "meta.json"),
            run_root,
        )
        result = self._read_result(run_root / "result.json")
        events = self._read_events_file(run_root)
        pending_gates = self._read_pending_gates(run_root)
        return RunDetail(
            **summary.model_dump(),
            result=result or None,
            events=events,
            pending_gates=pending_gates,
        )

    def _read_pending_gates(self, run_root: Path) -> list[PendingHumanGate]:
        gates_dir = run_root / "gates"
        if not gates_dir.exists():
            return []
        pending: list[PendingHumanGate] = []
        for path in sorted(gates_dir.glob("gate-*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                if payload.get("decision") not in {None, ""}:
                    continue
                pending.append(
                    PendingHumanGate(
                        gate_id=payload["gate_id"],
                        edge_id=payload["edge_id"],
                        prompt=payload["prompt"],
                        requested_at=payload["requested_at"],
                        timeout_seconds=payload.get("timeout_seconds"),
                    )
                )
            except (OSError, ValueError, KeyError, TypeError):
                continue
        return pending

    async def decide_gate(
        self,
        run_id: str,
        gate_id: str,
        request: GateDecisionRequest,
    ) -> GateDecisionResponse:
        run_root, _ = self._find_run(run_id)
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", gate_id):
            raise GateNotFound(gate_id)
        async with self._lock:
            live = self._live.get(run_id)
            if live is None or live.finished:
                raise GateConflict("Run is not waiting for human input")
        summary = self._read_meta(run_root / "meta.json")
        if summary.status != "waiting":
            raise GateConflict("Run is not waiting for human input")
        gate_path = run_root / "gates" / f"{gate_id}.json"
        if not gate_path.exists():
            raise GateNotFound(gate_id)
        lock_path = gate_path.with_suffix(".lock")
        deadline = time.monotonic() + 2
        while True:
            try:
                lock = lock_path.open("x", encoding="utf-8")
                break
            except FileExistsError:
                if time.monotonic() >= deadline:
                    raise GateConflict(
                        f"Gate '{gate_id}' is being decided"
                    )
                await asyncio.sleep(0.02)
            except OSError as exc:
                raise GateConflict(
                    f"Gate '{gate_id}' could not be decided"
                ) from exc
        try:
            try:
                payload = json.loads(gate_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise GateNotFound(gate_id) from exc
            if payload.get("decision") not in {None, ""}:
                raise GateConflict(f"Gate '{gate_id}' has already been decided")
            payload.update(
                {
                    "decision": request.decision,
                    "comment": request.comment,
                    "decided_at": utc_now().isoformat(),
                    "resolved_by": "human",
                }
            )
            _atomic_write_json(gate_path, payload)
        finally:
            lock.close()
            try:
                lock_path.unlink()
            except FileNotFoundError:
                pass
        return GateDecisionResponse(
            run_id=run_id,
            gate_id=gate_id,
            decision=request.decision,
            accepted_at=utc_now(),
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
                summaries.append(
                    self._with_output_directory(self._read_meta(meta), path)
                )
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
        run_root = self._find_run(run_id)[0]
        summary = self._with_output_directory(
            self._read_meta(run_root / "meta.json"),
            run_root,
        )
        if summary.status in {"succeeded", "failed", "cancelled"}:
            return summary
        return summary

    async def open_output_folder(self, run_id: str) -> str:
        run_root, _ = self._find_run(run_id)
        output_directory = (run_root / "workspace" / "artifacts").resolve()
        workspace_root = (run_root / "workspace").resolve()
        try:
            output_directory.relative_to(workspace_root)
        except ValueError as exc:
            raise RunFolderError("Run output directory is outside the workspace") from exc
        output_directory.mkdir(parents=True, exist_ok=True)
        try:
            _launch_directory(output_directory)
        except OSError as exc:
            raise RunFolderError(f"Unable to open output directory: {exc}") from exc
        return str(output_directory)

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
