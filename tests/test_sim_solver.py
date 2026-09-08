"""Analytical limits for the deliberately simplified HIP process model."""

import pytest

np = pytest.importorskip("numpy")
MeshTet = pytest.importorskip("skfem").MeshTet


def test_configuration_rejects_bad_cycles_and_nonfinite_values():
    from hipform.config import SimulationConfig
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        SimulationConfig.model_validate({"cycle": [
            {"time_s": 0, "temperature_c": 20, "pressure_mpa": 0},
            {"time_s": 0, "temperature_c": 900, "pressure_mpa": 100},
        ]})
    with pytest.raises(ValidationError):
        SimulationConfig.model_validate({"powder": {"initial_relative_density": 1.1}})
    with pytest.raises(ValidationError):
        SimulationConfig.model_validate({"solver": {"time_step_s": float("nan")}})


def test_densification_no_pressure_and_mass_conserving_shrinkage():
    from hipform.config import SimulationConfig
    from hipform.solver import advance_density, stress_free_shrinkage

    cfg = SimulationConfig()
    d0 = cfg.powder.initial_relative_density
    assert advance_density(d0, 920, 0, 7200, cfg.powder) == d0
    d1 = advance_density(d0, 920, 100, 7200, cfg.powder)
    assert d0 < d1 < cfg.powder.limiting_relative_density
    scale = 1 + stress_free_shrinkage(d0, d1)
    assert d1 * scale**3 == pytest.approx(d0)
    assert advance_density(d0, 850, 100, 7200, cfg.powder) < d1


def cube_mesh(material=0):
    from hipform.types import SimulationMesh
    mesh = MeshTet.init_tensor(np.linspace(0, 10, 3), np.linspace(0, 10, 3), np.linspace(0, 10, 3))
    faces = mesh.facets[:, mesh.boundary_facets()].T.copy()
    center = mesh.p.mean(axis=1)
    for tri in faces:
        a, b, c = mesh.p[:, tri].T
        if np.dot(np.cross(b-a, c-a), (a+b+c)/3-center) < 0:
            tri[1], tri[2] = tri[2], tri[1]
    return SimulationMesh(mesh.p.T, mesh.t.T, np.full(mesh.nelements, material),
                          faces, faces, np.ones(len(faces), dtype=int))


def test_free_uniform_eigenstrain_and_no_rigid_translation():
    from hipform.solver import solve_equilibrium
    mesh = cube_mesh()
    e = -0.02
    u, _, residual = solve_equilibrium(mesh, np.full(len(mesh.tetrahedra), 1e4),
        np.full(len(mesh.tetrahedra), 5e3), np.full(len(mesh.tetrahedra), e),
        np.zeros((len(mesh.tetrahedra), 3, 3)), 0)
    expected = e * (mesh.points - mesh.points.mean(axis=0))
    assert u == pytest.approx(expected, abs=1e-9)
    assert residual < 1e-8


def test_hydrostatic_pressure_has_correct_sign_and_magnitude():
    from hipform.solver import solve_equilibrium
    mesh = cube_mesh()
    n = len(mesh.tetrahedra)
    pressure, bulk = 3.0, 1000.0
    u, _, residual = solve_equilibrium(mesh, np.full(n, bulk), np.full(n, 500),
        np.zeros(n), np.zeros((n, 3, 3)), pressure)
    expected = -pressure / (3 * bulk) * (mesh.points - mesh.points.mean(axis=0))
    assert u == pytest.approx(expected, abs=1e-9)
    assert residual < 1e-8


def test_maxwell_relaxes_deviatoric_stress_without_relaxing_bulk():
    from hipform.solver import maxwell_coefficients
    effective_mu, memory = maxwell_coefficients(1000., 60., 60.)
    assert effective_mu == pytest.approx(500.)
    assert memory == pytest.approx(0.5)


def test_zero_cycle_stays_undeformed_and_mass_is_preserved():
    from hipform.config import SimulationConfig
    from hipform.solver import simulate
    cfg = SimulationConfig.model_validate({"cycle": [
        {"time_s": 0, "temperature_c": 20, "pressure_mpa": 0},
        {"time_s": 60, "temperature_c": 20, "pressure_mpa": 0},
    ]})
    result = simulate(cube_mesh(), cfg)
    assert result.displacement == pytest.approx(np.zeros_like(result.displacement), abs=1e-10)
    assert result.density == pytest.approx(np.full(len(result.density), 0.65))
    assert result.history[-1]["mass_error_relative"] < 1e-12


def test_time_grid_preserves_all_cycle_breakpoints():
    from hipform.config import SimulationConfig
    from hipform.solver import time_grid
    cfg = SimulationConfig()
    times = time_grid(cfg)
    assert all(point.time_s in times for point in cfg.cycle)
    assert np.diff(times).max() <= cfg.solver.time_step_s + 1e-8


def test_rigid_gauge_does_not_move_with_nonuniform_mesh_density():
    from hipform.solver import solve_equilibrium
    from hipform.types import SimulationMesh
    grid = MeshTet.init_tensor(np.array([0., 1., 2., 5., 10.]),
                               np.array([0., 5., 10.]), np.array([0., 5., 10.]))
    mesh = SimulationMesh(grid.p.T, grid.t.T, np.zeros(grid.nelements, dtype=int),
                          np.empty((0, 3), dtype=int), np.empty((0, 3), dtype=int), np.empty(0))
    n = grid.nelements
    u, _, _ = solve_equilibrium(mesh, np.full(n, 1e4), np.full(n, 5e3),
        np.full(n, -0.02), np.zeros((n, 3, 3)), 0)
    assert u == pytest.approx(-0.02 * (mesh.points - 5.), abs=1e-9)


def test_free_hot_powder_density_matches_prescribed_density_after_unloading():
    from hipform.config import SimulationConfig
    from hipform.solver import simulate
    cfg = SimulationConfig.model_validate({"powder": {"initial_relative_density": 0.95,
        "activation_energy_j_per_mol": 0}, "cycle": [
        {"time_s": 0, "temperature_c": 20, "pressure_mpa": 0},
        {"time_s": 120, "temperature_c": 920, "pressure_mpa": 100},
        {"time_s": 6000, "temperature_c": 920, "pressure_mpa": 100},
        {"time_s": 6120, "temperature_c": 920, "pressure_mpa": 0},
    ]})
    row = simulate(cube_mesh(), cfg).history[-1]
    assert row["mean_relative_density"] == pytest.approx(row["prescribed_relative_density"], abs=1e-9)
