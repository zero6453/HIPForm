"""Signed target checks use known solids and preserve source coordinates."""

import numpy as np
import pytest
import trimesh

from hipform.comparison import compare_target_surfaces


def compare(target, predicted, **kwargs):
    return compare_target_surfaces(target.vertices, target.faces, predicted.vertices,
                                   predicted.faces, ["Plane"] * len(target.faces), **kwargs)


def test_same_surface_has_zero_diff_despite_floating_point_roundoff():
    target = trimesh.creation.icosphere(subdivisions=1, radius=3)
    result = compare(target, target.copy())
    assert result["status"] == "measured"
    assert result["regions"]["planar"]["min_signed_mm"] == 0


def test_target_diff_measures_undersize_without_using_capsule_gap_limits():
    target = trimesh.creation.box(extents=[10, 10, 10])
    result = compare(target, trimesh.creation.box(extents=[8, 8, 8]))
    assert result["status"] == "measured"
    assert result["regions"]["planar"]["min_signed_mm"] == pytest.approx(-np.sqrt(3))
    assert len(result["regions"]["planar"]["min_location"]["point_mm"]) == 3
    assert "tolerance_mm" not in result["regions"]["planar"]
    assert "upstream_regeneration_advice" not in result


def test_signed_shift_detects_both_missing_and_excess_material():
    target = trimesh.creation.box(extents=[10, 10, 10])
    predicted = target.copy()
    predicted.apply_translation([11, 0, 0])
    result = compare(target, predicted)
    assert result["status"] == "measured"
    assert result["regions"]["planar"]["min_signed_mm"] < 0
    assert result["regions"]["planar"]["max_signed_mm"] > 10


def test_sampling_budget_is_checked_before_allocating_points():
    target = trimesh.creation.box()
    with pytest.raises(ValueError, match="max_samples"):
        compare(target, target, sample_spacing_mm=1e-100, max_samples=10)


def test_nonplanar_target_reports_diff_without_a_gap_verdict():
    target = trimesh.creation.icosphere(subdivisions=1, radius=10)
    predicted = trimesh.creation.icosphere(subdivisions=1, radius=25)
    args = (target.vertices, target.faces, predicted.vertices, predicted.faces,
            ["Sphere"] * len(target.faces))
    result = compare_target_surfaces(*args, sample_spacing_mm=10)
    assert result["status"] == "measured"
    assert result["regions"]["planar"]["status"] == "no_samples"
    assert result["regions"]["nonplanar"]["max_signed_mm"] == pytest.approx(15)
    assert "tolerance_mm" not in result["regions"]["nonplanar"]


def test_invalid_indices_are_rejected_before_distance_queries():
    target = trimesh.creation.box()
    triangles = target.faces.copy()
    triangles[0, 0] = -1
    with pytest.raises(ValueError, match="indices"):
        compare_target_surfaces(target.vertices, triangles, target.vertices, target.faces,
                                ["Plane"] * len(triangles))
