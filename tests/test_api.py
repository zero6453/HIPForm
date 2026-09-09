"""Real HTTP contracts and isolated CLI jobs with small analytic CAD inputs."""

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

from fastapi.testclient import TestClient
import gmsh
import pytest

from hipform.config import SimulationConfig


@pytest.fixture
def app(tmp_path, monkeypatch):
    from hipform.api import create_app
    monkeypatch.chdir(tmp_path)
    return create_app(tmp_path / "api")


@pytest.fixture
def client(app):
    with TestClient(app) as client:
        yield client


@pytest.fixture
def solids(tmp_path):
    paths = {}
    for role in ("cavity", "capsule", "gap"):
        path = tmp_path / f"{role}.step"
        gmsh.initialize(readConfigFiles=False)
        try:
            gmsh.option.setNumber("General.Terminal", 0)
            gmsh.model.add(role)
            if role == "capsule":
                outer = gmsh.model.occ.addBox(-6, -6, -6, 12, 12, 12)
                inner = gmsh.model.occ.addBox(-5, -5, -5, 10, 10, 10)
                gmsh.model.occ.cut([(3, outer)], [(3, inner)])
            else:
                half = 4 if role == "gap" else 5
                gmsh.model.occ.addBox(-half, -half, -half, 2*half, 2*half, 2*half)
            gmsh.model.occ.synchronize()
            gmsh.write(str(path))
        finally:
            gmsh.finalize()
        paths[role] = str(path)
    return paths


def config():
    return SimulationConfig.model_validate({
        "powder": {"initial_relative_density": 0.72},
        "solver": {"mesh_size_mm": 4},
        "cycle": [{"time_s": 0, "temperature_c": 20, "pressure_mpa": 0},
                  {"time_s": 60, "temperature_c": 20, "pressure_mpa": 0}],
    }).model_dump(mode="json")


def wait_job(client, job_id):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        response = client.get(f"/api/jobs/{job_id}")
        assert response.status_code == 200
        job = response.json()
        if job["status"] in ("completed", "failed"):
            return job
        time.sleep(.05)
    pytest.fail(f"Job did not finish: {job}")


