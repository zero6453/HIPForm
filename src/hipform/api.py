"""Swagger-documented local API for configured HIP simulation jobs."""

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Body, FastAPI, File, HTTPException, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import Field, model_validator
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .config import Settings, SimulationConfig
from .inputs import StepTooLarge
from .jobs import JobStore


class ConfigRequest(Settings):
    name: str = Field(min_length=1, max_length=120, description="Name of this process/material parameter set")
    config: SimulationConfig = Field(examples=[SimulationConfig().model_dump(mode="json")])


class ConfigRecord(ConfigRequest):
    id: str
    created_at: str
    updated_at: str
    calibrated: bool = False


class DefaultConfigResponse(Settings):
    config: SimulationConfig = Field(examples=[SimulationConfig().model_dump(mode="json")])
    calibrated: Literal[False] = False
    units: dict[str, str] = Field(examples=[{"length": "mm", "pressure": "MPa", "time": "s", "temperature": "C"}])


class JobRequest(Settings):
    name: str = Field(default="HIP simulation", min_length=1, max_length=120)
    workflow: Literal["auto", "verify", "legacy"] = Field(default="auto", description="verify compares predicted cavity+jobid.step to target cavity.step; auto selects verify for vented capsules")
    cavity_path: Path | None = Field(default=None, description="Absolute target finished-part cavity.step path on the API host")
    capsule_path: Path | None = Field(default=None, description="Absolute capsule.step wall and vent path on the API host")
    input_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$",
                                description="ID returned by POST /api/inputs; replaces both local paths")
    config: SimulationConfig | None = Field(default=None, description="Inline process/material configuration; saved as an immutable job snapshot")
    config_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$",
                                 description="Saved configuration ID; mutually exclusive with inline config")

    @model_validator(mode="after")
    def sources(self):
        if (self.config is None) == (self.config_id is None):
            raise ValueError("Provide exactly one of config or config_id")
        if self.input_id:
            if self.cavity_path is not None or self.capsule_path is not None:
                raise ValueError("input_id cannot be combined with local STEP paths")
        elif self.cavity_path is None or self.capsule_path is None:
            raise ValueError("Provide both cavity_path and capsule_path, or input_id")
        for path in (self.cavity_path, self.capsule_path):
            if path is not None and not path.is_absolute():
                raise ValueError("Local STEP paths must be absolute paths on the API host")
        return self


class JobError(Settings):
    code: str
    message: str
    details: dict = Field(default_factory=dict)


class JobResponse(Settings):
    id: str
    name: str = "HIP simulation"
    status: Literal["queued", "running", "completed", "failed"]
    created_at: str
    updated_at: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    config_id: str | None = None
    workflow: Literal["auto", "verify", "legacy"] = "auto"
    config: SimulationConfig
    inputs: dict = Field(default_factory=dict)
    error: JobError | None = None
    result: dict | None = None
    returncode: int | None = None
    status_url: str
    report_url: str | None = None
    artifacts: dict[str, str] = Field(default_factory=dict)


class InputResponse(Settings):
    id: str
    created_at: str
    filenames: dict[str, str]


