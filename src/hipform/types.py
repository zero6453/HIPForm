"""Shared arrays use millimetres; material 0 is powder and 1 is capsule."""

from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass
class SimulationMesh:
    points: np.ndarray
    tetrahedra: np.ndarray
    material: np.ndarray
    pressure_triangles: np.ndarray
    powder_triangles: np.ndarray
    powder_face_ids: np.ndarray
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SimulationResult:
    displacement: np.ndarray
    density: np.ndarray
    history: list[dict[str, float]]
    warnings: list[str]
    metadata: dict[str, Any] = field(default_factory=dict)
