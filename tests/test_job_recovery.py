"""Crash recovery must stop the old solver and preserve the failure evidence."""

import json
import os
import selectors
import shutil
import signal
import subprocess
import sys
import textwrap
import time

from fastapi.testclient import TestClient
import gmsh
import pytest

from hipform.api import create_app


PARENT = textwrap.dedent("""\
    from pathlib import Path
    import json, os, signal, sys, time
    from hipform.api import JobRequest
    from hipform.config import SimulationConfig
    from hipform.jobs import JobStore

    root, cavity, capsule = map(Path, sys.argv[1:])
    store = JobStore(root)
    store.start()
    config = SimulationConfig.model_validate({
        "solver": {"mesh_size_mm": 4, "time_step_s": 1, "max_steps": 10000},
        "cycle": [{"time_s": 0, "temperature_c": 20, "pressure_mpa": 0},
                  {"time_s": 9000, "temperature_c": 20, "pressure_mpa": 0}],
    })
    job = store.submit(JobRequest(cavity_path=cavity, capsule_path=capsule, config=config))
    folder = root / "jobs" / job["id"]
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        with store.lock:
            process = store.processes.get(job["id"])
        log = folder / "run.log"
        if process and log.exists() and "Preparing conformal" in log.read_text():
            os.kill(process.pid, signal.SIGSTOP)
            print(json.dumps({"job_id": job["id"], "worker_pid": process.pid}), flush=True)
            break
        time.sleep(.005)
    else:
        raise RuntimeError("Solver did not enter its run before the deadline")
    while True:
        time.sleep(.1)
""")


def _process_running(pid):
    result = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)],
                            capture_output=True, text=True, timeout=1)
    state = result.stdout.strip()
    return bool(state) and not state.startswith("Z")


@pytest.mark.skipif(os.name != "posix" or not shutil.which("ps"), reason="POSIX crash lifecycle test")
def test_killed_service_stops_solver_and_keeps_recovered_failure_report(tmp_path):
    cavity, capsule = tmp_path / "cavity.step", tmp_path / "capsule.step"
    gmsh.initialize(readConfigFiles=False)
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.model.add("cavity")
        gmsh.model.occ.addBox(-5, -5, -5, 10, 10, 10)
        gmsh.model.occ.synchronize()
        gmsh.write(str(cavity))
        gmsh.model.add("capsule")
        outer = gmsh.model.occ.addBox(-6, -6, -6, 12, 12, 12)
        inner = gmsh.model.occ.addBox(-5, -5, -5, 10, 10, 10)
        gmsh.model.occ.cut([(3, outer)], [(3, inner)])
        gmsh.model.occ.synchronize()
        gmsh.write(str(capsule))
    finally:
        gmsh.finalize()

    root = tmp_path / "api"
    parent = subprocess.Popen([sys.executable, "-u", "-c", PARENT, str(root), str(cavity), str(capsule)],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    worker_pid = None
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(parent.stdout, selectors.EVENT_READ)
            assert selector.select(timeout=7), "Parent did not report a running solver"
        line = parent.stdout.readline()
        assert line, f"Parent exited before its checkpoint: {parent.stderr.read()}"
        checkpoint = json.loads(line)
        worker_pid = checkpoint["worker_pid"]
        assert _process_running(worker_pid)
        parent.kill()
        parent.wait(timeout=2)

        with TestClient(create_app(root)) as client:
            status_url = f'/api/jobs/{checkpoint["job_id"]}'
            job = client.get(status_url).json()
            assert job["status"] == "failed"
            assert job["error"]["code"] == "interrupted"
            failure_report = client.get(job["report_url"]).text
            assert "Validation Failed" in failure_report

            # Resume after recovery so an orphan has a chance to corrupt the report.
            os.kill(worker_pid, signal.SIGCONT)
            deadline = time.monotonic() + 3
            while _process_running(worker_pid) and time.monotonic() < deadline:
                time.sleep(.02)
            assert not _process_running(worker_pid), "Solver survived its service parent"

            recovered = client.get(status_url).json()
            result = client.get(recovered["artifacts"]["result.json"]).json()
            assert recovered["status"] == result["execution_status"] == "failed"
            assert result["error_code"] == "interrupted"
            assert client.get(recovered["report_url"]).text == failure_report
            assert "predicted-powder.stl" not in recovered["artifacts"]
            assert client.get(f"{status_url}/artifacts/predicted-powder.stl").status_code == 404
    finally:
        if parent.poll() is None:
            parent.kill()
            parent.wait(timeout=2)
        parent.stdout.close()
        parent.stderr.close()
        if worker_pid is not None:
            try:
                os.kill(worker_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
