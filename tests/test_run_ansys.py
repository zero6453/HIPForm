"""ANSYS command behavior through the documented HTTP interface."""

import json
from pathlib import Path
import runpy

import httpx
import pytest


def run_main(args, client):
    script = Path(__file__).resolve().parents[1] / "scripts" / "run_ansys.py"
    return runpy.run_path(str(script))["main"](args, client=client)


@pytest.mark.parametrize("mesh_size", [None, 25])
def test_submit_current_pair_preserves_request_and_job_for_later_collection(tmp_path, mesh_size):
    for name in ("cavity.step", "capsule.step"):
        (tmp_path / name).write_text("ISO-10303-21;\nEND-ISO-10303-21;")
    output = tmp_path / "run"

    def service(request):
        if request.url.path == "/uploads":
            name = "cavity.step" if b'filename="cavity.step"' in request.content else "capsule.step"
            return httpx.Response(200, json={"path": f"C:/uploads/{name}", "size_bytes": 36})
        if request.method == "POST":
            assert request.url.path == "/sim/full3d-hip"
            params = {"geometry": {
                "cavity_step": "C:/uploads/cavity.step",
                "capsule_step": "C:/uploads/capsule.step",
            }}
            if mesh_size is not None:
                params["mesh"] = {"mesh_size_mm": 25}
            assert json.loads(request.content) == {"params": params}
            return httpx.Response(202, json={"id": "job-1", "method": "full3d-hip",
                "fidelity": "smoke", "status_url": "/jobs/job-1"})
        assert request.url.path == "/jobs/job-1"
        assert json.loads((output / "accepted.json").read_text())["id"] == "job-1"
        assert len(json.loads((output / "inputs.json").read_text())["cavity_step"]["sha256"]) == 64
        return httpx.Response(200, json={"id": "job-1", "status": "running", "fidelity": "smoke"})

    with httpx.Client(transport=httpx.MockTransport(service)) as client:
        args = ["--input-dir", str(tmp_path), "--out", str(output)]
        if mesh_size is not None:
            args += ["--mesh-size", str(mesh_size)]
        assert run_main(args, client) == 0
    summary = json.loads((output / "summary.json").read_text())
    assert summary["job_id"] == "job-1"
    assert summary["status"] == "running"
    assert summary["fidelity"] == "smoke"
    assert summary["engineering_assessment"]["status"] == "not_assessed"


def test_resume_collects_smoke_outputs_without_submitting_or_claiming_engineering_pass(tmp_path):
    output = tmp_path / "collection"

    def service(request):
        assert request.method == "GET"
        payloads = {
            "/jobs/job-1": {"id": "job-1", "status": "succeeded", "fidelity": "smoke"},
            "/jobs/job-1/result": {"fidelity": "smoke", "deformed_stl": None,
                                   "displacement_max_mm": 1.25},
            "/jobs/job-1/artifacts": ["resolved-params.json", "model.cdb"],
        }
        if request.url.path.endswith("/log"):
            return httpx.Response(200, text="MAPDL completed\n")
        if request.url.path.endswith("/model.cdb"):
            return httpx.Response(200, content=b"/PREP7\n")
        if request.url.path.endswith("/resolved-params.json"):
            return httpx.Response(200, json={"mesh": {"mesh_size_mm": 6.0}})
        return httpx.Response(200, json=payloads[request.url.path])

    with httpx.Client(transport=httpx.MockTransport(service)) as client:
        assert run_main(["--job-id", "job-1", "--out", str(output)], client) == 0
    assert (output / "artifacts" / "model.cdb").read_bytes() == b"/PREP7\n"
    assert (output / "job.log").read_text() == "MAPDL completed\n"
    assert json.loads((output / "result.json").read_text())["displacement_max_mm"] == 1.25
    summary = json.loads((output / "summary.json").read_text())
    assert summary["fidelity"] == "smoke"
    assert summary["engineering_assessment"]["status"] == "not_assessed"
    assert summary["contact_assessment"]["status"] == "not_assessed"
    assert summary["target_comparison"]["status"] == "not_assessed"


