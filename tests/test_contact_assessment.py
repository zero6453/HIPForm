"""A bonded interface cannot certify real powder-to-capsule separation."""

import numpy as np

from hipform.config import SimulationConfig
from hipform.types import SimulationMesh, SimulationResult


def test_shared_interface_is_reported_as_constrained_not_accepted():
    from hipform.assessment import assess_capsule_contact

    points = np.array([[0., 0., 0.], [1., 0., 0.], [0., 1., 0.], [0., 0., 1.], [0., 0., -1.]])
    mesh = SimulationMesh(points, np.array([[0, 1, 2, 3], [0, 2, 1, 4]]), np.array([0, 1]),
        np.empty((0, 3), dtype=int), np.array([[0, 2, 1], [0, 1, 3], [1, 2, 3], [2, 0, 3]]),
        np.array([1, 2, 2, 2]), {"reference_faces": {"1": {"type": "Plane"}, "2": {"type": "Sphere"}}})
    config = SimulationConfig()
    result = SimulationResult(-.1 * points, np.array([.65, 1.]), [], [], {"model": config.model})
    assessment = assess_capsule_contact(mesh, result, config.tolerances)
    assert assessment["status"] == "not_assessed"
    assert assessment["reason_code"] == "bonded_interface_prevents_separation"
    assert assessment["imposed_interface_gap_mm"] == 0
    assert assessment["interface_triangle_count"] == 1
    assert assessment["excluded_powder_triangle_count"] == 3
    assert assessment["regions"]["planar"] == {
        "status": "not_assessed", "interface_triangle_count": 1,
        "lower_limit_mm": 0., "upper_limit_mm": 10.,
    }
    assert assessment["regions"]["nonplanar"]["status"] == "no_interface"
    assert assessment["regions"]["nonplanar"]["upper_limit_mm"] == 20
    assert assessment["capsule"]["max_displacement_mm"] > 0
