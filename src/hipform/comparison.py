"""Bidirectional sampled distances in the original, unaligned CAD coordinates."""

from math import ceil, isfinite
from numbers import Integral

import numpy as np
import trimesh


def _positive(value, name: str) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be a finite positive number") from exc
    if isinstance(value, (bool, np.bool_)) or not isfinite(numeric) or numeric <= 0:
        raise ValueError(f"{name} must be a finite positive number")
    return numeric


def _points(value, name: str) -> np.ndarray:
    try:
        result = np.asarray(value, dtype=float)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite (n, 3) array") from exc
    if result.ndim != 2 or result.shape[1] != 3 or len(result) < 3:
        raise ValueError(f"{name} must be a finite (n, 3) array with at least three points")
    if not np.isfinite(result).all():
        raise ValueError(f"{name} contains nonfinite coordinates")
    return result


def _indices(value, name: str) -> np.ndarray:
    result = np.asarray(value)
    if not np.issubdtype(result.dtype, np.integer):
        raise ValueError(f"{name} must contain integer indices or face IDs")
    return result


def _subdivisions(points: np.ndarray, triangles: np.ndarray, spacing: float) -> list[int]:
    corners = points[triangles]
    with np.errstate(over="ignore", invalid="ignore"):
        edges = corners[:, [1, 2, 0]] - corners
        lengths = np.linalg.norm(edges, axis=2)
        longest = lengths.max(axis=1)
        double_area = np.linalg.norm(np.cross(edges[:, 0], -edges[:, 2]), axis=1)
        degenerate = double_area <= np.finfo(float).eps * 32 * longest ** 2
    if not np.isfinite(lengths).all() or not np.isfinite(double_area).all():
        raise ValueError("Surface coordinates exceed a numerically representable mesh scale")
    if degenerate.any():
        raise ValueError("Surface contains degenerate or numerically collapsed triangles")
    subdivisions = []
    for length in longest:
        ratio = float(length) / spacing
        if not isfinite(ratio):
            raise ValueError("Requested sample spacing exceeds the max_samples limit")
        # Even subdivision counts include every edge midpoint as well as vertices.
        subdivisions.append(max(2, 2 * ceil(ratio / 2)))
    return subdivisions


