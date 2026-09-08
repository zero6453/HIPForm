"""Small-strain FE surrogate; no calibrated porous plasticity/contact law.

The prescribed densification strain is mass-conserving in a free body. Actual
constrained density is separately inferred from the deformation Jacobian.
"""

from collections.abc import Callable

import numpy as np
from scipy.sparse import bmat, csr_matrix
from scipy.sparse.linalg import spsolve
from skfem import Basis, BilinearForm, ElementTetP1, ElementVector, LinearForm, MeshTet, asm
from skfem.helpers import ddot, dot, sym_grad, trace

from .config import Powder, SimulationConfig
from .types import SimulationMesh, SimulationResult

GAS_CONSTANT = 8.314462618


def advance_density(density: float, temperature_c: float, pressure_mpa: float, dt_s: float, powder: Powder) -> float:
    if pressure_mpa <= 0 or dt_s <= 0:
        return density
    exponent = -powder.activation_energy_j_per_mol / GAS_CONSTANT * (
        1 / (temperature_c + 273.15) - 1 / (powder.reference_temperature_c + 273.15))
    rate = powder.densification_rate_per_s * np.exp(np.clip(exponent, -700, 50)) * (
        pressure_mpa / powder.reference_pressure_mpa) ** powder.pressure_exponent
    return float(density + (powder.limiting_relative_density - density) * (-np.expm1(-rate * dt_s)))


def stress_free_shrinkage(initial_density: float, density: float) -> float:
    return (initial_density / density) ** (1 / 3) - 1


def maxwell_coefficients(mu, relaxation_time_s, dt_s):
    memory = 1 / (1 + dt_s / relaxation_time_s)
    return mu * memory, memory


def time_grid(config: SimulationConfig) -> np.ndarray:
    times = [0.0]
    for a, b in zip(config.cycle, config.cycle[1:]):
        steps = int(np.ceil((b.time_s - a.time_s) / config.solver.time_step_s))
        if len(times) + steps > config.solver.max_steps + 1:
            raise ValueError("cycle exceeds solver.max_steps; increase time_step_s or max_steps")
        times.extend(np.linspace(a.time_s, b.time_s, steps + 1)[1:])
    return np.asarray(times)


@BilinearForm
def _elasticity(u, v, w):
    eu, ev = sym_grad(u), sym_grad(v)
    return 2 * w.mu * ddot(eu, ev) + (w.bulk - 2 * w.mu / 3) * trace(eu) * trace(ev)


@LinearForm
def _internal_load(v, w):
    return 3 * w.bulk * w.eigen * trace(sym_grad(v)) - ddot(w.memory_stress, sym_grad(v))


@BilinearForm
def _volume_mass(u, v, w):
    return dot(u, v)


def _rigid_constraints(points: np.ndarray, dofs: np.ndarray, magnitude: float) -> csr_matrix:
    a = 0
    b = int(np.argmax(np.linalg.norm(points - points[a], axis=1)))
    e1 = points[b] - points[a]
    e1 /= np.linalg.norm(e1)
    cross = np.cross(points - points[a], e1)
    c = int(np.argmax(np.linalg.norm(cross, axis=1)))
    e3 = np.cross(e1, points[c] - points[a])
    if np.linalg.norm(e3) < 1e-12:
        raise ValueError("mesh is not three-dimensional")
    e3 /= np.linalg.norm(e3)
    e2 = np.cross(e3, e1)
    rows, columns, values = [], [], []
    for row, (node, direction) in enumerate([(a, v) for v in np.eye(3)] + [(b, e2), (b, e3), (c, e3)]):
        for component in range(3):
            rows.append(row)
            columns.append(dofs[component, node])
            values.append(direction[component] * magnitude)
    return csr_matrix((values, (rows, columns)), shape=(6, 3 * len(points)))


def _remove_rigid_motion(points, displacement, volume_mass):
    centered = points - points.mean(axis=0)
    rigid = np.zeros((3 * len(points), 6))
    for axis in range(3):
        rigid[axis::3, axis] = 1
        rigid[:, 3 + axis] = np.cross(np.eye(3)[axis], centered).reshape(-1)
    # Consistent FE volume integration keeps this gauge independent of local
    # node density, unlike an unweighted point-cloud fit.
    coefficients = np.linalg.solve(rigid.T @ volume_mass @ rigid,
                                    rigid.T @ (volume_mass @ displacement.reshape(-1)))
    return displacement - (rigid @ coefficients).reshape(-1, 3)


