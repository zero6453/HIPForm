"""Finished-target workflow, tested with independent analytic CAD examples."""

import json
import re

import numpy as np
import pytest
import trimesh

from hipform.config import SimulationConfig


def test_material_boundary_preserves_capsule_inner_wall_and_outward_orientation():
    from hipform.geometry import material_boundary
    from hipform.types import SimulationMesh

    points = np.array([[0., 0., 0.], [1., 0., 0.], [0., 1., 0.],
                       [0., 0., 1.], [0., 0., -1.]])
    mesh = SimulationMesh(points, np.array([[0, 1, 2, 3], [0, 1, 2, 4]]),
                          np.array([0, 1]), np.empty((0, 3), dtype=int),
                          np.empty((0, 3), dtype=int), np.empty(0, dtype=int))

    triangles = material_boundary(mesh, 1)

    assert {tuple(sorted(face)) for face in triangles} == {
        (0, 1, 2), (0, 1, 4), (0, 2, 4), (1, 2, 4)}
    capsule = trimesh.Trimesh(vertices=points, faces=triangles, process=False)
    assert capsule.is_winding_consistent
    assert capsule.volume == pytest.approx(1 / 6)


def test_supplied_materials_and_three_hour_process_round_trip():
    config = SimulationConfig()
    assert config.capsule.yield_strength_mpa == 250
    assert config.capsule.tensile_strength_mpa == 400
    assert config.powder.yield_strength_mpa == 800
    assert config.powder.tensile_strength_mpa == 950
    assert config.powder.expected_final_relative_density == .97
    assert config.powder.typical_volume_shrinkage_fraction == .30
    hold = sum(b.time_s - a.time_s for a, b in zip(config.cycle, config.cycle[1:])
               if a.temperature_c == b.temperature_c == 900
               and a.pressure_mpa == b.pressure_mpa == 120)
    assert hold == 10800
    assert max(point.temperature_c for point in config.cycle) == 900
    assert max(point.pressure_mpa for point in config.cycle) == 120
    assert SimulationConfig.model_validate_json(config.model_dump_json()) == config


def test_target_report_shows_geometry_process_and_model_limits(tmp_path):
    from hipform.comparison import compare_target_surfaces
    from hipform.geometry import material_boundary
    from hipform.report import write_verification_report
    from hipform.types import SimulationMesh, SimulationResult

    points = np.array([[0., 0., 0.], [1., 0., 0.], [0., 1., 0.],
                       [0., 0., 1.], [0., 0., -1.]])
    mesh = SimulationMesh(points, np.array([[0, 1, 2, 3], [0, 1, 2, 4]]),
                          np.array([0, 1]), np.empty((0, 3), dtype=int),
                          np.empty((0, 3), dtype=int), np.empty(0, dtype=int))
    mesh.powder_triangles = material_boundary(mesh, 0)
    triangles = mesh.powder_triangles
    result = SimulationResult(-.1 * points, np.array([.65]),
        [{"time_s": 0, "temperature_c": 20, "pressure_mpa": 0, "mean_relative_density": .65}],
        ["Numerical model is uncalibrated"], {"model_validity": "uncalibrated"})
    comparison = compare_target_surfaces(points, triangles, points + result.displacement,
                                        triangles, ["Plane"] * len(triangles))
    contact = {
        "status": "not_assessed", "reason_code": "bonded_interface_prevents_separation",
        "basis": "formed_powder_to_capsule_inner_wall", "interface_model": "bonded_shared_nodes",
        "imposed_interface_gap_mm": 0.,
        "reason": "当前绑定界面强制共同位移，不能预测粉末与包套内壁的分离间隙。",
        "regions": {name: {"status": "not_assessed", "lower_limit_mm": 0., "upper_limit_mm": limit}
                    for name, limit in (("planar", 10.), ("nonplanar", 20.))},
    }
    output = tmp_path / "report.html"
    write_verification_report(output, comparison, SimulationConfig().model_dump(mode="json"),
        {"predicted_step": "cavity+test.step"}, mesh=mesh, result=result,
        target=(points, triangles, ["Plane"] * len(triangles)), contact_assessment=contact)
    html = output.read_text()
    assert "Plotly.newPlot" in html
    assert "cavity.step" in html
    assert "温度" in html and "压力" in html and "密度" in html
    assert "Numerical model is uncalibrated" in html
    assert "内壁间隙判定：未评估" in html
    assert "尺寸判定：失败" not in html and "采样通过" not in html
    assert "0.000 ～ 10.000" in html and "0.000 ～ 20.000" in html
    assert "当前绑定界面强制共同位移" in html
    assert "原型 DIFF" in html
    assert not re.search(r'<script[^>]+src=', html)

    plots = {}
    for plot_id in ("assembly-before", "assembly-after", "target-diff"):
        match = re.search(r'Plotly.newPlot\(\s*"' + plot_id + r'",\s*', html)
        assert match, plot_id
        plots[plot_id], _ = json.JSONDecoder().raw_decode(html[match.end():])
    for plot_id in ("assembly-before", "assembly-after"):
        assert {trace["name"] for trace in plots[plot_id]} == {"包套（20号钢）", "粉末（TC4）"}
        assert all(trace["showlegend"] for trace in plots[plot_id])
        assert all(len(trace["i"]) == 4 for trace in plots[plot_id])
    before, after = plots["assembly-before"], plots["assembly-after"]
    assert max(before[0]["x"]) == 1.
    assert max(after[0]["x"]) == .9
    assert max(before[1]["z"]) == 1.
    assert max(after[1]["z"]) == .9
    assert {trace["name"] for trace in plots["target-diff"]} == {"目标 cavity.step", "预测成型粉末"}


@pytest.mark.parametrize("change", [{"seal": {"center_mm": [1000, 1000, 1000], "radius_mm": 1, "height_mm": 1}},
                                    {"tolerances": {"angular_face_ids": [999999]}}])
def test_verify_rejects_legacy_options_instead_of_ignoring_them(tmp_path, change):
    from hipform.cli import main
    config = tmp_path / "config.json"
    config.write_text(json.dumps(change))
    output = tmp_path / "result"
    assert main(["verify", "--config", str(config), "--output", str(output)]) == 2
    assert json.loads((output / "result.json").read_text())["error_code"] == "invalid_configuration"
