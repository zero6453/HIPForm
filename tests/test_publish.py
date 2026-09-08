import json

import numpy as np
import pytest

from hipform.cli import main
from hipform.comparison import compare_surfaces


@pytest.fixture
def completed_run(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    points = np.array([[0., 0., 0.], [4., 0., 0.], [0., 4., 0.]])
    triangles = np.array([[0, 1, 2]])
    np.savez(run / "mesh.npz", points=points, tetrahedra=np.empty((0, 4), dtype=int),
             material=np.empty(0, dtype=int), pressure_triangles=triangles,
             powder_triangles=triangles, powder_face_ids=np.array([7]))
    np.savez(run / "solution.npz", displacement_mm=np.zeros_like(points), relative_density=np.empty(0))
    geometry = {"synthetic_example": True, "inputs": {
        "cavity": {"path": "/Users/private/secret/cavity.step", "sha256": "abc"}}}
    files = {
        "result.json": {"execution_status": "completed", "engineering_acceptance": "not_assessed", "geometry": geometry},
        "mesh-info.json": geometry,
        "solver.json": {"metadata": {"model_validity": "outside_small_strain_or_density_range",
                                     "final_state": "capsule_attached"}, "warnings": [],
                        "history": [{"time_s": 0, "temperature_c": 20, "pressure_mpa": 0,
                                     "mean_relative_density": .65, "prescribed_relative_density": .65}]},
        "config.resolved.json": {},
        "comparison.json": compare_surfaces(points, points, triangles, [7]),
    }
    for name, data in files.items():
        (run / name).write_text(json.dumps(data))
    for name in ("predicted-powder.stl", "reference-powder.stl", "predicted-assembly.vtu", "history.csv"):
        (run / name).write_text("fixture")
    (run / "private.env").write_text("secret")
    (run / "report.html").write_text("untrusted old report")
    return run


def test_publish_rebuilds_report_and_exports_only_public_files(completed_run, tmp_path):
    output = tmp_path / "site"
    assert main(["publish", "--run", str(completed_run), "--output", str(output)]) == 0
    document = (output / "index.html").read_text()
    assert "HIPForm" in document
    assert "Plotly.newPlot" in document
    assert "超出小应变或密度适用范围" in document
    assert "工程验收：未评估" in document
    assert "untrusted old report" not in document
    assert "/Users/private/secret" not in document
    assert 'href="predicted-powder.stl"' in document
    assert (output / ".nojekyll").is_file()
    assert not (output / "private.env").exists()
    assert not (output / "mesh.npz").exists()
    metadata = json.loads((output / "mesh-info.json").read_text())
    assert metadata["inputs"]["cavity"]["path"] == "cavity.step"
    assert metadata["inputs"]["cavity"]["sha256"] == "abc"
    assert "/Users/private" in (completed_run / "mesh-info.json").read_text()


@pytest.mark.parametrize("status", ["failed", "mesh_prepared"])
def test_publish_rejects_incomplete_run(completed_run, tmp_path, status):
    (completed_run / "result.json").write_text(json.dumps({"execution_status": status}))
    output = tmp_path / "site"
    assert main(["publish", "--run", str(completed_run), "--output", str(output)]) == 2
    assert not output.exists()


def test_publish_preserves_existing_site(completed_run, tmp_path):
    output = tmp_path / "site"
    output.mkdir()
    (output / "index.html").write_text("existing")
    assert main(["publish", "--run", str(completed_run), "--output", str(output)]) == 2
    assert (output / "index.html").read_text() == "existing"


def test_publish_rejects_symlinked_artifact(completed_run, tmp_path):
    target = tmp_path / "private.stl"
    target.write_text("secret")
    artifact = completed_run / "predicted-powder.stl"
    artifact.unlink()
    artifact.symlink_to(target)
    assert main(["publish", "--run", str(completed_run), "--output", str(tmp_path / "site")]) == 2