def create_app(data_dir: Path | None = None, *, job_timeout_s=3600):
    store = JobStore(data_dir or Path("simulation-runs/api"), timeout_s=job_timeout_s)

    @asynccontextmanager
    async def lifespan(app):
        store.start()
        try:
            yield
        finally:
            store.close()

    app = FastAPI(title="HIPForm Simulation API", version="0.1.0", lifespan=lifespan,
                  description="CAD-to-HIP simulation: upload STEP inputs, configure TC4/material data and "
                  "temperature/pressure/time points, submit an asynchronous job, then retrieve its report. "
                  "The model is uncalibrated; a completed job is not engineering acceptance. "
                  "Local single-worker service. All dimensions are mm, stress/pressure MPa, time s, temperature Celsius.")
    app.state.store = store
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])

    @app.middleware("http")
    async def require_same_origin(request, call_next):
        # HTML forms can send multipart uploads cross-origin without CORS permission.
        origin = request.headers.get("origin")
        if request.method not in {"GET", "HEAD", "OPTIONS"} and origin is not None:
            if origin != str(request.base_url).removesuffix("/"):
                return JSONResponse(status_code=403, content={"detail": "Cross-origin writes are not allowed"})
        return await call_next(request)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        # Rejected values can include infinities or exceptions, which are not JSON.
        return JSONResponse(status_code=422, content={"detail": [
            {key: error[key] for key in ("loc", "msg", "type") if key in error}
            for error in exc.errors()]})

    def get_job(job_id):
        try:
            return store.get_job(job_id)
        except KeyError:
            raise HTTPException(404, "Job not found") from None

    def present(job):
        result = dict(job)
        base = f'/api/jobs/{job["id"]}'
        result["status_url"] = base
        artifacts = store.available_artifacts(job)
        result["artifacts"] = {name: f"{base}/artifacts/{name}" for name in artifacts}
        result["report_url"] = f"{base}/report" if "report.html" in artifacts else None
        return result

    @app.get("/", include_in_schema=False)
    def root():
        return RedirectResponse("/docs")

    @app.get("/health", tags=["Service"], summary="Service readiness")
    def health() -> dict[str, str]:
        return {"status": "ok", "worker": "serial-subprocess", "model": "uncalibrated"}

    @app.get("/api/configs/default", response_model=DefaultConfigResponse, tags=["Configuration"],
             summary="Get editable default process/material parameters")
    def default_config():
        return {"config": SimulationConfig().model_dump(mode="json"), "calibrated": False,
                "units": {"length": "mm", "pressure": "MPa", "time": "s", "temperature": "C"}}

    @app.get("/api/configs", response_model=list[ConfigRecord], tags=["Configuration"])
    def list_configs():
        return store.list_configs()

    @app.post("/api/configs", status_code=201, response_model=ConfigRecord, tags=["Configuration"],
              summary="Save a temperature/pressure/time curve and powder/capsule material parameters")
    def create_config(request: ConfigRequest):
        return store.save_config(request.name, request.config)

    @app.get("/api/configs/{config_id}", response_model=ConfigRecord, tags=["Configuration"])
    def get_config(config_id: str):
        try:
            return store.get_config(config_id)
        except KeyError:
            raise HTTPException(404, "Configuration not found") from None

    @app.put("/api/configs/{config_id}", response_model=ConfigRecord, tags=["Configuration"],
             summary="Replace a saved configuration; existing jobs keep their snapshots")
    def update_config(config_id: str, request: ConfigRequest):
        try:
            return store.save_config(request.name, request.config, config_id)
        except KeyError:
            raise HTTPException(404, "Configuration not found") from None

    @app.post("/api/inputs", status_code=201, response_model=InputResponse, tags=["Inputs"],
              summary="Upload cavity.step and capsule.step (maximum 100 MiB each)")
    def upload_inputs(cavity: Annotated[UploadFile, File(description="Target finished-part cavity.step")],
                      capsule: Annotated[UploadFile, File(description="Capsule wall and vent capsule.step")]):
        try:
            return store.save_inputs({role: (upload.filename or "", upload.file)
                                      for role, upload in {"cavity": cavity, "capsule": capsule}.items()})
        except StepTooLarge as exc:
            raise HTTPException(413, str(exc)) from exc
        except (ValueError, OSError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/api/jobs", status_code=202, response_model=JobResponse, tags=["Simulations"],
              summary="Queue a simulation with STEP inputs and an explicit configuration source",
              description="Provide input_id OR two absolute STEP paths; provide config OR config_id. "
                  "cavity.step is the finished target; powder is derived from capsule.step and the straight vent is virtually capped. "
                  "A geometry gap fails the job with code material_gap. "
              "Accepted jobs return immediately; poll status_url. Both successful and failed jobs have report_url.")
    def submit_job(request: Annotated[JobRequest, Body(openapi_examples={
        "local_steps": {"summary": "Local STEP paths with inline process parameters",
                        "value": {"name": "HIP verification", "cavity_path": "/absolute/path/cavity.step",
                                  "capsule_path": "/absolute/path/capsule.step",
                                  "config": SimulationConfig().model_dump(mode="json")}},
        "uploaded_steps": {"summary": "Uploaded files and saved configuration",
                           "value": {"input_id": "REPLACE_WITH_INPUT_ID", "config_id": "REPLACE_WITH_CONFIG_ID"}},
    })]):
        try:
            return present(store.submit(request))
        except KeyError:
            raise HTTPException(404, "Input or configuration ID not found") from None
        except (OSError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(503, str(exc)) from exc

    @app.get("/api/jobs", response_model=list[JobResponse], tags=["Simulations"], summary="List persisted jobs, newest first")
    def list_jobs():
        return [present(job) for job in store.list_jobs()]

    @app.get("/api/jobs/{job_id}", response_model=JobResponse, tags=["Simulations"], summary="Poll job status and report/model URLs")
    def job_status(job_id: str):
        return present(get_job(job_id))

    @app.get("/api/jobs/{job_id}/report", response_class=FileResponse, tags=["Results"],
             summary="Open the successful simulation report or the failure report")
    def report(job_id: str):
        job = get_job(job_id)
        if job["status"] not in {"completed", "failed"}:
            raise HTTPException(409, "Report is not ready; poll job status")
        try:
            return FileResponse(store.artifact(job_id, "report.html"), media_type="text/html")
        except KeyError:
            raise HTTPException(404, "Report not found") from None

    @app.get("/api/jobs/{job_id}/artifacts/{name}", response_class=FileResponse, tags=["Results"],
             summary="Download a generated model, configuration, numerical result or log")
    def artifact(job_id: str, name: str):
        try:
            path = store.artifact(job_id, name)
        except KeyError:
            raise HTTPException(404, "Artifact not available") from None
        return FileResponse(path, filename=name)

    return app
