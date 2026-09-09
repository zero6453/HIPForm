"""Local durable job/config storage and serialized, isolated CLI execution."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import uuid

from filelock import FileLock, Timeout

from .config import SimulationConfig
from .failure_report import write_failure
from .inputs import copy_step, load_provenance, validate_step


ARTIFACTS = frozenset({"report.html", "result.json", "config.resolved.json", "mesh-info.json",
                      "comparison.json", "solver.json", "history.csv", "upstream-advice.json", "predicted-powder.stl",
                      "reference-powder.stl", "predicted-assembly.vtu", "initial-mesh.vtu",
                      "mesh.npz", "solution.npz", "run.log"})


def now():
    return datetime.now(timezone.utc).isoformat()


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, data):
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                             encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def checked_id(value):
    if not re.fullmatch(r"[a-f0-9]{32}", value):
        raise KeyError(value)
    return value


class JobStore:
    def __init__(self, root: Path, *, timeout_s=3600, source_dir: Path | None = None):
        self.root = Path(root).resolve()
        self.source_dir = Path(source_dir or Path.cwd()).resolve()
        self.timeout_s = timeout_s
        self.lock = threading.RLock()
        self.executor = None
        self.processes = {}
        self.closing = False
        for name in ("jobs", "configs"):
            (self.root / name).mkdir(parents=True, exist_ok=True)
        self.service_lock = FileLock(self.root / ".service.lock", thread_local=False)

    def start(self):
        with self.lock:
            try:
                self.service_lock.acquire(timeout=0)
            except Timeout:
                raise RuntimeError(f"API data directory is already in use: {self.root}") from None
            self.closing = False
            try:
                for path in (self.root / "jobs").glob("*/job.json"):
                    job = read_json(path)
                    if job["status"] in {"queued", "running"}:
                        self._fail(job, "interrupted", "Service stopped before this job completed.")
                self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="hipform")
            except BaseException:
                self.service_lock.release()
                raise

    def close(self):
        with self.lock:
            self.closing = True
            processes = list(self.processes.values())
        for process in processes:
            if process.poll() is None:
                process.terminate()
        for process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        if self.executor:
            self.executor.shutdown(wait=True, cancel_futures=True)
        with self.lock:
            for path in (self.root / "jobs").glob("*/job.json"):
                job = read_json(path)
                if job["status"] in {"queued", "running"}:
                    self._fail(job, "interrupted", "Service stopped before this job completed.")
        self.service_lock.release()

    def save_config(self, name, config, config_id=None):
        with self.lock:
            if config_id is not None:
                old = self.get_config(config_id)
            else:
                config_id, old = uuid.uuid4().hex, {}
            record = {"id": config_id, "name": name, "config": config.model_dump(mode="json"),
                      "created_at": old.get("created_at", now()), "updated_at": now(),
                      "calibrated": False}
            write_json(self.root / "configs" / f"{config_id}.json", record)
            return record

    def get_config(self, config_id):
        path = self.root / "configs" / f"{checked_id(config_id)}.json"
        if not path.is_file():
            raise KeyError(config_id)
        return read_json(path)

    def list_configs(self):
        return [read_json(path) for path in sorted((self.root / "configs").glob("*.json"))]

    def _snapshot_inputs(self, sources: dict[str, Path], folder: Path) -> dict:
        folder.mkdir()
        snapshots = {}
        for role, source in sources.items():
            target = folder / f"{role}.step"
            with source.open("rb") as stream:
                digest = copy_step(stream, target)
            snapshots[role] = {"source_path": str(source), "filename": source.name, "sha256": digest}
            if role == "capsule":
                provenance = load_provenance(source, digest)
                if provenance is not None:
                    write_json(target.with_suffix(".provenance.json"), provenance)
        return snapshots

    def submit(self, request):
        with self.lock:
            if self.closing or self.executor is None:
                raise RuntimeError("Simulation worker is unavailable")
            if request.config_id:
                config = SimulationConfig.model_validate(self.get_config(request.config_id)["config"])
            else:
                config = request.config
            sources = {"cavity": self.source_dir / "cavity.step",
                       "capsule": self.source_dir / "capsule.step"}
            missing = [path.name for path in sources.values() if not path.is_file()]
            if missing:
                raise ValueError(f"Required {', '.join(missing)} not found in the service source directory")
            sources = {role: validate_step(path) for role, path in sources.items()}
            job_id = uuid.uuid4().hex
            folder = self.root / "jobs" / job_id
            folder.mkdir()
            try:
                snapshots = self._snapshot_inputs(sources, folder / "inputs")
                resolved = config.model_dump(mode="json")
                write_json(folder / "config.json", resolved)
                job = {"id": job_id, "name": request.name, "status": "queued", "created_at": now(),
                       "updated_at": now(), "config_id": request.config_id, "config": resolved,
                       "inputs": snapshots, "workflow": "verify", "error": None}
                self._save(job)
                self.executor.submit(self._run, job_id)
            except Exception:
                shutil.rmtree(folder)
                raise
            return job

    def _save(self, job):
        job["updated_at"] = now()
        write_json(self.root / "jobs" / job["id"] / "job.json", job)

    def _fail(self, job, code, message):
        folder = self.root / "jobs" / job["id"] / "run"
        summary = write_failure(folder, error=message, error_code=code, stage="worker")
        job.update(status="failed", error={"code": code, "message": message, "details": {}},
                   result=summary, completed_at=now())
        self._save(job)

    def get_job(self, job_id):
        path = self.root / "jobs" / checked_id(job_id) / "job.json"
        if not path.is_file():
            raise KeyError(job_id)
        return read_json(path)

    def list_jobs(self):
        return sorted((read_json(path) for path in (self.root / "jobs").glob("*/job.json")),
                      key=lambda job: job["created_at"], reverse=True)

    def available_artifacts(self, job: dict) -> dict[str, Path]:
        """Apply the result-visibility policy to one consistent job snapshot."""
        allowed = ARTIFACTS if job["status"] == "completed" else {"config.resolved.json", "run.log"}
        if job["status"] == "failed":
            allowed = allowed | {"report.html", "result.json"}
        folder = self.root / "jobs" / checked_id(job["id"])
        artifacts = {}
        for name in sorted(allowed):
            path = folder / "run.log" if name == "run.log" else folder / "run" / name
            if not path.is_symlink() and path.is_file():
                artifacts[name] = path
        if job["status"] == "completed":
            path = folder / "run" / f"cavity+{job['id']}.step"
            if path.is_file() and not path.is_symlink():
                artifacts[path.name] = path
        return artifacts

    def artifact(self, job_id: str, name: str) -> Path:
        return self.available_artifacts(self.get_job(job_id))[name]

    def _run(self, job_id):
        folder = self.root / "jobs" / job_id
        job = self.get_job(job_id)
        with self.lock:
            if self.closing:
                self._fail(job, "interrupted", "Service stopped before this job started.")
                return
            job.update(status="running", started_at=now())
            self._save(job)
        command = [sys.executable, "-u", "-m", "hipform.worker", "verify",
                   "--cavity", str(folder / "inputs" / "cavity.step"),
                   "--capsule", str(folder / "inputs" / "capsule.step"),
                   "--config", str(folder / "config.json"), "--output", str(folder / "working-run"),
                   "--job-id", job_id]
        try:
            with (folder / "run.log").open("w", encoding="utf-8") as log:
                with self.lock:
                    if self.closing:
                        self._fail(job, "interrupted", "Service stopped before this job started.")
                        return
                    process = subprocess.Popen(command, cwd=folder, stdin=subprocess.PIPE,
                                               stdout=log, stderr=subprocess.STDOUT)
                    self.processes[job_id] = process
                try:
                    code = process.wait(timeout=self.timeout_s)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                    self._fail(job, "timeout", f"Simulation exceeded {self.timeout_s} seconds.")
                    return
                finally:
                    process.stdin.close()
            if self.closing:
                self._fail(job, "interrupted", "Service stopped before this job completed.")
                return
            result_path = folder / "working-run" / "result.json"
            if not result_path.is_file():
                self._fail(job, "worker_failed", f"Simulation process exited with code {code}; inspect run.log.")
                return
            result = read_json(result_path)
            if code == 0 and result.get("execution_status") == "completed" and (folder / "working-run" / "report.html").is_file():
                job.update(status="completed", error=None)
            elif result.get("execution_status") == "failed" and (folder / "working-run" / "report.html").is_file():
                job.update(status="failed", error={"code": result.get("error_code", "simulation_failed"),
                           "message": result["error"], "details": result.get("error_details", {})})
            else:
                self._fail(job, "worker_failed", f"Incomplete result from process with exit code {code}.")
                return
            (folder / "working-run").rename(folder / "run")
            job.update(result=result, returncode=code, completed_at=now())
            self._save(job)
        except Exception as exc:
            self._fail(job, "worker_failed", str(exc))
        finally:
            with self.lock:
                self.processes.pop(job_id, None)