class _Equilibrium:
    def __init__(self, mesh: SimulationMesh):
        self.mesh = mesh
        self.basis = Basis(MeshTet(np.ascontiguousarray(mesh.points.T),
            np.ascontiguousarray(mesh.tetrahedra.T), sort_t=False), ElementVector(ElementTetP1()))
        self.volume_mass = asm(_volume_mass, self.basis)
        self.pressure_force = np.zeros(self.basis.N)
        tri = mesh.pressure_triangles
        if len(tri):
            p = mesh.points[tri]
            area_normals = np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0]) / 2
            for corner in range(3):
                for axis in range(3):
                    np.add.at(self.pressure_force, self.basis.nodal_dofs[axis, tri[:, corner]], -area_normals[:, axis] / 3)

    def solve(self, bulk, mu, eigenstrain, memory_stress, pressure_mpa):
        basis = self.basis
        q = basis.dx.shape[1]
        bulk_q = np.broadcast_to(np.asarray(bulk)[:, None], (len(bulk), q))
        mu_q = np.broadcast_to(np.asarray(mu)[:, None], (len(mu), q))
        eigen_q = np.broadcast_to(np.asarray(eigenstrain)[:, None], (len(bulk), q))
        stress_q = np.broadcast_to(memory_stress.transpose(1, 2, 0)[..., None], (3, 3, len(bulk), q))
        stiffness = asm(_elasticity, basis, bulk=bulk_q, mu=mu_q)
        force = asm(_internal_load, basis, bulk=bulk_q, eigen=eigen_q, memory_stress=stress_q) + pressure_mpa * self.pressure_force
        constraints = _rigid_constraints(self.mesh.points, basis.nodal_dofs, float(np.median(stiffness.diagonal())))
        system = bmat([[stiffness, constraints.T], [constraints, None]], format="csc")
        solution = spsolve(system, np.r_[force, np.zeros(6)])
        u = solution[:basis.N]
        if not np.isfinite(u).all():
            raise ValueError("equilibrium solve returned non-finite displacements")
        residual = np.linalg.norm(stiffness @ u - force) / max(np.linalg.norm(force), 1)
        displacement = _remove_rigid_motion(self.mesh.points, u[basis.nodal_dofs].T, self.volume_mass)
        u[basis.nodal_dofs] = displacement.T
        gradient = basis.interpolate(u).grad.mean(axis=-1).transpose(2, 0, 1)
        return displacement, gradient, float(residual)


def solve_equilibrium(mesh, bulk, mu, eigenstrain, memory_stress, pressure_mpa):
    return _Equilibrium(mesh).solve(bulk, mu, eigenstrain, memory_stress, pressure_mpa)


def _moduli(young, poisson):
    return young / (3 * (1 - 2 * poisson)), young / (2 * (1 + poisson))


def _young_at_temperature(material, temperature, initial_temperature):
    ratio = np.clip((temperature - initial_temperature) / (material.reference_temperature_c - initial_temperature), 0, 1)
    return material.young_modulus_mpa + ratio * (material.hot_young_modulus_mpa - material.young_modulus_mpa)