def test_failed_job_preserves_failure_and_solver_log_with_nonzero_exit(tmp_path, capsys):
    output = tmp_path / "failed"
    failure = {"code": "MAPDL_NOT_FOUND", "message": "MAPDL executable unavailable"}

    def service(request):
        assert request.method == "GET"
        if request.url.path.endswith("/log"):
            return httpx.Response(200, text="Cannot start MAPDL")
        if request.url.path.endswith("/artifacts"):
            return httpx.Response(200, json=[])
        assert request.url.path == "/jobs/job-1"
        return httpx.Response(200, json={"id": "job-1", "status": "failed", "error": failure})

    with httpx.Client(transport=httpx.MockTransport(service)) as client:
        assert run_main(["--job-id", "job-1", "--out", str(output)], client) == 1
    assert json.loads((output / "summary.json").read_text())["error"] == failure
    assert (output / "job.log").read_text() == "Cannot start MAPDL"
    assert "MAPDL_NOT_FOUND" in capsys.readouterr().err


def test_connection_failure_after_submission_keeps_job_id_without_retry(tmp_path, capsys):
    for name in ("cavity.step", "capsule.step"):
        (tmp_path / name).write_text("ISO-10303-21;\nEND-ISO-10303-21;")
    output = tmp_path / "interrupted"
    submissions = []

    def service(request):
        if request.url.path == "/uploads":
            return httpx.Response(200, json={"path": "C:/uploads/input.step", "size_bytes": 36})
        if request.method == "POST":
            submissions.append(request)
            return httpx.Response(202, json={"id": "job-1", "method": "full3d-hip",
                "fidelity": "smoke", "status_url": "/jobs/job-1"})
        return httpx.Response(503, text="Sakura Frp Service Unavailable")

    with httpx.Client(transport=httpx.MockTransport(service)) as client:
        assert run_main(["--input-dir", str(tmp_path), "--out", str(output)], client) == 2
    assert len(submissions) == 1
    assert json.loads((output / "accepted.json").read_text())["id"] == "job-1"
    captured = capsys.readouterr()
    assert "job-1" in captured.out
    assert "503" in captured.err


def test_wait_collects_only_after_remote_job_finishes(tmp_path):
    statuses = iter(["running", "succeeded"])

    def service(request):
        assert request.method == "GET"
        path = request.url.path
        if path.endswith("/log"):
            return httpx.Response(200, text="Complete")
        if path.endswith("/artifacts"):
            return httpx.Response(200, json=[])
        if path.endswith("/result"):
            return httpx.Response(200, json={"fidelity": "smoke"})
        return httpx.Response(200, json={"id": "job-1", "status": next(statuses), "fidelity": "smoke"})

    output = tmp_path / "waited"
    with httpx.Client(transport=httpx.MockTransport(service)) as client:
        assert run_main(["--job-id", "job-1", "--out", str(output),
                         "--wait", "--poll-interval", "0.001"], client) == 0
    assert json.loads((output / "status.json").read_text())["status"] == "succeeded"
    assert (output / "result.json").exists()


def test_wait_timeout_preserves_running_job_for_resume(tmp_path, capsys):
    def service(request):
        assert request.method == "GET"
        assert request.url.path == "/jobs/job-1"
        return httpx.Response(200, json={"id": "job-1", "status": "running", "fidelity": "smoke"})

    output = tmp_path / "timed-out"
    with httpx.Client(transport=httpx.MockTransport(service)) as client:
        assert run_main(["--job-id", "job-1", "--out", str(output), "--wait",
                         "--timeout", "0.001", "--poll-interval", "0.001"], client) == 3
    assert json.loads((output / "status.json").read_text())["status"] == "running"
    assert "job-1" in capsys.readouterr().err


@pytest.mark.parametrize("option,value", [
    ("--mesh-size", "nan"), ("--poll-interval", "0"), ("--timeout", "-1"),
])
def test_invalid_numeric_controls_are_rejected_before_network_requests(tmp_path, option, value):
    def service(request):
        pytest.fail("Invalid controls must not reach ANSYS")

    with httpx.Client(transport=httpx.MockTransport(service)) as client:
        assert run_main(["--job-id", "job-1", "--out", str(tmp_path / "invalid"),
                         option, value], client) == 2