def _sample_count(subdivisions: list[int]) -> int:
    return sum((n + 1) * (n + 2) // 2 + (n % 3 != 0) for n in subdivisions)


def _sample(points: np.ndarray, triangles: np.ndarray, subdivisions: list[int]):
    samples, owners = [], []
    for triangle_id, (vertices, n) in enumerate(zip(points[triangles], subdivisions, strict=True)):
        weights = np.array([(i / n, j / n) for i in range(n + 1) for j in range(n + 1 - i)])
        barycentric = np.column_stack((1 - weights.sum(axis=1), weights))
        if n % 3:
            barycentric = np.vstack((barycentric, [1 / 3, 1 / 3, 1 / 3]))
        local = barycentric @ vertices
        samples.append(local)
        owners.append(np.full(len(local), triangle_id, dtype=int))
    return np.concatenate(samples), np.concatenate(owners)


def _closest(points: np.ndarray, triangles: np.ndarray, samples: np.ndarray):
    surface = trimesh.Trimesh(vertices=points, faces=triangles, process=False)
    query = trimesh.proximity.ProximityQuery(surface)
    closest, distances, indices = [], [], []
    for start in range(0, len(samples), 2048):
        xyz, distance, triangle_ids = query.on_surface(samples[start:start + 2048])
        if not np.isfinite(xyz).all() or not np.isfinite(distance).all():
            raise ValueError("Point-to-triangle comparison returned nonfinite distances")
        closest.append(xyz)
        distances.append(distance)
        indices.append(triangle_ids)
    return np.concatenate(closest), np.concatenate(distances), np.concatenate(indices)


def _metrics(distances, samples, closest, tags, directions) -> dict:
    if not len(distances):
        return {"sample_count": 0, "max_mm": None, "p95_mm": None,
                "rms_mm": None, "max_location": None}
    index = int(np.argmax(distances))
    maximum = float(distances[index])
    # Normalizing first avoids overflow when squaring large finite distances.
    rms = maximum * float(np.sqrt(np.mean((distances / maximum) ** 2))) if maximum else 0.
    return {
        "sample_count": len(distances),
        "max_mm": maximum,
        "p95_mm": float(np.percentile(distances, 95)),
        "rms_mm": rms,
        "max_location": {
            "direction": str(directions[index]),
            "point_mm": samples[index].tolist(),
            "closest_point_mm": closest[index].tolist(),
            "reference_face_id": int(tags[index]),
        },
    }


def compare_surfaces(
    reference_points: np.ndarray,
    predicted_points: np.ndarray,
    triangles: np.ndarray,
    face_ids: np.ndarray,
    flat_mm: float = 10.,
    angular_mm: float = 20.,
    angular_face_ids: list[int] | None = None,
    sample_spacing_mm: float = 2.,
    max_samples: int = 100000,
) -> dict:
    """Compare shared-topology surfaces without alignment or rescaling.

    Each triangle receives a barycentric lattice whose subtriangle edges are no
    longer than ``sample_spacing_mm``, plus its centroid. Shared vertices and
    edges retain samples for each owning reference face. P95 and RMS use equal
    sample weights, so these are tessellation-dependent sampled statistics.
    """
    flat_mm = _positive(flat_mm, "flat_mm")
    angular_mm = _positive(angular_mm, "angular_mm")
    sample_spacing_mm = _positive(sample_spacing_mm, "sample_spacing_mm")
    if isinstance(max_samples, (bool, np.bool_)) or not isinstance(max_samples, Integral) or max_samples <= 0:
        raise ValueError("max_samples must be a positive integer")
    reference = _points(reference_points, "reference_points")
    predicted = _points(predicted_points, "predicted_points")
    if predicted.shape != reference.shape:
        raise ValueError("Reference and predicted surfaces must have the same point array shape")
    triangles = _indices(triangles, "triangles")
    if triangles.ndim != 2 or triangles.shape[1] != 3 or not len(triangles):
        raise ValueError("triangles must be a nonempty (k, 3) array")
    if triangles.min() < 0 or triangles.max() >= len(reference):
        raise ValueError("Triangle indices are outside the point array")
    if len(np.unique(np.sort(triangles, axis=1), axis=0)) != len(triangles):
        raise ValueError("Surface contains duplicate triangles")
    face_ids = _indices(face_ids, "reference face IDs")
    if face_ids.shape != (len(triangles),):
        raise ValueError("Reference face IDs must provide one ID per triangle")
    angular_face_ids = [] if angular_face_ids is None else angular_face_ids
    if any(isinstance(tag, (bool, np.bool_)) or not isinstance(tag, Integral) for tag in angular_face_ids):
        raise ValueError("Angular face IDs must be integers")
    angular_ids = sorted(set(int(tag) for tag in angular_face_ids))
    known_ids = set(int(tag) for tag in face_ids)
    unknown = set(angular_ids) - known_ids
    if unknown:
        raise ValueError(f"Angular face IDs are absent from the reference surface: {sorted(unknown)}")

    reference_subdivisions = _subdivisions(reference, triangles, sample_spacing_mm)
    predicted_subdivisions = _subdivisions(predicted, triangles, sample_spacing_mm)
    reference_count = _sample_count(reference_subdivisions)
    predicted_count = _sample_count(predicted_subdivisions)
    if reference_count + predicted_count > max_samples:
        raise ValueError(
            f"Surface sampling needs {reference_count + predicted_count} samples, exceeding "
            f"max_samples={max_samples}; increase max_samples or explicitly choose a larger sample_spacing_mm"
        )
    reference_samples, reference_owners = _sample(reference, triangles, reference_subdivisions)
    predicted_samples, predicted_owners = _sample(predicted, triangles, predicted_subdivisions)
    to_predicted, reference_distances, _ = _closest(predicted, triangles, reference_samples)
    to_reference, predicted_distances, nearest_reference_triangles = _closest(reference, triangles, predicted_samples)
    reference_tags = face_ids[reference_owners]
    predicted_tags = face_ids[nearest_reference_triangles]

    distances = np.concatenate((reference_distances, predicted_distances))
    samples = np.concatenate((reference_samples, predicted_samples))
    closest = np.concatenate((to_predicted, to_reference))
    tags = np.concatenate((reference_tags, predicted_tags))
    directions = np.array(["reference_to_predicted"] * reference_count + ["predicted_to_reference"] * predicted_count)
    surface_nodes = np.unique(triangles)
    tolerance_epsilon = max(1e-9, np.finfo(float).eps * 64 * max(
        float(np.abs(reference[surface_nodes]).max()), float(np.abs(predicted[surface_nodes]).max())))
    regions = {}
    is_angular = np.isin(tags, angular_ids)
    for name, mask, tolerance, region_ids in (
        ("flat", ~is_angular, flat_mm, sorted(known_ids - set(angular_ids))),
        ("angular", is_angular, angular_mm, angular_ids),
    ):
        metrics = _metrics(distances[mask], samples[mask], closest[mask], tags[mask], directions[mask])
        status = "no_samples" if not mask.any() else (
            "within_limits_on_sampled_surface" if metrics["max_mm"] <= tolerance + tolerance_epsilon
            else "exceeds_limits")
        regions[name] = {"face_ids": region_ids, "tolerance_mm": tolerance, "status": status, **metrics}
    reference_field = np.zeros(len(triangles))
    predicted_field = np.zeros(len(triangles))
    np.maximum.at(reference_field, reference_owners, reference_distances)
    np.maximum.at(predicted_field, predicted_owners, predicted_distances)
    return {
        "status": "exceeds_limits" if any(r["status"] == "exceeds_limits" for r in regions.values())
        else "within_limits_on_sampled_surface",
        "units": "mm", "calibrated": False, "engineering_acceptance": "not_assessed", "alignment": "none",
        "sampling": {"method": "barycentric_lattice_with_vertices_edge_midpoints_and_centroids",
                     "spacing_mm": sample_spacing_mm, "max_samples": int(max_samples),
                     "reference_count": reference_count, "predicted_count": predicted_count,
                     "metric_weighting": "equal_sample_weights", "equality_tolerance_mm": tolerance_epsilon},
        "global": _metrics(distances, samples, closest, tags, directions),
        "directions": {
            "reference_to_predicted": _metrics(reference_distances, reference_samples, to_predicted, reference_tags,
                                                directions[:reference_count]),
            "predicted_to_reference": _metrics(predicted_distances, predicted_samples, to_reference, predicted_tags,
                                                directions[reference_count:]),
        },
        "regions": regions,
        "fields": {"reference_triangle_max_mm": reference_field.tolist(),
                   "predicted_triangle_max_mm": predicted_field.tolist()},
        "warnings": [
            "Ideal parameters; this model is uncalibrated and engineering acceptance is not assessed.",
            "Sampled point-to-triangle distances do not certify the true continuous CAD-surface maximum.",
            "P95 and RMS use equal sample weights and depend on surface tessellation and sample spacing.",
            "Reference CAD coordinates are fixed; no alignment, best-fit transform, or scaling was applied.",
        ],
    }


def _closed_surface(points, triangles, name):
    points = _points(points, f"{name} points")
    triangles = _indices(triangles, f"{name} triangles")
    if triangles.ndim != 2 or triangles.shape[1] != 3 or len(triangles) < 4:
        raise ValueError(f"{name} triangles must describe a closed solid")
    if triangles.min() < 0 or triangles.max() >= len(points):
        raise ValueError(f"{name} triangle indices are outside the point array")
    if len(np.unique(np.sort(triangles, axis=1), axis=0)) != len(triangles):
        raise ValueError(f"{name} has duplicate triangles")
    surface = trimesh.Trimesh(points, triangles, process=False)
    if not surface.is_watertight:
        raise ValueError(f"{name} must be a watertight closed surface")
    return points, triangles, surface


def _signed_distances(surface, samples, distances, inside_sign, epsilon):
    signed = np.zeros(len(distances))
    # Points on the surface have no reliable ray-cast sign; only roundoff is zeroed.
    active = np.flatnonzero(distances > epsilon)
    for start in range(0, len(active), 2048):
        indices = active[start:start + 2048]
        inside = surface.contains(samples[indices])
        signed[indices] = distances[indices] * np.where(inside, inside_sign, -inside_sign)
    return signed


def compare_target_surfaces(
    target_points, target_triangles, predicted_points, predicted_triangles,
    target_face_types=None, sample_spacing_mm=3., max_samples=200000,
    planar_limit_mm=10., nonplanar_limit_mm=20.,
) -> dict:
    """Signed bidirectional distances in source coordinates; negative is undersize.

    Limits apply to target CAD face types. Reported maxima are sampled mesh
    distances, not certified bounds on the continuous CAD geometry.
    """
    target_points, target_triangles, target_mesh = _closed_surface(target_points, target_triangles, "Target")
    predicted_points, predicted_triangles, predicted_mesh = _closed_surface(predicted_points, predicted_triangles, "Predicted")
    spacing = _positive(sample_spacing_mm, "sample_spacing_mm")
    planar_limit_mm = _positive(planar_limit_mm, "planar_limit_mm")
    nonplanar_limit_mm = _positive(nonplanar_limit_mm, "nonplanar_limit_mm")
    if isinstance(max_samples, bool) or not isinstance(max_samples, Integral) or max_samples <= 0:
        raise ValueError("max_samples must be a positive integer")
    if target_face_types is None or len(target_face_types) != len(target_triangles):
        raise ValueError("target_face_types requires one CAD type per target triangle")
    planar = np.asarray(target_face_types) == "Plane"
    target_sub = _subdivisions(target_points, target_triangles, spacing)
    predicted_sub = _subdivisions(predicted_points, predicted_triangles, spacing)
    counts = [_sample_count(target_sub), _sample_count(predicted_sub)]
    if sum(counts) > max_samples:
        raise ValueError(f"Target comparison needs {sum(counts)} samples, exceeding max_samples={max_samples}; increase sample_spacing_mm explicitly")
    target_samples, target_owner = _sample(target_points, target_triangles, target_sub)
    predicted_samples, predicted_owner = _sample(predicted_points, predicted_triangles, predicted_sub)
    target_closest, target_distance, _ = _closest(predicted_points, predicted_triangles, target_samples)
    predicted_closest, predicted_distance, nearest = _closest(target_points, target_triangles, predicted_samples)
    scale = max(np.abs(target_points[np.unique(target_triangles)]).max(),
                np.abs(predicted_points[np.unique(predicted_triangles)]).max())
    epsilon = max(1e-9, np.finfo(float).eps * 64 * scale)
    signed_target = _signed_distances(predicted_mesh, target_samples, target_distance, 1, epsilon)
    signed_predicted = _signed_distances(target_mesh, predicted_samples, predicted_distance, -1, epsilon)
    values = np.concatenate((signed_target, signed_predicted))
    samples = np.concatenate((target_samples, predicted_samples))
    closest = np.concatenate((target_closest, predicted_closest))
    owners = np.concatenate((target_owner, nearest))
    is_planar = planar[owners]

    def location(index):
        return {"point_mm": samples[index].tolist(), "closest_point_mm": closest[index].tolist(),
                "target_triangle_index": int(owners[index]),
                "direction": "target_to_predicted" if index < counts[0] else "predicted_to_target"}

    regions, advice = {}, []
    for name, mask, limit in (("planar", is_planar, planar_limit_mm), ("nonplanar", ~is_planar, nonplanar_limit_mm)):
        indices = np.flatnonzero(mask)
        region = {"sample_count": len(indices), "tolerance_mm": limit, "lower_limit_mm": 0., "status": "no_samples"}
        if len(indices):
            low, high = indices[np.argmin(values[mask])], indices[np.argmax(values[mask])]
            region.update(min_signed_mm=float(values[low]), max_signed_mm=float(values[high]),
                          min_location=location(low), max_location=location(high),
                          status="within_limits" if values[low] >= -epsilon and values[high] <= limit + epsilon else "exceeds_limits")
            for code, index, bound in (("undersize", low, 0.), ("oversize", high, limit)):
                if (code == "undersize" and values[index] < -epsilon) or (code == "oversize" and values[index] > limit + epsilon):
                    advice.append({"code": code, "region": name, "signed_deviation_mm": float(values[index]),
                        "required_surface_correction_mm": float(bound - values[index]), "location": location(index),
                        "action": "增大对应主内腔的收缩补偿并重新仿真" if code == "undersize" else "减小对应主内腔的收缩补偿并重新仿真",
                        "scope": "修正量是终态表面回到允许区间所需的局部距离，不是包套壁厚或直接 CAD 偏置量。"})
        regions[name] = region
    positive, negative = np.zeros(len(predicted_triangles)), np.zeros(len(predicted_triangles))
    np.maximum.at(positive, predicted_owner, signed_predicted)
    np.minimum.at(negative, predicted_owner, signed_predicted)
    field = np.where(positive >= -negative, positive, negative)
    return {"status": "failed" if advice else "passed", "units": "mm", "alignment": "none",
        "sign_convention": "negative=undersize; positive=oversize", "regions": regions,
        "sampling": {"spacing_mm": spacing, "target_count": counts[0], "predicted_count": counts[1],
                     "equality_tolerance_mm": epsilon, "max_samples": max_samples},
        "fields": {"predicted_triangle_signed_mm": field.tolist()},
        "upstream_regeneration_advice": advice,
        "warnings": ["Signed surface distances are sampled and do not certify a continuous CAD maximum.",
                     "The surrogate is uncalibrated; dimensional pass is not engineering acceptance."]}
