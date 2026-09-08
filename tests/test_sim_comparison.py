"""Surface distances must preserve CAD coordinates and reference face regions."""

import json

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("trimesh")
pytest.importorskip("rtree")


def compare(*args, **kwargs):
    from hipform.comparison import compare_surfaces

    return compare_surfaces(*args, **kwargs)


@pytest.fixture
def plane():
    points = np.array([[0., 0., 0.], [4., 0., 0.], [4., 4., 0.], [0., 4., 0.]])
    return points, np.array([[0, 1, 2], [0, 2, 3]]), np.array([7, 7])


def test_identical_surface_has_zero_bidirectional_distances(plane):
    points, triangles, tags = plane
    result = compare(points, points.copy(), triangles, tags)
    assert result["status"] == "within_limits_on_sampled_surface"
    assert result["global"]["max_mm"] == pytest.approx(0, abs=1e-12)
    assert result["global"]["rms_mm"] == pytest.approx(0, abs=1e-12)
    assert result["directions"]["reference_to_predicted"]["max_mm"] == pytest.approx(0)
    assert result["directions"]["predicted_to_reference"]["max_mm"] == pytest.approx(0)
    assert result["engineering_acceptance"] == "not_assessed"
    assert result["alignment"] == "none"
    assert result["calibrated"] is False
    json.dumps(result, allow_nan=False)


def test_normal_plane_translation_is_triangle_distance_and_uses_10_20_limits(plane):
    points, triangles, tags = plane
    predicted = points + [0, 0, 11]
    strict = compare(points, predicted, triangles, tags)
    angular = compare(points, predicted, triangles, tags, angular_face_ids=[7])
    assert strict["status"] == "exceeds_limits"
    assert strict["global"]["max_mm"] == pytest.approx(11)
    assert strict["global"]["p95_mm"] == pytest.approx(11)
    assert strict["global"]["rms_mm"] == pytest.approx(11)
    assert strict["regions"]["flat"]["tolerance_mm"] == 10
    assert angular["regions"]["angular"]["tolerance_mm"] == 20
    assert angular["status"] == "within_limits_on_sampled_surface"
    assert angular["global"]["max_location"]["reference_face_id"] == 7
    assert angular["regions"]["flat"]["status"] == "no_samples"


def test_partial_coverage_requires_reference_to_predicted_direction(plane):
    points, triangles, tags = plane
    predicted = points.copy()
    predicted[:, 0] *= 0.5
    result = compare(points, predicted, triangles, tags, flat_mm=1)
    assert result["directions"]["reference_to_predicted"]["max_mm"] == pytest.approx(2)
    assert result["directions"]["predicted_to_reference"]["max_mm"] == pytest.approx(0)
    assert result["status"] == "exceeds_limits"


def test_only_explicit_reference_faces_receive_angular_tolerance():
    first = np.array([[0., 0., 0.], [2., 0., 0.], [0., 2., 0.]])
    points = np.vstack([first, first + [30., 0., 0.]])
    result = compare(points, points + [0., 0., 11.], [[0, 1, 2], [3, 4, 5]], [7, 8],
                     angular_face_ids=[7])
    assert result["regions"]["flat"]["face_ids"] == [8]
    assert result["regions"]["flat"]["status"] == "exceeds_limits"
    assert result["regions"]["angular"]["status"] == "within_limits_on_sampled_surface"
    assert result["status"] == "exceeds_limits"


def test_limit_equality_survives_roundoff(plane):
    points, triangles, tags = plane
    result = compare(points, points + [0, 0, 10 + 1e-12], triangles, tags)
    assert result["status"] == "within_limits_on_sampled_surface"


@pytest.mark.parametrize("unused_input", ["angular_tolerance", "vertex"])
def test_unused_inputs_do_not_relax_flat_tolerance(plane, unused_input):
    points, triangles, tags = plane
    settings = {}
    if unused_input == "angular_tolerance":
        settings["angular_mm"] = 1e15
    else:
        points = np.vstack((points, [1e15, 1e15, 1e15]))

    result = compare(points, points + [0, 0, 11], triangles, tags, flat_mm=10, **settings)

    assert result["global"]["max_mm"] == pytest.approx(11)
    assert result["regions"]["flat"]["status"] == "exceeds_limits"
    assert result["status"] == "exceeds_limits"


def test_reverse_samples_use_nearest_reference_tag_instead_of_source_tag():
    first = np.array([[0., 0., 0.], [2., 0., 0.], [0., 2., 0.]])
    points = np.vstack([first, first + [30., 0., 0.]])
    predicted = np.vstack([first * 2 + [30., 0., 11.], first + [0., 0., 1.]])
    result = compare(points, predicted, [[0, 1, 2], [3, 4, 5]], [7, 8],
                     flat_mm=11, angular_face_ids=[7])
    assert result["regions"]["flat"]["max_mm"] == pytest.approx(np.sqrt(11 ** 2 + 2 ** 2))
    assert result["regions"]["flat"]["max_location"]["direction"] == "predicted_to_reference"
    assert result["regions"]["flat"]["max_location"]["reference_face_id"] == 8
    assert result["regions"]["angular"]["max_mm"] == pytest.approx(1)
    assert result["status"] == "exceeds_limits"


