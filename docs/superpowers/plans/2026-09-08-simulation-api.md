# HIPForm Simulation API Implementation Plan

**Goal:** Accept CAD outputs and explicit process/material configuration through
a documented local HTTP API, reject incompatible geometry, and return a report
and predicted powder surface for each simulation.

**Architecture:** FastAPI provides Swagger and validates the existing Pydantic
configuration models. A single background worker launches the existing CLI in
isolated subprocesses. Each job snapshots inputs and resolved configuration,
persists its status, and serves only an allowlist of generated artifacts.
The solver and its physical assumptions remain the existing HIPForm model.

**Tech Stack:** Python 3.12, FastAPI, Uvicorn, Pydantic, Gmsh, pytest, HTTPX.

## Contract

- `/docs`: Swagger UI; `/openapi.json`: schemas; `/health`: service readiness.
- `/api/configs/default`: complete editable default process/material parameters.
- `/api/configs`: create/list named configurations; `/{id}`: read/update.
- `/api/inputs`: upload cavity and capsule STEP files for cross-process callers.
- `/api/jobs`: submit with uploaded inputs or local paths, plus exactly one of
  an inline config or saved config ID. Return 202 and a job ID immediately.
- `/api/jobs/{id}`: queued/running/completed/failed status and structured error.
- `/api/jobs/{id}/report`: HTML report, including a readable failure report.
- `/api/jobs/{id}/artifacts/{name}`: allowlisted model, history, config and logs.
- Source solids and user parameters must never be silently scaled, filled,
  sealed, replaced, or reset to defaults. Saved configuration updates must not
  alter already submitted jobs.
- Output model is the existing predicted powder STL and assembly VTU, not a
  reconstructed analytic STEP or a calibrated manufacturing acceptance result.

## Tasks

- [x] 1. Review geometry/configuration/reporting behavior; reproduce missing
  contracts with gap, invalid configuration, and failure-report tests.
- [x] 2. Add typed geometry errors and early positive-distance rejection; retain
  overlap, exposed-powder and sealed-exterior checks. Emit failure HTML and
  structured JSON without overwriting an existing run.
- [x] 3. Implement validated configuration and immutable job/input storage,
  serial subprocess execution, restart failure recovery, and shutdown cleanup.
  Test config snapshotting, input validation, job success/failure and artifacts.
- [x] 4. Add FastAPI routes, file uploads, Swagger schemas/examples, and
  `hipform serve`; test with the real ASGI client and tiny generated STEP solids.
- [x] 5. Update integration documentation and runnable sample requests. Run all
  tests, independently review the change, exercise current STEP files over HTTP,
  and leave a local server running with the Swagger URL.

## Verification

Run focused tests before implementation to observe failures, then the complete
suite after implementation. End-to-end tests must use the actual CLI process,
generate a valid small bonded/sealed two-material mesh, fetch a generated HTML
report and predicted STL, and verify that a separated input fails with the exact
requested message. Invalid curves/densities and both/missing config sources must
be rejected before queueing. Verify no path traversal or stale artifacts can be
served; interrupted jobs must not remain reported as running after restart.

## Verification Results

- Full suite: 98 passed; two third-party test-client deprecation warnings.
- `uv build`, `uv pip check`, and `git diff --check` passed.
- Swagger rendered in the browser; Try it out returned the full default config.
- Original supplied STEP job `116839c5c9864fa6bdd2585fa5103d48` rejected the
  open vent with `open_capsule` and served a failure report over HTTP.
- Synthetic L demo job `1e3f28e4be7e444d9b2b0b6613f59989` completed through
  HTTP and served its report and predicted STL. It is not the supplied model.
- Independent geometry and API reviews completed; regression tests cover the
  identified lifecycle, configuration, gap, and sealed-vent issues.
- Local API left running at `http://127.0.0.1:8000/docs`.
