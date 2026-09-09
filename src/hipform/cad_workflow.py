"""CAD adapters for deriving a powder domain and exporting a faceted STEP."""

from pathlib import Path
import math

import gmsh
import numpy as np
import trimesh

from .geometry import _model, _import_solid, _vent_tube_exterior


def _circular_cylinder(face):
    if gmsh.model.getType(2, face) != "Cylinder":
        return None
    rims = gmsh.model.getBoundary([(2, face)], combined=True, oriented=False)
    if len(rims) != 2 or any(gmsh.model.getType(*rim) != "Circle" for rim in rims):
        return None
    radii = np.asarray([gmsh.model.occ.getMass(*rim) / (2 * math.pi) for rim in rims])
    centers = np.asarray([gmsh.model.occ.getCenterOfMass(*rim) for rim in rims])
    height = np.linalg.norm(centers[1] - centers[0])
    if height <= 1e-6 or not np.isclose(radii[0], radii[1], rtol=1e-7):
        return None
    if not np.isclose(gmsh.model.occ.getMass(2, face), 2 * math.pi * radii[0] * height, rtol=1e-7):
        return None
    return {"rims": rims, "centers": centers, "radius": float(radii[0])}


def _open_vent():
    """Recognize an annular mouth shared by coaxial inner/outer cylinders."""
    candidates = []
    cylinders = {tag: cylinder for _, tag in gmsh.model.getEntities(2)
                 if (cylinder := _circular_cylinder(tag)) is not None}
    for _, face in gmsh.model.getEntities(2):
        if gmsh.model.getType(2, face) != "Plane":
            continue
        rims = gmsh.model.getBoundary([(2, face)], combined=True, oriented=False)
        if len(rims) != 2 or any(gmsh.model.getType(*rim) != "Circle" for rim in rims):
            continue
        adjoining = [[cylinder for cylinder in cylinders.values() if rim in cylinder["rims"]] for rim in rims]
        if any(len(owners) != 1 for owners in adjoining):
            continue
        (inner_rim, inner), (outer_rim, outer) = sorted(
            zip(rims, [owners[0] for owners in adjoining]), key=lambda pair: pair[1]["radius"])
        radius, outer_radius = inner["radius"], outer["radius"]
        mouth = inner["centers"][inner["rims"].index(inner_rim)]
        base = inner["centers"][1 - inner["rims"].index(inner_rim)]
        outer_mouth = outer["centers"][outer["rims"].index(outer_rim)]
        outer_base = outer["centers"][1 - outer["rims"].index(outer_rim)]
        height = float(np.linalg.norm(mouth - base))
        axis = (mouth - base) / height
        neck = float((outer_base - base) @ axis)
        if (outer_radius <= radius or not np.allclose(mouth, outer_mouth, rtol=0, atol=1e-6)
                or not np.allclose(outer_base, base + neck * axis, rtol=0, atol=1e-6)
                or not 1e-6 < neck < height - 1e-6):
            continue
        if not np.isclose(gmsh.model.occ.getMass(2, face), math.pi * (outer_radius**2 - radius**2), rtol=1e-7):
            continue
        candidates.append({"base": base, "mouth": mouth, "axis": axis, "height": height,
                           "radius": radius, "outer_radius": outer_radius})
    if len(candidates) > 1:
        raise ValueError("Multiple straight vent mouths are ambiguous; provide an explicitly sealed capsule.")
    return candidates[0] if candidates else None


def _cap_vent(capsule, vent):
    if vent is None:
        return capsule
    # Continue the tube outward by its radial wall thickness. The cap neither
    # shortens the existing bore nor replaces any manufactured wall material.
    thickness = vent["outer_radius"] - vent["radius"]
    cap = gmsh.model.occ.addCylinder(*vent["mouth"], *(vent["axis"] * thickness), vent["outer_radius"])
    fused, _ = gmsh.model.occ.fuse([capsule], [(3, cap)])
    if len(fused) != 1 or fused[0][0] != 3:
        raise ValueError("Virtual vent cap did not connect to the original capsule.")
    return fused[0]


