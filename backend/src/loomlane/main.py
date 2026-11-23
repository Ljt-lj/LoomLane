from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Header, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse

from . import __version__
from .compiler import COMPILER_VERSION, WorkflowCompiler
from .models import (
    ApiProfile,
    GateDecisionRequest,
    GateDecisionResponse,
    GenerateResponse,
    ImportSDLRequest,
    InputFileRef,
    OpenFolderResponse,
    ProjectCreateRequest,
    ProjectDetail,
    ProjectSummary,
    ProjectUpdateRequest,
    RunDetail,
    RunResumeOptions,
    RunResumeRequest,
    RunStartRequest,
    RunSummary,
    SecretImportRequest,
    SecretPutRequest,
    SecretStatus,
    ValidateWorkflowRequest,
    ValidationResponse,
)
from .runtime import (
    ActiveRunExists,
    GateConflict,
    GateNotFound,
    InputFileError,
    RunFolderError,
    RunConfigurationError,
    RunManager,
    RunNotFound,
    RunResumeConflict,
)
from .secrets import CredentialError, CredentialStore
from .storage import (
    ApiProfileConflict,
    ApiProfileNotFound,
    ApiProfileStore,
    InvalidWorkflow,
    ProjectNotFound,
    ProjectStore,
    RevisionConflict,
    StorageWriteError,
)
from .validator import validate_sdl

logger = logging.getLogger(__name__)


