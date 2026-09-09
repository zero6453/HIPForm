"""Finished-target workflow, tested with independent analytic CAD examples."""

import numpy as np
import json
import pytest
import trimesh

from hipform.config import SimulationConfig


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
    from hipform.report import write_verification_report
    from hipform.types import SimulationMesh, SimulationResult

    surface = trimesh.creation.box()
    mesh = SimulationMesh(surface.vertices, np.empty((0, 4)), np.empty(0),
                          surface.faces, surface.faces, np.ones(len(surface.faces)))
    result = SimulationResult(np.zeros_like(surface.vertices), np.array([.65]),
        [{"time_s": 0, "temperature_c": 20, "pressure_mpa": 0, "mean_relative_density": .65}],
        ["Numerical model is uncalibrated"], {"model_validity": "uncalibrated"})
    comparison = compare_target_surfaces(surface.vertices, surface.faces, surface.vertices,
                                        surface.faces, ["Plane"] * len(surface.faces))
    output = tmp_path / "report.html"
    write_verification_report(output, comparison, SimulationConfig().model_dump(mode="json"),
        {"predicted_step": "cavity+test.step"}, mesh=mesh, result=result,
        target=(surface.vertices, surface.faces, ["Plane"] * len(surface.faces)))
    html = output.read_text()
    assert "Plotly.newPlot" in html
    assert "cavity.step" in html
    assert "温度" in html and "压力" in html and "密度" in html
    assert "Numerical model is uncalibrated" in html


@pytest.mark.parametrize("change", [{"seal": {"center_mm": [1000, 1000, 1000], "radius_mm": 1, "height_mm": 1}},
                                    {"tolerances": {"angular_face_ids": [999999]}}])
def test_verify_rejects_legacy_options_instead_of_ignoring_them(tmp_path, change):
    from hipform.cli import main
    config = tmp_path / "config.json"
    config.write_text(json.dumps(change))
    output = tmp_path / "result"
    assert main(["verify", "--config", str(config), "--output", str(output)]) == 2
    assert json.loads((output / "result.json").read_text())["error_code"] == "invalid_configuration"