@pytest.mark.parametrize("angular_ids", [[999], [7.5], [True]])
def test_unknown_or_noninteger_reference_ids_fail(plane, angular_ids):
    points, triangles, tags = plane
    with pytest.raises(ValueError, match="face.*ID|face.*id"):
        compare(points, points, triangles, tags, angular_face_ids=angular_ids)


@pytest.mark.parametrize("kind", ["nonfinite", "shape", "indices", "degenerate", "tags"])
def test_bad_surface_fails_readably(plane, kind):
    points, triangles, tags = plane
    predicted = points.copy()
    if kind == "nonfinite":
        predicted[0, 0] = np.nan
    elif kind == "shape":
        predicted = predicted[:, :2]
    elif kind == "indices":
        triangles[0, 0] = 100
    elif kind == "degenerate":
        predicted[1] = predicted[0]
    else:
        tags = tags[:1]
    with pytest.raises(ValueError):
        compare(points, predicted, triangles, tags)


def test_sampling_limit_fails_instead_of_silently_reducing_resolution(plane):
    points, triangles, tags = plane
    with pytest.raises(ValueError, match="max_samples|sample.*limit"):
        compare(points, points, triangles, tags, sample_spacing_mm=0.01, max_samples=20)


@pytest.mark.parametrize("kwargs", [{"sample_spacing_mm": 0}, {"sample_spacing_mm": np.nan},
                                   {"flat_mm": -1}, {"angular_mm": np.inf},
                                   {"max_samples": 0}, {"max_samples": 1.5}])
def test_invalid_comparison_settings_fail(plane, kwargs):
    points, triangles, tags = plane
    with pytest.raises(ValueError):
        compare(points, points, triangles, tags, **kwargs)


def test_report_is_offline_and_escapes_external_content(tmp_path, plane):
    pytest.importorskip("plotly")
    from hipform.report import write_report
    from hipform.types import SimulationMesh, SimulationResult

    points, triangles, tags = plane
    comparison = compare(points, points + [0, 0, 11], triangles, tags)
    attack = '</script><script>alert("injected")</script>'
    mesh = SimulationMesh(points, np.empty((0, 4), dtype=int), np.empty(0, dtype=int),
                          triangles, triangles, tags, {"synthetic_example": True, "source": attack,
                                                       "relative_volume_error": {"powder": -0.005}})
    result = SimulationResult(np.tile([0., 0., 11.], (len(points), 1)), np.array([]), [
        {"time_s": 0, "temperature_c": 20, "pressure_mpa": 0,
         "prescribed_relative_density": 0.6, "mean_relative_density": 0.6},
        {"time_s": 60, "temperature_c": 1000, "pressure_mpa": 100,
         "prescribed_relative_density": 0.9, "mean_relative_density": 0.8}], [attack],
         {"model_validity": "outside_small_strain_or_density_range", "final_state": "capsule_attached"})
    output = tmp_path / "report.html"
    write_report(output, mesh, result, comparison, {"source": attack})
    html = output.read_text()
    assert '<script src="http' not in html
    assert "Plotly.newPlot" in html
    assert "理想参数 / 未经标定" in html
    assert "合成包套" in html
    assert "工程验收：未评估" in html
    assert "11.000" in html
    assert attack not in html
    assert "&lt;/script&gt;" in html
    assert '"aspectmode":"data"' in html
    assert '"intensitymode":"cell"' in html
    assert "超出小应变或密度适用范围" in html
    assert "包套仍附着" in html
    assert "-0.500%" in html


def test_surface_plot_excludes_unused_capsule_nodes(plane):
    pytest.importorskip("plotly")
    from hipform.report import _surface_figure
    from hipform.types import SimulationMesh, SimulationResult

    points, triangles, tags = plane
    comparison = compare(points, points + [0, 0, 11], triangles, tags)
    points = np.vstack(([10000., 10000., 10000.], points))
    mesh = SimulationMesh(points, np.empty((0, 4), dtype=int), np.empty(0, dtype=int),
                          triangles + 1, triangles + 1, tags)
    result = SimulationResult(np.tile([0., 0., 11.], (len(points), 1)), np.array([]), [], [])
    figure = _surface_figure(mesh, result, comparison)
    assert len(figure.data[0].x) == 4
    assert max(figure.data[0].x) == 4
    assert len(figure.data[1].z) == 4
    assert max(figure.data[1].z) == 11
    np.testing.assert_array_equal(figure.data[0].i, triangles[:, 0])