def create_app(
    *,
    store: ProjectStore | None = None,
    api_profiles: ApiProfileStore | None = None,
    credentials: CredentialStore | None = None,
    manager: RunManager | None = None,
) -> FastAPI:
    project_store = store or ProjectStore()
    api_profile_store = api_profiles or ApiProfileStore()
    credential_store = credentials or CredentialStore()
    run_manager = manager or RunManager(
        project_store,
        credential_store,
        WorkflowCompiler(),
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        project_store.ensure_seed()
        try:
            yield
        finally:
            await run_manager.shutdown()

    app = FastAPI(
        title="LoomLane API",
        version=__version__,
        lifespan=lifespan,
    )
    app.state.store = project_store
    app.state.api_profiles = api_profile_store
    app.state.credentials = credential_store
    app.state.runs = run_manager
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1:5173",
            "http://localhost:5173",
        ],
        allow_origin_regex=r"^http://(?:127\.0\.0\.1|localhost):\d+$",
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(ProjectNotFound)
    async def project_not_found(_: Request, exc: ProjectNotFound) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"detail": "Project not found", "project_id": str(exc)},
        )

    @app.exception_handler(ApiProfileNotFound)
    async def api_profile_not_found(
        _: Request,
        exc: ApiProfileNotFound,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"detail": "API Profile not found", "profile_id": str(exc)},
        )

    @app.exception_handler(ApiProfileConflict)
    async def api_profile_conflict(
        _: Request,
        exc: ApiProfileConflict,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"detail": "API Profile already exists", "profile_id": str(exc)},
        )

    @app.exception_handler(RunNotFound)
    async def run_not_found(_: Request, exc: RunNotFound) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"detail": "Run not found", "run_id": str(exc)},
        )

    @app.exception_handler(RevisionConflict)
    async def revision_conflict(_: Request, exc: RevisionConflict) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"detail": str(exc)},
        )

    @app.exception_handler(ActiveRunExists)
    async def active_run_exists(_: Request, exc: ActiveRunExists) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"detail": str(exc)},
        )

    @app.exception_handler(RunResumeConflict)
    async def run_resume_conflict(_: Request, exc: RunResumeConflict) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"detail": str(exc)},
        )

    @app.exception_handler(RunConfigurationError)
    async def run_configuration_error(
        _: Request,
        exc: RunConfigurationError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": str(exc)},
        )

    @app.exception_handler(InputFileError)
    async def input_file_error(_: Request, exc: InputFileError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": str(exc)},
        )

    @app.exception_handler(RunFolderError)
    async def run_folder_error(_: Request, exc: RunFolderError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": str(exc)},
        )

    @app.exception_handler(GateNotFound)
    async def gate_not_found(_: Request, exc: GateNotFound) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"detail": "Human gate not found", "gate_id": str(exc)},
        )

    @app.exception_handler(GateConflict)
    async def gate_conflict(_: Request, exc: GateConflict) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"detail": str(exc)},
        )

    @app.exception_handler(CredentialError)
    async def credential_error(_: Request, exc: CredentialError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"detail": str(exc)},
        )

    @app.exception_handler(InvalidWorkflow)
    async def invalid_workflow(_: Request, exc: InvalidWorkflow) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"detail": "Workflow validation failed", "issues": exc.issues},
        )

    @app.exception_handler(StorageWriteError)
    async def storage_write_error(
        _: Request,
        exc: StorageWriteError,
    ) -> JSONResponse:
        cause = exc.__cause__ or exc
        logger.error(
            "Project storage write failed for %s (errno=%s winerror=%s)",
            exc.path,
            exc.errno,
            exc.winerror,
            exc_info=(type(cause), cause, cause.__traceback__),
        )
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "detail": str(exc),
                "code": "project_storage_write_failed",
            },
        )

    def project_or_404(project_id: str) -> ProjectDetail:
        return project_store.get(project_id)

    def profile_or_404(project: ProjectDetail, profile_id: str):
        profile = next(
            (
                item
                for item in project.sdl.api_profiles
                if item.id == profile_id
            ),
            None,
        )
        if profile is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="API Profile not found",
            )
        return profile

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {
            "status": "ok",
            "version": __version__,
            "compiler_version": COMPILER_VERSION,
        }

    @app.get("/api/api-profiles", response_model=list[ApiProfile])
    async def list_api_profiles() -> list[ApiProfile]:
        return api_profile_store.list()

    @app.post(
        "/api/api-profiles",
        response_model=ApiProfile,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_api_profile(payload: ApiProfile) -> ApiProfile:
        return api_profile_store.create(payload)

    @app.put("/api/api-profiles/{profile_id}", response_model=ApiProfile)
    async def update_api_profile(
        profile_id: str,
        payload: ApiProfile,
    ) -> ApiProfile:
        return api_profile_store.update(profile_id, payload)

    @app.delete(
        "/api/api-profiles/{profile_id}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def delete_api_profile(profile_id: str) -> None:
        api_profile_store.delete(profile_id)
        try:
            credential_store.delete_library(profile_id)
        except CredentialError:
            # Metadata removal is authoritative; an unavailable OS vault must
            # not make the profile deletion appear to have failed.
            pass

    @app.get(
        "/api/api-profiles/{profile_id}/secret/status",
        response_model=SecretStatus,
    )
    async def get_library_secret_status(profile_id: str) -> SecretStatus:
        profile = api_profile_store.get(profile_id)
        return credential_store.status_library(profile_id, profile.base_url)

    @app.put(
        "/api/api-profiles/{profile_id}/secret",
        response_model=SecretStatus,
    )
    async def put_library_secret(
        profile_id: str,
        payload: SecretPutRequest,
    ) -> SecretStatus:
        profile = api_profile_store.get(profile_id)
        return credential_store.put_library(
            profile_id,
            payload.api_key,
            profile.base_url,
        )

    @app.delete(
        "/api/api-profiles/{profile_id}/secret",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def delete_library_secret(profile_id: str) -> None:
        api_profile_store.get(profile_id)
        credential_store.delete_library(profile_id)

    @app.post(
        "/api/api-profiles/{profile_id}/secret/import-from-project",
        response_model=SecretStatus,
    )
    async def import_library_secret(
        profile_id: str,
        payload: SecretImportRequest,
    ) -> SecretStatus:
        library_profile = api_profile_store.get(profile_id)
        project = project_or_404(payload.project_id)
        project_profile = profile_or_404(project, payload.profile_id)
        status_result = credential_store.copy_to_library(
            project.id,
            project_profile.id,
            profile_id,
            library_profile.base_url,
        )
        if status_result is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No compatible project credential is configured",
            )
        return status_result

    @app.get("/api/projects", response_model=list[ProjectSummary])
    async def list_projects() -> list[ProjectSummary]:
        project_store.ensure_seed()
        return project_store.list()

    @app.post(
        "/api/projects",
        response_model=ProjectDetail,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_project(payload: ProjectCreateRequest) -> ProjectDetail:
        return project_store.create(payload.name, payload.sdl, payload.canvas)

    @app.get("/api/projects/{project_id}", response_model=ProjectDetail)
    async def get_project(project_id: str) -> ProjectDetail:
        return project_or_404(project_id)

    @app.put("/api/projects/{project_id}", response_model=ProjectDetail)
    async def update_project(
        project_id: str,
        payload: ProjectUpdateRequest,
    ) -> ProjectDetail:
        return project_store.save(
            project_id,
            payload.sdl,
            payload.canvas,
            payload.base_revision,
        )

    @app.delete("/api/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_project(project_id: str) -> None:
        if await run_manager.is_active(project_id):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Stop the active run before deleting this project",
            )
        project_store.delete(project_id)

    @app.post(
        "/api/projects/{project_id}/validate",
        response_model=ValidationResponse,
    )
    async def validate_project(
        project_id: str,
        payload: ValidateWorkflowRequest,
    ) -> ValidationResponse:
        project_or_404(project_id)
        return validate_sdl(payload.sdl)

    @app.post(
        "/api/projects/{project_id}/import-sdl",
        response_model=ProjectDetail,
    )
    async def import_project_sdl(
        project_id: str,
        payload: ImportSDLRequest,
        base_revision: int | None = Query(default=None),
    ) -> ProjectDetail:
        return project_store.import_sdl(project_id, payload.text, base_revision)

    @app.get(
        "/api/projects/{project_id}/sdl",
        response_class=PlainTextResponse,
    )
    async def get_project_sdl(project_id: str) -> PlainTextResponse:
        project_or_404(project_id)
        return PlainTextResponse(
            project_store.read_sdl_text(project_id),
            media_type="application/yaml",
        )

    @app.post(
        "/api/projects/{project_id}/generate",
        response_model=GenerateResponse,
    )
    async def generate_project(project_id: str) -> GenerateResponse:
        project = project_or_404(project_id)
        result = run_manager.compiler.compile(
            project.sdl,
            project_store.project_path(project_id),
        )
        project_store.set_last_build(project_id, result.compiler_key)
        return result

    @app.get(
        "/api/projects/{project_id}/secrets/{profile_id}/status",
        response_model=SecretStatus,
    )
    async def get_secret_status(
        project_id: str,
        profile_id: str,
    ) -> SecretStatus:
        project = project_or_404(project_id)
        profile = profile_or_404(project, profile_id)
        status_result = credential_store.status(
            project_id,
            profile_id,
            profile.base_url,
        )
        if status_result.configured or not profile.library_id:
            return status_result
        library_status = credential_store.status_library(
            profile.library_id,
            profile.base_url,
        )
        if library_status.configured:
            return library_status.model_copy(update={"profile_id": profile_id})
        return status_result

    @app.put(
        "/api/projects/{project_id}/secrets/{profile_id}",
        response_model=SecretStatus,
    )
    async def put_secret(
        project_id: str,
        profile_id: str,
        payload: SecretPutRequest,
    ) -> SecretStatus:
        project = project_or_404(project_id)
        profile = profile_or_404(project, profile_id)
        return credential_store.put(
            project_id,
            profile_id,
            payload.api_key,
            profile.base_url,
        )

    @app.delete(
        "/api/projects/{project_id}/secrets/{profile_id}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def delete_secret(project_id: str, profile_id: str) -> None:
        project = project_or_404(project_id)
        profile_or_404(project, profile_id)
        credential_store.delete(project_id, profile_id)

    @app.post(
        "/api/projects/{project_id}/runs",
        response_model=RunSummary,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def start_run(
        project_id: str,
        payload: RunStartRequest,
    ) -> RunSummary:
        project_or_404(project_id)
        return await run_manager.start(project_id, payload)

    @app.post(
        "/api/projects/{project_id}/runs/resume",
        response_model=RunSummary,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def resume_run(
        project_id: str,
        payload: RunResumeRequest,
    ) -> RunSummary:
        project_or_404(project_id)
        return await run_manager.resume(project_id, payload)

    @app.post(
        "/api/projects/{project_id}/input-files",
        response_model=InputFileRef,
        status_code=status.HTTP_201_CREATED,
    )
    async def upload_input_file(
        project_id: str,
        request: Request,
        input_name: str = Query(min_length=1),
        file_name: str = Query(min_length=1, max_length=255),
    ) -> InputFileRef:
        project_or_404(project_id)
        return run_manager.stage_input_file(
            project_id,
            input_name,
            file_name,
            await request.body(),
        )

    @app.get(
        "/api/projects/{project_id}/runs",
        response_model=list[RunSummary],
    )
    async def list_runs(project_id: str) -> list[RunSummary]:
        project_or_404(project_id)
        return await run_manager.list_for_project(project_id)

    @app.get("/api/runs/{run_id}", response_model=RunDetail)
    async def get_run(run_id: str) -> RunDetail:
        return await run_manager.get(run_id)

    @app.get(
        "/api/runs/{run_id}/resume-options",
        response_model=RunResumeOptions,
    )
    async def get_resume_options(run_id: str) -> RunResumeOptions:
        return await run_manager.resume_options(run_id)

    @app.post(
        "/api/runs/{run_id}/open-folder",
        response_model=OpenFolderResponse,
    )
    async def open_run_folder(run_id: str) -> OpenFolderResponse:
        return OpenFolderResponse(
            path=await run_manager.open_output_folder(run_id),
            opened=True,
        )

    @app.post(
        "/api/runs/{run_id}/gates/{gate_id}",
        response_model=GateDecisionResponse,
    )
    async def decide_gate(
        run_id: str,
        gate_id: str,
        payload: GateDecisionRequest,
    ) -> GateDecisionResponse:
        return await run_manager.decide_gate(run_id, gate_id, payload)

    @app.get("/api/runs/{run_id}/events")
    async def run_events(
        run_id: str,
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
        after: int = Query(default=0, ge=0),
    ) -> StreamingResponse:
        if last_event_id:
            try:
                after = max(after, int(last_event_id))
            except ValueError:
                pass

        async def stream() -> AsyncIterator[str]:
            try:
                async for event in run_manager.events(run_id, after_seq=after):
                    payload = json.dumps(event, ensure_ascii=False)
                    sequence = int(event.get("seq", 0))
                    event_type = str(event.get("type", "message"))
                    yield (
                        f"id: {sequence}\n"
                        f"event: {event_type}\n"
                        f"data: {payload}\n\n"
                    )
            except RunNotFound:
                yield (
                    "event: stream_error\n"
                    'data: {"message":"Run not found"}\n\n'
                )

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    @app.post("/api/runs/{run_id}/cancel", response_model=RunSummary)
    async def cancel_run(run_id: str) -> RunSummary:
        return await run_manager.cancel(run_id)

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("loomlane.main:app", host="127.0.0.1", port=8000, reload=True)
