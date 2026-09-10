"""Submit the current STEP pair to ANSYS and save traceable service responses."""

import argparse
import json
import math
from pathlib import Path
import sys
import time

import httpx

from hipform.ansys_client import AnsysClient, AnsysServiceError
from hipform.inputs import file_info, validate_step


def _write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _collect(service, job_id, output):
    state = service.get_job(job_id)
    _write_json(output / "status.json", state)
    summary = {
        "job_id": job_id,
        "status": state["status"],
        "fidelity": state.get("fidelity"),
        "error": state.get("error"),
        "engineering_assessment": {"status": "not_assessed",
            "reason": "Service execution success is not an engineering acceptance verdict."},
        "contact_assessment": {"status": "not_assessed",
            "reason": "No deformed powder/capsule contact gap field is provided."},
        "target_comparison": {"status": "not_assessed",
            "reason": "This method does not provide a formed powder STEP for target comparison."},
    }
    _write_json(output / "summary.json", summary)
    if state["status"] == "succeeded":
        result = service.get_result(job_id)
        _write_json(output / "result.json", result)
        summary["result_fidelity"] = result.get("fidelity")
        _write_json(output / "summary.json", summary)
    if state["status"] in {"succeeded", "failed", "cancelled"}:
        (output / "job.log").write_text(service.get_log(job_id), encoding="utf-8")
        names = service.list_artifacts(job_id)
        _write_json(output / "artifacts.json", names)
        artifacts = output / "artifacts"
        artifacts.mkdir()
        for name in names:
            service.download_artifact(job_id, name, artifacts / name)
    return state


def _execute(args, client):
    paths = {} if args.job_id else {
        f"{name}_step": validate_step(args.input_dir / f"{name}.step")
        for name in ("cavity", "capsule")}
    args.out.mkdir(parents=True, exist_ok=False)
    _write_json(args.out / "service.json", {"url": args.service_url, "job_id": args.job_id})
    with AnsysClient(args.service_url, client=client) as service:
        job_id = args.job_id
        if not job_id:
            _write_json(args.out / "inputs.json", {name: file_info(path) for name, path in paths.items()})
            geometry = {name: service.upload_step(path) for name, path in paths.items()}
            _write_json(args.out / "uploads.json", geometry)
            params = {"geometry": geometry}
            if args.mesh_size is not None:
                params["mesh"] = {"mesh_size_mm": args.mesh_size}
            _write_json(args.out / "request.json", {"params": params})
            accepted = service.submit("full3d-hip", params)
            _write_json(args.out / "accepted.json", accepted)
            job_id = accepted["id"]
        print(f"ANSYS job {job_id}; resume with --job-id {job_id} and a new --out directory", flush=True)
        deadline = time.monotonic() + args.timeout
        state = _collect(service, job_id, args.out)
        while args.wait and state["status"] in {"pending", "running"}:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                print(f"Wait timed out; ANSYS job {job_id} continues remotely. Resume using --job-id.",
                      file=sys.stderr)
                return 3
            time.sleep(min(args.poll_interval, remaining, 60))
            state = _collect(service, job_id, args.out)
    print(f"ANSYS job {job_id}: {state['status']}, fidelity={state.get('fidelity')}; "
          f"saved to {args.out.resolve()}")
    if state["status"] in {"failed", "cancelled"}:
        print(f"ANSYS job {job_id} {state['status']}: {state.get('error')}", file=sys.stderr)
        return 1
    return 0


def main(argv=None, *, client: httpx.Client | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=Path.cwd())
    parser.add_argument("--out", type=Path, required=True, help="New output directory")
    parser.add_argument("--service-url", default="http://dev.htcmc.site")
    parser.add_argument("--mesh-size", type=float, help="Override the service mesh size in mm")
    parser.add_argument("--job-id", help="Collect an existing job without uploading or submitting")
    parser.add_argument("--wait", action="store_true", help="Poll until the job finishes")
    parser.add_argument("--poll-interval", type=float, default=5, help="Polling interval in seconds")
    parser.add_argument("--timeout", type=float, default=14400, help="Maximum wait in seconds (default: 4 h)")
    args = parser.parse_args(argv)
    try:
        for name in ("mesh_size", "poll_interval", "timeout"):
            value = getattr(args, name)
            if value is not None and (not math.isfinite(value) or value <= 0):
                raise ValueError(f"--{name.replace('_', '-')} must be a finite positive number")
        return _execute(args, client)
    except (AnsysServiceError, OSError, ValueError) as exc:
        print(f"ANSYS error: {exc}. Saved files: {args.out.resolve()}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
