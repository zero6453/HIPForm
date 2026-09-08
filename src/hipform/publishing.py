"""Export a completed numerical run as a self-contained GitHub Pages site."""

import hashlib
import json
from pathlib import Path, PureWindowsPath
import shutil
import tempfile

import numpy as np

from .report import write_report
from .types import SimulationMesh, SimulationResult


DOWNLOADS = {
    "predicted-powder.stl": "预测粉末表面 STL",
    "reference-powder.stl": "参考表面 STL",
    "predicted-assembly.vtu": "预测体网格 VTU",
    "history.csv": "计算历程 CSV",
    "config.resolved.json": "本次参数 JSON",
    "comparison.json": "公差结果 JSON",
    "result.json": "运行结果 JSON",
    "mesh-info.json": "几何与网格 JSON",
    "solver.json": "求解记录 JSON",
}


def _public_metadata(value):
    if isinstance(value, dict):
        return {key: PureWindowsPath(item).name if key == "path" and isinstance(item, str)
                else _public_metadata(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_public_metadata(item) for item in value]
    return value


def _read_json(path):
    return _public_metadata(json.loads(path.read_text(encoding="utf-8")))


def publish_run(run_dir: Path, output_dir: Path) -> Path:
    run_dir, output_dir = Path(run_dir).resolve(), Path(output_dir)
    if output_dir.is_symlink() or output_dir.exists():
        raise ValueError(f"Publication output already exists: {output_dir}; choose a new directory")
    required = [*DOWNLOADS, "mesh.npz", "solution.npz"]
    for name in required:
        artifact = run_dir / name
        if artifact.is_symlink() or not artifact.is_file():
            raise ValueError(f"Missing or symlinked run artifact: {name}")
    summary = _read_json(run_dir / "result.json")
    if summary.get("execution_status") != "completed":
        raise ValueError("Only completed simulation runs can be published")
    geometry = _read_json(run_dir / "mesh-info.json")
    solver = _read_json(run_dir / "solver.json")
    comparison = _read_json(run_dir / "comparison.json")
    config = _read_json(run_dir / "config.resolved.json")
    with np.load(run_dir / "mesh.npz", allow_pickle=False) as data:
        mesh = SimulationMesh(**{name: data[name] for name in (
            "points", "tetrahedra", "material", "pressure_triangles", "powder_triangles", "powder_face_ids")},
            metadata=geometry)
    with np.load(run_dir / "solution.npz", allow_pickle=False) as data:
        result = SimulationResult(data["displacement_mm"], data["relative_density"],
                                  solver["history"], solver["warnings"], solver["metadata"])
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    # Build atomically so failed exports cannot replace a previously published report.
    with tempfile.TemporaryDirectory(prefix=".hipform-", dir=output_dir.parent) as temporary:
        staging = Path(temporary)
        for name in DOWNLOADS:
            source = run_dir / name
            if source.suffix == ".json":
                data = _read_json(source)
                if name == "result.json":
                    data.setdefault("artifacts", {})["report"] = "index.html"
                (staging / name).write_text(json.dumps(data, ensure_ascii=False, indent=2,
                                                     allow_nan=False) + "\n", encoding="utf-8")
            else:
                shutil.copyfile(source, staging / name)
        write_report(staging / "index.html", mesh, result, comparison, config, downloads=DOWNLOADS)
        (staging / ".nojekyll").touch()
        manifest = {"format": "hipform-pages-v1", "files": {
            name: hashlib.sha256((staging / name).read_bytes()).hexdigest()
            for name in ["index.html", ".nojekyll", *DOWNLOADS]}}
        (staging / "publication.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        staging.rename(output_dir)
    return output_dir / "index.html"