def _enclosed_void(capsule):
    """Subtract the actual wall from a padded box and discard its exterior."""
    bounds = np.asarray(gmsh.model.occ.getBoundingBox(*capsule))
    margin = max(float(np.max(bounds[3:] - bounds[:3])) * .1, 1.)
    lower, upper = bounds[:3] - margin, bounds[3:] + margin
    box = gmsh.model.occ.addBox(*lower, *(upper - lower))
    voids, _ = gmsh.model.occ.cut([(3, box)], [capsule], removeTool=False)
    enclosed = []
    exterior = []
    for entity in voids:
        if entity[0] != 3:
            continue
        box = np.asarray(gmsh.model.occ.getBoundingBox(*entity))
        if np.any(box[:3] <= lower + 1e-6) or np.any(box[3:] >= upper - 1e-6):
            exterior.append(entity)
        else:
            enclosed.append(entity)
    gmsh.model.occ.remove(exterior, recursive=True)
    if len(enclosed) != 1:
        raise ValueError("Capsule must enclose exactly one connected main cavity; an open or ambiguous cavity cannot be extracted.")
    return enclosed[0]


def _sealed_vent(cavity, capsule):
    """Find a blind circular bore whose base is inside a capped exterior tube."""
    gmsh.model.occ.synchronize()
    surfaces = [tag for dim, tag in gmsh.model.getBoundary([cavity], oriented=False) if dim == 2]
    capsule_faces = [tag for dim, tag in gmsh.model.getBoundary([capsule], oriented=False) if dim == 2]
    candidates = []
    for face in surfaces:
        cylinder = _circular_cylinder(face)
        if cylinder is None:
            continue
        for cap_index, rim in enumerate(cylinder["rims"]):
            if not any(gmsh.model.getType(2, tag) == "Plane" and
                       gmsh.model.getBoundary([(2, tag)], combined=True, oriented=False) == [rim]
                       for tag in surfaces):
                continue
            base_rim = cylinder["rims"][1 - cap_index]
            if not any(gmsh.model.getType(2, tag) == "Plane" and base_rim in
                       (edges := gmsh.model.getBoundary([(2, tag)], combined=True, oriented=False)) and len(edges) > 1
                       for tag in surfaces):
                continue
            base = cylinder["centers"][1 - cap_index]
            mouth = cylinder["centers"][cap_index]
            height = float(np.linalg.norm(mouth - base))
            axis = (mouth - base) / height
            tube = _vent_tube_exterior(capsule_faces, base, axis, cylinder["radius"], height, 1e-6)
            if tube is not None:
                candidates.append({"base": base, "mouth": mouth, "axis": axis, "height": height,
                                   "radius": cylinder["radius"], "outer_radius": tube["outer_radius_mm"]})
    if len(candidates) > 1:
        raise ValueError("Multiple sealed vent bores are ambiguous; the main powder cavity cannot be extracted.")
    return candidates[0] if candidates else None


