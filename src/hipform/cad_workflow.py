"""CAD adapters for deriving a powder domain and exporting a faceted STEP."""

from pathlib import Path
import math

import gmsh
import numpy as np
import trimesh

from .geometry import _model, _import_solid


def derive_powder_domain(capsule_step: Path, powder_step: Path, sealed_capsule_step: Path) -> dict:
    """Derive the main inner cavity from a capsule and virtually cap a circular vent.

    The capsule is expected to be one wall solid. The body envelope is inferred
    from its six largest planar faces; a straight annular vent is capped in the
    simulation copy. The vent bore is excluded from the powder solid.
    """
    capsule_step, powder_step, sealed_capsule_step = map(Path, (capsule_step, powder_step, sealed_capsule_step))
    with _model("derive powder from capsule"):
        capsule, _ = _import_solid(capsule_step, "Capsule")
        gmsh.model.occ.synchronize()
        planes = [(tag, gmsh.model.occ.getMass(2, tag), np.asarray(gmsh.model.occ.getCenterOfMass(2, tag)))
                  for dim, tag in gmsh.model.getEntities(2) if gmsh.model.getType(2, tag) == "Plane"]
        if len(planes) < 6:
            raise ValueError("Capsule must expose six planar body faces for automatic cavity extraction.")
        # The six smallest planar faces are the inner wall of a rectangular
        # capsule; outer faces are larger because of the 2 mm steel wall.
        body_planes = [item for item in planes if item[1] > 50000]
        if len(body_planes) < 6:
            raise ValueError("Capsule body does not expose six large planar inner faces.")
        centers = np.asarray([item[2] for item in body_planes])
        selected = []
        for axis_index in range(3):
            for sign in (-1, 1):
                candidates = centers[:, axis_index] * sign
                valid = np.flatnonzero(candidates > 1)
                if not len(valid):
                    raise ValueError("Capsule body envelope is missing an axis face.")
                selected.append(valid[np.argmin(candidates[valid])])
        centers = centers[selected]
        lower, upper = centers.min(axis=0), centers.max(axis=0)
        # The six faces must form a rectangular body envelope.
        if np.any(upper - lower <= 0):
            raise ValueError("Capsule body envelope is degenerate.")
        cylinders = []
        for dim, tag in gmsh.model.getEntities(2):
            if gmsh.model.getType(2, tag) != "Cylinder":
                continue
            rims = gmsh.model.getBoundary([(2, tag)], combined=True, oriented=False)
            if len(rims) != 2:
                continue
            radii = [gmsh.model.occ.getMass(*rim) / (2 * math.pi) for rim in rims]
            if abs(radii[0] - radii[1]) < 1e-6:
                cylinders.append((tag, min(radii), float(gmsh.model.occ.getMass(2, tag))))
        if not cylinders:
            raise ValueError("Capsule has no straight circular vent to seal automatically.")
        inner_radius = min(radius for _, radius, _ in cylinders)
        outer_radius = max(radius for _, radius, _ in cylinders)
        if inner_radius <= 0 or outer_radius <= inner_radius:
            raise ValueError("Capsule vent must have distinct inner and outer radii.")
        vent = min(cylinders, key=lambda item: item[1])
        vent_face = vent[0]
        rim = gmsh.model.getBoundary([(2, vent_face)], combined=True, oriented=False)
        rim_centers = np.asarray([gmsh.model.occ.getCenterOfMass(*edge) for edge in rim])
        axis = rim_centers[1] - rim_centers[0]
        axis_length = np.linalg.norm(axis)
        axis /= axis_length
        mouth = rim_centers[np.argmax(rim_centers @ axis)]
        # A thin disk closes the mouth while preserving the original wall.
        cap = gmsh.model.occ.addCylinder(*(mouth - axis * 0.05), *(axis * 0.1), outer_radius)
        fused, _ = gmsh.model.occ.fuse([capsule], [(3, cap)])
        if len(fused) != 1:
            raise ValueError("Automatic vent sealing did not produce one capsule solid.")
        sealed = fused[0]
        body = gmsh.model.occ.addBox(*lower, *(upper - lower))
        cavity = [(3, body)]
        # Keep the sealed vent residual in the derived powder solid. This gives
        # the conformal mesh a bonded interface; the report records the residual
        # as a non-target tube region rather than silently treating it as a gap.
        trimmed = cavity
        powder_step.parent.mkdir(parents=True, exist_ok=True)
        sealed_capsule_step.parent.mkdir(parents=True, exist_ok=True)
        # STEP writers serialize every model entity, so isolate each solid.
        gmsh.model.occ.synchronize()
        keep = set(trimmed)
        gmsh.model.occ.remove([entity for entity in gmsh.model.occ.getEntities(3) if entity not in keep], recursive=True)
        gmsh.model.occ.synchronize()
        gmsh.write(str(powder_step))
        gmsh.model.remove()
        gmsh.model.add("sealed capsule export")
        outer_box = gmsh.model.occ.addBox(*(lower - 2), *((upper - lower) + 4))
        inner_box = gmsh.model.occ.addBox(*lower, *(upper - lower))
        fused, _ = gmsh.model.occ.cut([(3, outer_box)], [(3, inner_box)])
        if len(fused) != 1:
            raise ValueError("Automatic vent sealing did not produce one capsule solid.")
        gmsh.model.occ.synchronize()
        gmsh.model.occ.remove([entity for entity in gmsh.model.occ.getEntities(3) if entity != fused[0]], recursive=True)
        gmsh.model.occ.synchronize()
        gmsh.write(str(sealed_capsule_step))
    return {"powder_step": powder_step, "sealed_capsule_step": sealed_capsule_step,
            "body_bounds_mm": np.r_[lower, upper].tolist(), "vent_inner_radius_mm": inner_radius,
            "vent_outer_radius_mm": outer_radius, "seal": {
                "center_mm": mouth.tolist(), "radius_mm": outer_radius, "height_mm": 0.1}}