def test_swagger_and_full_configuration_schema(client):
    assert client.get("/docs").status_code == 200
    schema = client.get("/openapi.json").json()
    assert "/api/jobs" in schema["paths"]
    assert "/api/inputs" not in schema["paths"]
    assert set(schema["components"]["schemas"]["JobRequest"]["properties"]) == {"name", "config", "config_id"}
    assert "/api/configs/{config_id}" in schema["paths"]
    response_schema = schema["paths"]["/api/configs/default"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    default_schema = schema["components"]["schemas"][response_schema["$ref"].rsplit("/", 1)[-1]]
    assert {"config", "calibrated", "units"} <= default_schema["properties"].keys()
    for model in ("ConfigRequest", "DefaultConfigResponse"):
        example = schema["components"]["schemas"][model]["properties"]["config"]["examples"][0]
        assert SimulationConfig.model_validate(example) == SimulationConfig()
    default = client.get("/api/configs/default").json()
    assert default["config"]["powder"]["initial_relative_density"] == .65
    assert "cycle" in default["config"]
    assert default["calibrated"] is False


def test_config_create_update_and_validation(client):
    created = client.post("/api/configs", json={"name": "TC4 test", "config": config()})
    assert created.status_code == 201, created.text
    saved = created.json()
    assert saved["config"]["powder"]["initial_relative_density"] == .72
    modified = config()
    modified["cycle"][-1]["time_s"] = 240
    assert client.put(f'/api/configs/{saved["id"]}', json={"name": "revised", "config": modified}).status_code == 200
    assert client.get(f'/api/configs/{saved["id"]}').json()["config"]["cycle"][-1]["time_s"] == 240
    assert len(client.get("/api/configs").json()) == 1
    modified["powder"]["initial_relative_density"] = 1.1
    assert client.post("/api/configs", json={"name": "bad", "config": modified}).status_code == 422


@pytest.mark.parametrize("change", ["missing_config", "both_configs", "bad_curve", "missing_input", "old_input"])
def test_invalid_job_rejected_before_queueing(client, solids, change):
    payload = {"config": config()}
    if change == "missing_config":
        del payload["config"]
    elif change == "both_configs":
        payload["config_id"] = "a" * 32
    elif change == "bad_curve":
        payload["config"]["cycle"][1]["time_s"] = 0
    elif change == "missing_input":
        Path(solids["cavity"]).unlink()
    else:
        payload["input_id"] = "b" * 32
    assert client.post("/api/jobs", json=payload).status_code == 422
    assert client.get("/api/jobs").json() == []


def test_real_simulation_config_snapshot_report_and_model(client, solids):
    saved = client.post("/api/configs", json={"name": "process", "config": config()}).json()
    submitted = client.post("/api/jobs", json={"config_id": saved["id"]})
    assert submitted.status_code == 202, submitted.text
    job_id = submitted.json()["id"]
    original_hash = hashlib.sha256(Path(solids["cavity"]).read_bytes()).hexdigest()
    for role in ("cavity", "capsule"):
        Path(solids[role]).unlink()
    modified = config()
    modified["powder"]["initial_relative_density"] = .8
    client.put(f'/api/configs/{saved["id"]}', json={"name": "updated", "config": modified})
    job = wait_job(client, job_id)
    assert job["status"] == "completed", job
    assert job["result"]["engineering_acceptance"] == "not_assessed"
    assert job["workflow"] == "verify"
    assert job["inputs"]["cavity"] == {"filename": "cavity.step", "sha256": original_hash}
    assert job["config"]["powder"]["initial_relative_density"] == .72
    report = client.get(job["report_url"])
    assert report.status_code == 200
    assert "text/html" in report.headers["content-type"]
    assert "Plotly.newPlot" in report.text
    assert client.get(job["artifacts"]["predicted-powder.stl"]).content
    predicted_name = f"cavity+{job_id}.step"
    predicted_step = client.get(job["artifacts"][predicted_name]).content
    assert predicted_step.startswith(b"ISO-10303-21;")
    assert b"END-ISO-10303-21;" in predicted_step
    predicted_path = Path(solids["cavity"]).with_name(predicted_name)
    predicted_path.write_bytes(predicted_step)
    gmsh.initialize(readConfigFiles=False)
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        volumes = gmsh.model.occ.importShapes(str(predicted_path), highestDimOnly=True)
        assert len(volumes) == 1 and volumes[0][0] == 3
        assert gmsh.model.occ.getMass(*volumes[0]) > 0
    finally:
        gmsh.finalize()
    assert client.get(f"/api/jobs/{job_id}/artifacts/cavity+{'a' * 32}.step").status_code == 404
    resolved = client.get(job["artifacts"]["config.resolved.json"]).json()
    assert resolved["powder"]["initial_relative_density"] == .72
    assert client.get(f"/api/jobs/{job_id}/artifacts/input.step").status_code == 404
    assert client.get(f"/api/jobs/{job_id}/artifacts/..%2F..%2Fconfig.json").status_code == 404


def test_target_size_does_not_define_the_powder_domain(client, solids):
    shutil.copyfile(solids["gap"], solids["cavity"])
    submitted = client.post("/api/jobs", json={"config": config()})
    assert submitted.status_code == 202, submitted.text
    job = wait_job(client, submitted.json()["id"])
    assert job["status"] == "completed", job
    assert job["result"]["validation_status"] == "not_assessed"
    contact = client.get(job["artifacts"]["contact-assessment.json"]).json()
    assert contact == job["result"]["contact_assessment"]
    assert contact["reason_code"] == "bonded_interface_prevents_separation"
    comparison = client.get(job["artifacts"]["comparison.json"]).json()
    assert comparison["regions"]["planar"]["min_signed_mm"] > 0
    assert comparison["status"] == "measured"


@pytest.mark.parametrize("target_half,powder_half,metric", [
    (6, 5, "min_signed_mm"), (1, 20, "max_signed_mm"),
])
def test_target_diff_does_not_issue_inner_wall_gap_verdict_or_regeneration_offsets(
        client, solids, target_half, powder_half, metric):
    for role in ("cavity", "capsule"):
        gmsh.initialize(readConfigFiles=False)
        try:
            gmsh.option.setNumber("General.Terminal", 0)
            gmsh.model.add(role)
            if role == "cavity":
                gmsh.model.occ.addBox(*([-target_half] * 3), *([2 * target_half] * 3))
            else:
                outer = gmsh.model.occ.addBox(*([-powder_half - 1] * 3), *([2 * powder_half + 2] * 3))
                inner = gmsh.model.occ.addBox(*([-powder_half] * 3), *([2 * powder_half] * 3))
                gmsh.model.occ.cut([(3, outer)], [(3, inner)])
            gmsh.model.occ.synchronize()
            gmsh.write(solids[role])
        finally:
            gmsh.finalize()
    process = config()
    process["solver"]["mesh_size_mm"] = powder_half
    submitted = client.post("/api/jobs", json={"config": process})
    assert submitted.status_code == 202, submitted.text
    job = wait_job(client, submitted.json()["id"])
    assert job["status"] == "completed", job
    assert job["result"]["validation_status"] == "not_assessed"
    assert job["error"] is None
    comparison = client.get(job["artifacts"]["comparison.json"]).json()
    deviation = comparison["regions"]["planar"][metric]
    assert deviation < 0 if metric == "min_signed_mm" else deviation > 10
    assert comparison["status"] == "measured"
    assert "tolerance_mm" not in comparison["regions"]["planar"]
    assert job["result"]["upstream_regeneration_advice"] == []
    advice = client.get(job["artifacts"]["upstream-advice.json"]).json()
    assert advice["job_id"] == job["id"]
    assert advice["status"] == "not_assessed"
    assert advice["recommendations"] == []
    assert advice["blocked_reason"]
    assert client.get(job["report_url"]).status_code == 200
    assert client.get(job["artifacts"][f"cavity+{job['id']}.step"]).content.startswith(b"ISO-10303-21;")


def test_upload_endpoint_is_removed(client):
    assert client.post("/api/inputs", files={"cavity": ("cavity.step", b"STEP")}).status_code == 404


def test_invalid_capsule_geometry_has_failure_report(client, solids):
    shutil.copyfile(solids["cavity"], solids["capsule"])
    submitted = client.post("/api/jobs", json={"config": config()})
    assert submitted.status_code == 202, submitted.text
    job = wait_job(client, submitted.json()["id"])
    assert job["status"] == "failed", job
    assert job["error"]["message"]
    assert client.get(job["report_url"]).status_code == 200
    assert "predicted-powder.stl" not in job["artifacts"]
    assert not any(name.endswith(".step") for name in job["artifacts"])


@pytest.mark.parametrize("field,value", [("cavity_path", "/tmp/cavity.step"),
                                          ("capsule_path", "/tmp/capsule.step"),
                                          ("input_id", "a" * 32), ("workflow", "legacy")])
def test_old_input_options_are_rejected(client, solids, field, value):
    assert client.post("/api/jobs", json={"config": config(), field: value}).status_code == 422
    assert client.get("/api/jobs").json() == []


@pytest.mark.parametrize("content", [b"", b"not STEP"])
def test_invalid_step_is_rejected_without_a_partial_job(client, solids, content):
    Path(solids["capsule"]).write_bytes(content)
    response = client.post("/api/jobs", json={"config": config()})
    assert response.status_code == 422
    assert client.get("/api/jobs").json() == []


@pytest.mark.parametrize("role", ["cavity", "capsule"])
def test_missing_current_directory_step_is_identified(client, solids, role):
    Path(solids[role]).unlink()
    response = client.post("/api/jobs", json={"config": config()})
    assert response.status_code == 422
    assert f"{role}.step" in response.json()["detail"]
    assert client.get("/api/jobs").json() == []


def test_interrupted_jobs_become_failed_after_restart(tmp_path):
    from hipform.api import create_app
    root = tmp_path / "api"
    job_id = "f" * 32
    folder = root / "jobs" / job_id
    folder.mkdir(parents=True)
    (folder / "job.json").write_text(json.dumps({"id": job_id, "status": "running", "config": config(),
                                               "created_at": "2026-09-08T00:00:00Z"}))
    with TestClient(create_app(root)) as client:
        job = client.get(f"/api/jobs/{job_id}").json()
        assert job["status"] == "failed"
        assert job["error"]["code"] == "interrupted"
        assert client.get(job["report_url"]).status_code == 200


def test_two_servers_cannot_share_a_job_store(tmp_path):
    from hipform.jobs import JobStore
    first, second = JobStore(tmp_path / "api"), JobStore(tmp_path / "api")
    first.start()
    try:
        with pytest.raises(RuntimeError, match="already in use"):
            second.start()
    finally:
        first.close()
    second.start()
    second.close()


def test_solver_timeout_is_reported_and_process_reaped(tmp_path, solids):
    from hipform.api import create_app
    app = create_app(tmp_path / "api", source_dir=tmp_path, job_timeout_s=.001)
    with TestClient(app) as client:
        submitted = client.post("/api/jobs", json={"config": config()})
        job = wait_job(client, submitted.json()["id"])
        assert job["status"] == "failed"
        assert job["error"]["code"] == "timeout"
        assert client.get(job["report_url"]).status_code == 200
    assert not app.state.store.processes


def test_empty_configuration_uses_defaults_for_current_pair(tmp_path, solids):
    from hipform.api import create_app
    with TestClient(create_app(tmp_path / "defaults-api", source_dir=tmp_path, job_timeout_s=.001)) as client:
        response = client.post("/api/jobs", json={"config": {}})
        assert response.status_code == 202, response.text
        assert response.json()["config"] == SimulationConfig().model_dump(mode="json")
        assert wait_job(client, response.json()["id"])["error"]["code"] == "timeout"


def test_cross_host_browser_requests_are_rejected(client):
    assert client.get("/api/configs/default", headers={"host": "untrusted.example"}).status_code == 400


@pytest.mark.parametrize("origin", ["https://untrusted.example", "null"])
def test_cross_origin_jobs_are_rejected_before_persistence(client, solids, origin):
    response = client.post("/api/jobs", headers={"origin": origin}, json={"config": config()})
    assert response.status_code == 403
    assert client.get("/api/jobs").json() == []


def test_same_origin_swagger_can_save_configuration(client):
    response = client.post("/api/configs", headers={"origin": "http://testserver"},
                           json={"name": "Swagger", "config": config()})
    assert response.status_code == 201


@pytest.mark.parametrize("solver", [{"max_steps": 1}, {"time_step_s": 5e-324}])
def test_impossible_time_step_budget_rejected(client, solver):
    bad = SimulationConfig().model_dump(mode="json")
    bad["solver"].update(solver)
    assert client.post("/api/configs", json={"name": "bad", "config": bad}).status_code == 422


@pytest.mark.parametrize("endpoint,body", [
    ("/api/configs", '{"name":"bad","config":{"powder":{"initial_relative_density":1e999}}}'),
    ("/api/jobs", '{"config":{"powder":{"initial_relative_density":1e999}}}'),
])
def test_nonfinite_json_returns_validation_error(client, endpoint, body):
    response = client.post(endpoint, content=body, headers={"content-type": "application/json"})
    assert response.status_code == 422
    assert response.json()["detail"]


def test_local_synthetic_provenance_is_preserved(client, solids):
    capsule = Path(solids["capsule"])
    capsule.with_suffix(".provenance.json").write_text(json.dumps({
        "synthetic_example": True, "capsule": {"sha256": hashlib.sha256(capsule.read_bytes()).hexdigest()}}))
    submitted = client.post("/api/jobs", json={"config": config()})
    assert submitted.status_code == 202
    job = wait_job(client, submitted.json()["id"])
    assert job["status"] == "completed", job
    assert job["result"]["geometry"]["synthetic_example"] is True


def test_bad_local_provenance_rejected_before_queue(client, solids):
    Path(solids["capsule"]).with_suffix(".provenance.json").write_text('{"capsule": []}')
    submitted = client.post("/api/jobs", json={"config": config()})
    assert submitted.status_code == 422
    assert client.get("/api/jobs").json() == []


def test_incomplete_failure_output_still_gets_a_report(client, solids, monkeypatch):
    original = subprocess.Popen

    def broken_worker(command, **kwargs):
        output = command[command.index("--output") + 1]
        script = (f"from pathlib import Path; p=Path({output!r}); p.mkdir(); "
                  "(p/'result.json').write_text('{\"execution_status\":\"failed\",\"error\":\"stopped early\"}'); "
                  "raise SystemExit(2)")
        return original([sys.executable, "-c", script], **kwargs)

    monkeypatch.setattr(subprocess, "Popen", broken_worker)
    submitted = client.post("/api/jobs", json={"config": config()})
    job = wait_job(client, submitted.json()["id"])
    assert job["status"] == "failed"
    assert job["error"]["code"] == "worker_failed"
    assert client.get(job["report_url"]).status_code == 200