def simulate(mesh: SimulationMesh, config: SimulationConfig, progress: Callable | None = None) -> SimulationResult:
    powder_mask = mesh.material == 0
    if not powder_mask.any():
        raise ValueError("mesh has no powder elements")
    if not np.isin(mesh.material, [0, 1]).all():
        raise ValueError("unknown material label")
    powder, capsule = config.powder, config.capsule
    solver = _Equilibrium(mesh)
    vertices = mesh.points[mesh.tetrahedra]
    initial_volumes = np.abs(np.linalg.det((vertices[:, 1:] - vertices[:, :1]).transpose(0, 2, 1))) / 6
    if np.any(initial_volumes <= 1e-12):
        raise ValueError("mesh contains degenerate tetrahedra")
    powder_volumes = initial_volumes[powder_mask]
    powder_mass = powder.initial_relative_density * powder_volumes.sum()
    n = len(mesh.tetrahedra)
    viscous_strain = np.zeros((n, 3, 3))
    density = powder.initial_relative_density
    times = time_grid(config)
    cycle_t = [p.time_s for p in config.cycle]
    temperatures = [p.temperature_c for p in config.cycle]
    pressures = [p.pressure_mpa for p in config.cycle]
    reference_temp = temperatures[0]
    history = []
    warnings = ["Uncalibrated ideal material parameters; engineering acceptance is not assessed.",
                "Small-strain, uniform-temperature, bonded-interface model; no buckling, damage, welds or sliding contact.",
                "Densification kinetics use external pressure, not local porous-plastic stress or capsule back-pressure."]
    displacement = np.zeros_like(mesh.points)
    actual_density = np.full(n, np.nan)
    max_strain_ever = 0.0
    for i, time in enumerate(times):
        dt = 0 if i == 0 else time - times[i-1]
        temp = float(np.interp(time, cycle_t, temperatures))
        pressure = float(np.interp(time, cycle_t, pressures))
        midtime = time - dt / 2
        density = advance_density(density, float(np.interp(midtime, cycle_t, temperatures)),
                                  float(np.interp(midtime, cycle_t, pressures)), dt, powder)
        ep = _young_at_temperature(powder, temp, reference_temp) * density**powder.density_stiffness_exponent
        ec = _young_at_temperature(capsule, temp, reference_temp)
        kp, mup = _moduli(ep, powder.poisson_ratio)
        kc, muc = _moduli(ec, capsule.poisson_ratio)
        exponent = capsule.relaxation_activation_energy_j_per_mol / GAS_CONSTANT * (
            1 / (temp + 273.15) - 1 / (capsule.reference_temperature_c + 273.15))
        tau = capsule.hot_relaxation_time_s * np.exp(np.clip(exponent, -50, 50))
        capsule_mu, memory = maxwell_coefficients(muc, tau, dt)
        bulk = np.where(powder_mask, kp, kc)
        shear = np.where(powder_mask, mup, capsule_mu)
        eigen = np.where(powder_mask,
            (1 + stress_free_shrinkage(powder.initial_relative_density, density)) *
                (1 + powder.thermal_expansion_per_k * (temp-reference_temp)) - 1,
            capsule.thermal_expansion_per_k * (temp-reference_temp))
        memory_stress = -2 * shear[:, None, None] * viscous_strain
        displacement, gradient, residual = solver.solve(bulk, shear, eigen, memory_stress, pressure)
        if residual > config.solver.linear_residual_tolerance:
            raise ValueError(f"equilibrium residual {residual:.3g} exceeds configured tolerance")
        strain = (gradient + gradient.transpose(0, 2, 1)) / 2
        deviator = strain - np.trace(strain, axis1=1, axis2=2)[:, None, None] * np.eye(3) / 3
        viscous_strain[~powder_mask] = (memory * viscous_strain + (1-memory) * deviator)[~powder_mask]
        jacobian = np.linalg.det(np.eye(3)[None] + gradient)
        if np.any(jacobian <= 0):
            raise ValueError("deformation inverted tetrahedra; this small-strain model is outside its usable range")
        # Relative density is referenced to the fully dense material at the same temperature.
        thermal_volume = (1 + powder.thermal_expansion_per_k * (temp-reference_temp))**3
        actual_density = np.where(powder_mask, powder.initial_relative_density * thermal_volume / jacobian, 1.0)
        deformed_volumes = powder_volumes * jacobian[powder_mask]
        actual = actual_density[powder_mask]
        max_strain = float(np.max(np.abs(np.linalg.eigvalsh(strain))))
        max_strain_ever = max(max_strain_ever, max_strain)
        row = {"time_s": float(time), "temperature_c": temp, "pressure_mpa": pressure,
            "prescribed_relative_density": density,
            "mean_relative_density": float(np.average(actual, weights=deformed_volumes)),
            "min_relative_density": float(actual.min()), "max_relative_density": float(actual.max()),
            "max_displacement_mm": float(np.linalg.norm(displacement, axis=1).max()),
            "powder_volume_mm3": float(deformed_volumes.sum()),
            "mass_error_relative": float(abs(np.sum(actual * deformed_volumes / thermal_volume)-powder_mass)/powder_mass),
            "linear_residual_relative": residual, "max_abs_principal_strain": max_strain}
        history.append(row)
        if progress:
            progress(i, len(times), row)
    if max_strain_ever > config.solver.strain_warning_threshold:
        warnings.append(f"Principal strain reached {max_strain_ever:.1%}, exceeding the configured small-strain warning threshold; quantitative prediction requires finite-strain validation.")
    density_invalid = any(h["max_relative_density"] > 1.001 or h["min_relative_density"] <= 0 for h in history)
    if density_invalid:
        warnings.append("Locally inferred relative density leaves its physical range; no clipping was applied. Quantitative prediction is not valid.")
    if pressures[-1] != 0 or abs(temperatures[-1]-reference_temp) > 1e-6:
        warnings.append("Cycle ends under load or above initial temperature; this is not an unloaded room-temperature shape.")
    return SimulationResult(displacement, actual_density, history, warnings, {
        "model": config.model, "calibrated": False, "engineering_acceptance": "not_assessed",
        "model_validity": "outside_small_strain_or_density_range" if density_invalid or max_strain_ever > config.solver.strain_warning_threshold else "uncalibrated",
        "configuration_units": "mm, MPa, s, Celsius; kg/mm3 for solid density",
        "powder_mass_kg": float(powder_mass * powder.solid_density_kg_per_mm3),
        "final_state": "capsule_attached", "alignment": "CAD coordinates; rigid displacement removed by consistent FE volume projection; no geometry scaling",
        "nodes": len(mesh.points), "tetrahedra": n})