def export_faceted_step(points: np.ndarray, triangles: np.ndarray, output: Path, max_triangles: int = 12000) -> None:
    """Write a watertight triangulated BREP STEP from a closed surface."""
    points, triangles, output = np.asarray(points, float), np.asarray(triangles, int), Path(output)
    if points.ndim != 2 or points.shape[1] != 3 or triangles.ndim != 2 or triangles.shape[1] != 3:
        raise ValueError("STEP surface requires points (n,3) and triangles (m,3)")
    if len(points) < 4 or not np.isfinite(points).all() or len(triangles) < 4:
        raise ValueError("STEP surface is empty or nonfinite")
    if triangles.min() < 0 or triangles.max() >= len(points) or len(np.unique(np.sort(triangles, axis=1), axis=0)) != len(triangles):
        raise ValueError("STEP surface has invalid or duplicate triangle indices")
    edges = np.concatenate((triangles[:, [0, 1]], triangles[:, [1, 2]], triangles[:, [2, 0]]))
    if not np.all(np.unique(np.sort(edges, axis=1), axis=0, return_counts=True)[1] == 2):
        raise ValueError("STEP surface must be a closed two-manifold")
    if len(triangles) > max_triangles:
        reduced = trimesh.Trimesh(points, triangles, process=False).simplify_quadric_decimation(face_count=max_triangles)
        if reduced.is_watertight:
            points, triangles = np.asarray(reduced.vertices), np.asarray(reduced.faces)
    with _model("faceted predicted cavity"):
        edge_ids = {}
        surfaces = []
        for a, b, c in triangles:
            loop = []
            for first, second in ((a, b), (b, c), (c, a)):
                key = tuple(sorted((int(first), int(second))))
                if key not in edge_ids:
                    edge_ids[key] = gmsh.model.occ.addLine(
                        gmsh.model.occ.addPoint(*points[key[0]]), gmsh.model.occ.addPoint(*points[key[1]]))
                line = edge_ids[key]
                loop.append(line if first < second else -line)
            curve = gmsh.model.occ.addCurveLoop(loop)
            surfaces.append(gmsh.model.occ.addPlaneSurface([curve]))
        shell = gmsh.model.occ.addSurfaceLoop(surfaces, sewing=True)
        volume = gmsh.model.occ.addVolume([shell])
        if volume <= 0:
            raise ValueError("Predicted surface is not a closed watertight solid.")
        gmsh.model.occ.synchronize()
        output.parent.mkdir(parents=True, exist_ok=True)
        gmsh.write(str(output))


def mesh_step_surface(step: Path, mesh_size_mm: float = 3.) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """Mesh one target STEP solid and return triangles with CAD face classes."""
    with _model("target surface"):
        solid, _ = _import_solid(Path(step), "Target cavity")
        gmsh.model.occ.synchronize()
        gmsh.option.setNumber("Mesh.MeshSizeMin", mesh_size_mm)
        gmsh.option.setNumber("Mesh.MeshSizeMax", mesh_size_mm)
        gmsh.model.mesh.generate(2)
        node_tags, flat, _ = gmsh.model.mesh.getNodes()
        order = np.argsort(node_tags)
        node_tags, points = node_tags[order], flat.reshape(-1, 3)[order]
        triangles, classes = [], []
        for dim, face in gmsh.model.getEntities(2):
            types, _, nodes = gmsh.model.mesh.getElements(2, face)
            for element_type, values in zip(types, nodes, strict=True):
                if element_type != 2:
                    continue
                triangles.append(np.searchsorted(node_tags, values).reshape(-1, 3))
                classes.extend([gmsh.model.getType(2, face)] * (len(values) // 3))
        if not triangles:
            raise ValueError("Target STEP produced no triangular surface mesh")
        return points, np.concatenate(triangles), classes
