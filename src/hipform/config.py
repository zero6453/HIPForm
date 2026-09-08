"""Strict, unit-explicit configuration for an uncalibrated HIP approximation."""

from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

Positive = Annotated[float, Field(gt=0)]
Nonnegative = Annotated[float, Field(ge=0)]


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class CyclePoint(Settings):
    time_s: Nonnegative
    temperature_c: Annotated[float, Field(gt=-273.15)]
    pressure_mpa: Nonnegative


class Powder(Settings):
    name: str = "TC4 idealized powder (uncalibrated)"
    initial_relative_density: Annotated[float, Field(gt=0, le=1)] = 0.65
    limiting_relative_density: Annotated[float, Field(gt=0, le=1)] = 0.995
    solid_density_kg_per_mm3: Positive = 4.43e-6
    young_modulus_mpa: Positive = 110000
    hot_young_modulus_mpa: Positive = 45000
    poisson_ratio: Annotated[float, Field(gt=-1, lt=0.5)] = 0.34
    density_stiffness_exponent: Positive = 3
    thermal_expansion_per_k: Nonnegative = 9e-6
    reference_temperature_c: Annotated[float, Field(gt=-273.15)] = 920
    reference_pressure_mpa: Positive = 100
    densification_rate_per_s: Nonnegative = 6e-4
    activation_energy_j_per_mol: Nonnegative = 150000
    pressure_exponent: Positive = 1.5

    @model_validator(mode="after")
    def density_order(self):
        if self.initial_relative_density > self.limiting_relative_density:
            raise ValueError("initial_relative_density must not exceed limiting_relative_density")
        return self


class Capsule(Settings):
    name: str = "20 steel idealized Maxwell solid (uncalibrated)"
    young_modulus_mpa: Positive = 200000
    hot_young_modulus_mpa: Positive = 25000
    poisson_ratio: Annotated[float, Field(gt=-1, lt=0.5)] = 0.3
    thermal_expansion_per_k: Nonnegative = 14e-6
    reference_temperature_c: Annotated[float, Field(gt=-273.15)] = 920
    hot_relaxation_time_s: Positive = 60
    relaxation_activation_energy_j_per_mol: Nonnegative = 180000


class Solver(Settings):
    mesh_size_mm: Positive = 6
    time_step_s: Positive = 120
    max_steps: Annotated[int, Field(gt=0, le=10000)] = 1000
    strain_warning_threshold: Positive = 0.05
    linear_residual_tolerance: Positive = 1e-6


class Tolerances(Settings):
    flat_mm: Positive = 10
    angular_mm: Positive = 20
    angular_face_ids: list[Annotated[int, Field(gt=0)]] = Field(default_factory=list)
    sample_spacing_mm: Positive = 3
    max_samples: Annotated[int, Field(gt=0)] = 200000


class Seal(Settings):
    # A +Z cylinder, with center_mm specifying its base centre.
    center_mm: tuple[float, float, float]
    radius_mm: Positive
    height_mm: Positive


def default_cycle():
    return [CyclePoint(time_s=t, temperature_c=temp, pressure_mpa=p) for t, temp, p in (
        (0, 20, 0), (3600, 920, 100), (10800, 920, 100),
        (14400, 200, 100), (16200, 20, 0),
    )]


class SimulationConfig(Settings):
    schema_version: Literal[1] = 1
    model: Literal["small_strain_thermoviscoelastic_densification_surrogate"] = "small_strain_thermoviscoelastic_densification_surrogate"
    powder: Powder = Field(default_factory=Powder)
    capsule: Capsule = Field(default_factory=Capsule)
    solver: Solver = Field(default_factory=Solver)
    tolerances: Tolerances = Field(default_factory=Tolerances)
    cycle: list[CyclePoint] = Field(default_factory=default_cycle)
    seal: Seal | None = None

    @model_validator(mode="after")
    def cycle_valid(self):
        if len(self.cycle) < 2 or self.cycle[0].time_s != 0:
            raise ValueError("cycle needs at least two points and must start at time_s=0")
        if self.cycle[0].pressure_mpa != 0:
            raise ValueError("cycle must start at zero gauge pressure")
        if any(b.time_s <= a.time_s for a, b in zip(self.cycle, self.cycle[1:])):
            raise ValueError("cycle times must strictly increase")
        if self.powder.reference_temperature_c <= self.cycle[0].temperature_c:
            raise ValueError("powder reference temperature must exceed initial temperature")
        if self.capsule.reference_temperature_c <= self.cycle[0].temperature_c:
            raise ValueError("capsule reference temperature must exceed initial temperature")
        return self


def load_config(path: Path | None) -> SimulationConfig:
    if path is None:
        return SimulationConfig()
    try:
        with path.open(encoding="utf-8") as stream:
            payload = yaml.safe_load(stream)
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML configuration: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("configuration must be a YAML mapping")
    return SimulationConfig.model_validate(payload)


def write_default_config(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        stream.write("# IDEALIZED DEMO VALUES, NOT CALIBRATED TC4/20 STEEL MATERIAL DATA.\n")
        yaml.safe_dump(SimulationConfig().model_dump(mode="json"), stream, sort_keys=False)