def derive_powder_domain(capsule_step: Path, powder_step: Path, sealed_capsule_step: Path) -> dict:
    """Extract the enclosed main cavity from the actual wall in source coordinates.

    A recognized open straight tube receives an outward cap whose thickness
    equals its radial wall thickness. The tube bore stays empty, including for
    a presealed tube. Other openings and multiple enclosed cavities are rejected.
    """
    capsule_step, powder_step, sealed_capsule_step = map(Path, (capsule_step, powder_step, sealed_capsule_step))
    for output in (powder_step, sealed_capsule_step):
        if output.exists() or output.is_symlink():
            raise ValueError(f"CAD output already exists: {output}")
    if powder_step.resolve() == sealed_capsule_step.resolve():
        raise ValueError("Powder and sealed capsule outputs must be different files.")
    with _model("derive powder from capsule"):
        # Unify coplanar Boolean faces without moving surfaces. Otherwise a
        # trimmed bore leaves a circular seam in the planar powder boundary.
        gmsh.option.setNumber("Geometry.OCCBooleanSimplify", 2)
        capsule, capsule_volume = _import_solid(capsule_step, "Capsule")
        gmsh.model.occ.synchronize()
        open_vent = _open_vent()
        capsule = _cap_vent(capsule, open_vent)
        cavity = _enclosed_void(capsule)
        vent = open_vent or _sealed_vent(cavity, capsule)
        if vent is not None:
            cavity_volume = gmsh.model.occ.getMass(*cavity)
            bore = gmsh.model.occ.addCylinder(*vent["base"], *(vent["axis"] * vent["height"]), vent["radius"])
            powder, _ = gmsh.model.occ.cut([cavity], [(3, bore)])
            if len(powder) != 1 or powder[0][0] != 3:
                raise ValueError("Removing the vent bore did not leave one main powder cavity.")
            cavity = powder[0]
            removed_volume = cavity_volume - gmsh.model.occ.getMass(*cavity)
            if not np.isclose(removed_volume, math.pi * vent["radius"]**2 * vent["height"], rtol=1e-7, atol=1e-6):
                raise ValueError("The identified vent bore is not wholly connected to the enclosed cavity.")
        bounds = list(gmsh.model.occ.getBoundingBox(*cavity))
        gmsh.model.occ.remove([capsule], recursive=True)
        gmsh.model.occ.synchronize()
        powder_step.parent.mkdir(parents=True, exist_ok=True)
        gmsh.write(str(powder_step))
        gmsh.model.remove()
        gmsh.model.add("sealed capsule export")
        capsule, _ = _import_solid(capsule_step, "Capsule")
        _cap_vent(capsule, open_vent)
        gmsh.model.occ.synchronize()
        sealed_capsule_step.parent.mkdir(parents=True, exist_ok=True)
        gmsh.write(str(sealed_capsule_step))
    return {"powder_step": powder_step, "sealed_capsule_step": sealed_capsule_step,
            "body_bounds_mm": bounds, "original_capsule_volume_mm3": capsule_volume,
            "vent_inner_radius_mm": None if vent is None else vent["radius"],
            "vent_outer_radius_mm": None if vent is None else vent["outer_radius"],
            "seal": None if open_vent is None else {"center_mm": vent["mouth"].tolist(),
                "axis": vent["axis"].tolist(), "radius_mm": vent["outer_radius"],
                "height_mm": vent["outer_radius"] - vent["radius"]}}


def export_faceted_step(points: np.ndarray, triangles: np.ndarray, output: Path) -> None:
    """Write a watertight triangulated BREP STEP from a closed surface."""
    points, triangles, output = np.asarray(points, float), np.asarray(triangles), Path(output)
    if not np.issubdtype(triangles.dtype, np.integer):
        raise ValueError("STEP triangle indices must be integers")
    if points.ndim != 2 or points.shape[1] != 3 or triangles.ndim != 2 or triangles.shape[1] != 3:
        raise ValueError("STEP surface requires points (n,3) and triangles (m,3)")
    if len(points) < 4 or not np.isfinite(points).all() or len(triangles) < 4:
        raise ValueError("STEP surface is empty or nonfinite")
    if triangles.min() < 0 or triangles.max() >= len(points) or len(np.unique(np.sort(triangles, axis=1), axis=0)) != len(triangles):
        raise ValueError("STEP surface has invalid or duplicate triangle indices")
    edges = np.concatenate((triangles[:, [0, 1]], triangles[:, [1, 2]], triangles[:, [2, 0]]))
    if not np.all(np.unique(np.sort(edges, axis=1), axis=0, return_counts=True)[1] == 2):
        raise ValueError("STEP surface must be a closed two-manifold")
    surface = trimesh.Trimesh(points, triangles, process=False)
    if not surface.is_volume or np.any(surface.area_faces <= 0):
        raise ValueError("STEP surface must have consistently oriented, nondegenerate faces enclosing positive volume")
    with _model("faceted predicted cavity"):
        point_ids = {int(index): gmsh.model.occ.addPoint(*points[index]) for index in np.unique(triangles)}
        edge_ids = {}
        surfaces = []
        for a, b, c in triangles:
            loop = []
            for first, second in ((a, b), (b, c), (c, a)):
                key = tuple(sorted((int(first), int(second))))
                if key not in edge_ids:
                    edge_ids[key] = gmsh.model.occ.addLine(
                        point_ids[key[0]], point_ids[key[1]])
                line = edge_ids[key]
                loop.append(line if first < second else -line)
            curve = gmsh.model.occ.addCurveLoop(loop)
            surfaces.append(gmsh.model.occ.addPlaneSurface([curve]))
        shell = gmsh.model.occ.addSurfaceLoop(surfaces, sewing=True)
        volume = gmsh.model.occ.addVolume([shell])
        if gmsh.model.occ.getMass(3, volume) <= 0:
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
