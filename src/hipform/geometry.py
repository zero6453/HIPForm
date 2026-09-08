"""Conformal two-material meshing with an explicitly identified sealed exterior."""

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import shutil
import threading

import gmsh
import numpy as np

from .types import SimulationMesh


_GMSH_LOCK = threading.RLock()
_TET_FACES = np.array([[0, 1, 2], [0, 1, 3], [0, 2, 3], [1, 2, 3]])


@contextmanager
def _model(name):
    with _GMSH_LOCK:
        if gmsh.isInitialized():
            raise ValueError("Gmsh is already active; finish the existing model before meshing.")
        gmsh.initialize(readConfigFiles=False)
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.option.setString("Geometry.OCCTargetUnit", "MM")
        gmsh.model.add(name)
        try:
            yield
        finally:
            gmsh.finalize()


def _positive(value, name):
    if not np.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be finite and greater than zero.")
    return float(value)


def _input_info(path):
    return {"path": str(path.resolve()), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def _import_solid(path, name):
    path = Path(path)
    if not path.is_file():
        raise ValueError(f"{name} STEP file does not exist: {path}")
    try:
        shapes = gmsh.model.occ.importShapes(str(path), highestDimOnly=True)
    except Exception as exc:
        raise ValueError(f"Could not read {name} STEP solid: {exc}") from exc
    solids = [shape for shape in shapes if shape[0] == 3]
    if len(solids) != 1:
        raise ValueError(f"{name} STEP must contain exactly one connected solid; found {len(solids)}.")
    volume = gmsh.model.occ.getMass(*solids[0])
    if not np.isfinite(volume) or volume <= 0:
        raise ValueError(f"{name} STEP has no positive solid volume.")
    return solids[0], float(volume)


def generate_example(cavity_step: Path, output_dir: Path, wall_mm: float = 3.0) -> dict[str, Path]:
    """Create a synthetic enclosing box minus the actual cavity, in source coordinates.

    ``wall_mm`` is the minimum clearance from the cavity's bounding box, not
    a conformal constant-thickness offset. The provenance sidecar is mandatory.
    """
    wall_mm = _positive(wall_mm, "wall_mm")
    cavity_step, output_dir = Path(cavity_step), Path(output_dir)
    output_cavity = output_dir / "compensated-cavity.step"
    output_capsule = output_dir / "synthetic-capsule.step"
    provenance_path = output_capsule.with_suffix(".provenance.json")
    for output in (output_cavity, output_capsule, provenance_path):
        if output.exists() or output.is_symlink():
            raise ValueError(f"Example output already exists: {output}. Choose a new output directory.")
    if output_dir.exists() and not output_dir.is_dir():
        raise ValueError(f"Example output directory is not a directory: {output_dir}")
    with _model("synthetic HIP capsule"):
        cavity, cavity_volume = _import_solid(cavity_step, "Cavity")
        bounds = np.asarray(gmsh.model.occ.getBoundingBox(*cavity))
        lower, size = bounds[:3] - wall_mm, bounds[3:] - bounds[:3] + 2 * wall_mm
        box = gmsh.model.occ.addBox(*lower, *size)
        capsule, _ = gmsh.model.occ.cut([(3, box)], [cavity])
        if len(capsule) != 1 or capsule[0][0] != 3:
            raise ValueError("Could not form a single sealed synthetic capsule around the cavity.")
        gmsh.model.occ.synchronize()
        output_dir.mkdir(parents=True, exist_ok=True)
        gmsh.write(str(output_capsule))
        shutil.copy2(cavity_step, output_cavity)
        provenance = {
            "synthetic_example": True,
            "construction": "Axis-aligned cavity bounding box expanded by wall_mm, minus the original cavity solid",
            "description": "Synthetic sealed capsule for numerical demonstration; not the user's manufactured capsule",
            "minimum_bounding_box_clearance_mm": wall_mm,
            "cavity_volume_mm3": cavity_volume,
            "source_cavity": _input_info(cavity_step),
            "capsule": _input_info(output_capsule),
        }
        provenance_path.write_text(json.dumps(provenance, indent=2) + "\n")
    return {"cavity_step": output_cavity, "capsule_step": output_capsule, "provenance": provenance_path}


def _apply_seal(capsule, seal):
    if not isinstance(seal, dict) or set(seal) != {"center_mm", "radius_mm", "height_mm"}:
        raise ValueError("seal requires center_mm, radius_mm and height_mm; center_mm is the cylinder base and its axis is +Z.")
    center = np.asarray(seal["center_mm"], dtype=float)
    if center.shape != (3,) or not np.all(np.isfinite(center)):
        raise ValueError("seal.center_mm must contain three finite coordinates.")
    radius = _positive(seal["radius_mm"], "seal.radius_mm")
    height = _positive(seal["height_mm"], "seal.height_mm")
    plug = gmsh.model.occ.addCylinder(*center, 0, 0, height, radius)
    fused, _ = gmsh.model.occ.fuse([capsule], [(3, plug)])
    if len(fused) != 1 or fused[0][0] != 3:
        raise ValueError("The explicit seal does not connect to the capsule as one solid. Check its base center, radius and +Z height.")
    return fused[0]


def _overlap_volume(first, second):
    copies = gmsh.model.occ.copy([first, second])
    common, _ = gmsh.model.occ.intersect(copies[:1], copies[1:])
    overlap = sum(gmsh.model.occ.getMass(*entity) for entity in common if entity[0] == 3)
    if common:
        gmsh.model.occ.remove(common, recursive=True)
    return float(overlap)


def _facets(points, tetrahedra):
    faces = np.concatenate([tetrahedra[:, local] for local in _TET_FACES])
    owners = np.tile(np.arange(len(tetrahedra)), 4)
    opposite = np.concatenate([tetrahedra[:, local] for local in (3, 2, 1, 0)])
    vertices = points[faces]
    inward = np.einsum(
        "ij,ij->i",
        np.cross(vertices[:, 1] - vertices[:, 0], vertices[:, 2] - vertices[:, 0]),
        points[opposite] - vertices[:, 0],
    ) > 0
    faces[inward] = faces[inward][:, [0, 2, 1]]
    _, first, inverse, counts = np.unique(np.sort(faces, axis=1), axis=0, return_index=True, return_inverse=True, return_counts=True)
    if np.any(counts > 2):
        raise ValueError("Mesh has a non-manifold tetrahedral interface.")
    boundary = first[counts == 1]
    return faces[boundary], owners[boundary], inverse, counts, owners


def _components(count, pairs):
    parent = np.arange(count)

    def find(index):
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    for first, second in pairs:
        root_first, root_second = find(first), find(second)
        if root_first != root_second:
            parent[root_second] = root_first
    return np.array([find(index) for index in range(count)])


def _check_connected(points, tetrahedra, label):
    _, _, inverse, counts, owners = _facets(points, tetrahedra)
    order = np.argsort(inverse, kind="stable")
    starts = np.cumsum(counts) - counts
    shared = starts[counts == 2]
    pairs = np.column_stack((owners[order[shared]], owners[order[shared + 1]]))
    if len(np.unique(_components(len(tetrahedra), pairs))) != 1:
        raise ValueError(f"{label} contains disconnected material bodies in the mesh.")


def _closed_boundary_components(triangles):
    edges = np.concatenate([triangles[:, local] for local in ([0, 1], [1, 2], [2, 0])])
    owners = np.tile(np.arange(len(triangles)), 3)
    _, inverse, counts = np.unique(np.sort(edges, axis=1), axis=0, return_inverse=True, return_counts=True)
    if np.any(counts != 2):
        raise ValueError("The assembly boundary is open or non-manifold; a sealed pressure exterior cannot be identified.")
    order = np.argsort(inverse, kind="stable").reshape(-1, 2)
    labels = _components(len(triangles), owners[order])
    return [np.flatnonzero(labels == label) for label in np.unique(labels)]


def _signed_volume(points, triangles):
    vertices = points[triangles] - points.mean(axis=0)
    return float(np.einsum("ij,ij->i", vertices[:, 0], np.cross(vertices[:, 1], vertices[:, 2])).sum() / 6)


def _read_mesh(powder_volumes, capsule_volumes):
    node_tags, flat_points, _ = gmsh.model.mesh.getNodes()
    order = np.argsort(node_tags)
    node_tags, points = node_tags[order], flat_points.reshape(-1, 3)[order]
    tetrahedra, materials = [], []
    for material, volumes in enumerate((powder_volumes, capsule_volumes)):
        for tag in volumes:
            element_types, _, element_nodes = gmsh.model.mesh.getElements(3, tag)
            for element_type, nodes in zip(element_types, element_nodes, strict=True):
                if element_type != 4:
                    raise ValueError("Meshing produced unsupported elements; only linear tetrahedra are supported.")
                tetrahedra.append(np.searchsorted(node_tags, nodes).reshape(-1, 4))
                materials.append(np.full(len(nodes) // 4, material, dtype=np.int8))
    if not tetrahedra or any(not np.any(np.concatenate(materials) == material) for material in (0, 1)):
        raise ValueError("Meshing did not produce both powder and capsule tetrahedra.")
    tetrahedra, materials = np.concatenate(tetrahedra), np.concatenate(materials)
    vertices = points[tetrahedra]
    determinants = np.linalg.det(np.stack([vertices[:, index] - vertices[:, 0] for index in (1, 2, 3)], axis=2))
    if np.any(np.abs(determinants) <= np.finfo(float).eps):
        raise ValueError("Meshing produced zero-volume tetrahedra.")
    negative = determinants < 0
    tetrahedra[negative] = tetrahedra[negative][:, [0, 2, 1, 3]]
    used = np.unique(tetrahedra)
    return points[used], np.searchsorted(used, tetrahedra), materials, node_tags[used], np.abs(determinants) / 6


def build_mesh(
    cavity_step: Path,
    capsule_step: Path,
    mesh_size_mm: float = 5.0,
    seal: dict | None = None,
) -> SimulationMesh:
    """Import mm STEP solids and mesh a bonded powder/capsule assembly.

    An optional cylindrical seal uses ``center_mm`` as its base center and
    points along +Z. A disconnected or ineffective seal is rejected. Pressure
    acts only on the outer closed component; sealed internal voids are unloaded.
    """
    mesh_size_mm = _positive(mesh_size_mm, "mesh_size_mm")
    cavity_step, capsule_step = Path(cavity_step), Path(capsule_step)
    with _model("HIP powder and capsule"):
        powder, powder_volume = _import_solid(cavity_step, "Cavity")
        capsule, capsule_volume = _import_solid(capsule_step, "Capsule")
        if seal is not None:
            capsule = _apply_seal(capsule, seal)
            capsule_volume = float(gmsh.model.occ.getMass(*capsule))
        overlap = _overlap_volume(powder, capsule)
        if overlap > max(powder_volume, capsule_volume) * 1e-8:
            raise ValueError(f"Powder and capsule overlap by {overlap:.6g} mm^3. Capsule STEP must contain wall material only.")
        try:
            fragments, mapping = gmsh.model.occ.fragment([powder], [capsule])
            gmsh.model.occ.synchronize()
            powder_volumes = {tag for dim, tag in mapping[0] if dim == 3}
            capsule_volumes = {tag for dim, tag in mapping[1] if dim == 3}
            if not powder_volumes or not capsule_volumes or powder_volumes & capsule_volumes:
                raise ValueError("Powder and capsule could not be resolved into disjoint material volumes.")
            if {tag for dim, tag in fragments if dim == 3} != powder_volumes | capsule_volumes:
                raise ValueError("Boolean fragmentation produced unassigned solid volumes.")
            for name, volumes, physical_id in (("powder", powder_volumes, 1), ("capsule", capsule_volumes, 2)):
                gmsh.model.addPhysicalGroup(3, sorted(volumes), physical_id)
                gmsh.model.setPhysicalName(3, physical_id, name)
            surfaces = sorted({abs(tag) for dim, tag in gmsh.model.getBoundary([(3, tag) for tag in sorted(powder_volumes)], combined=True, oriented=False) if dim == 2})
            reference_faces = {
                str(tag): {
                    "id": tag,
                    "type": gmsh.model.getType(2, tag),
                    "center_mm": list(gmsh.model.occ.getCenterOfMass(2, tag)),
                    "area_mm2": float(gmsh.model.occ.getMass(2, tag)),
                }
                for tag in surfaces
            }
            gmsh.option.setNumber("Mesh.MeshSizeMin", mesh_size_mm)
            gmsh.option.setNumber("Mesh.MeshSizeMax", mesh_size_mm)
            gmsh.option.setNumber("Mesh.ElementOrder", 1)
            gmsh.option.setNumber("Mesh.Algorithm3D", 1)
            gmsh.option.setNumber("General.NumThreads", 1)
            gmsh.model.mesh.generate(3)
            points, tetrahedra, material, node_tags, element_volumes = _read_mesh(sorted(powder_volumes), sorted(capsule_volumes))
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError(f"Could not build a conformal powder/capsule mesh: {exc}") from exc
        for region, label in ((0, "Powder"), (1, "Capsule")):
            _check_connected(points, tetrahedra[material == region], label)
        _check_connected(points, tetrahedra, "Assembly")
        exterior, owners, _, _, _ = _facets(points, tetrahedra)
        components = _closed_boundary_components(exterior)
        volumes = [_signed_volume(points, exterior[component]) for component in components]
        outer_index = int(np.argmax(np.abs(volumes)))
        if volumes[outer_index] <= 0 or any(volume > 1e-7 for index, volume in enumerate(volumes) if index != outer_index):
            raise ValueError("A unique enclosing pressure exterior could not be identified.")
        outer = components[outer_index]
        if np.any(material[owners[outer]] == 0):
            raise ValueError("Powder is exposed on the exterior boundary: the capsule is open or the explicit seal is ineffective.")
        powder_triangles, _, _, _, _ = _facets(points, tetrahedra[material == 0])
        face_map = {}
        for tag in surfaces:
            element_types, _, element_nodes = gmsh.model.mesh.getElements(2, tag)
            for element_type, nodes in zip(element_types, element_nodes, strict=True):
                if element_type != 2:
                    raise ValueError("Reference surfaces require linear triangular elements.")
                triangles = np.searchsorted(node_tags, nodes).reshape(-1, 3)
                face_map.update({tuple(sorted(triangle)): tag for triangle in triangles})
        try:
            powder_face_ids = np.array([face_map[tuple(sorted(triangle))] for triangle in powder_triangles], dtype=np.int64)
        except KeyError as exc:
            raise ValueError("A powder boundary triangle could not be linked to its CAD reference face.") from exc
        metadata = {
            "units": "mm",
            "step_unit_normalization": "OpenCASCADE imports STEP declared units into millimetres (Geometry.OCCTargetUnit=MM)",
            "inputs": {"cavity": _input_info(cavity_step), "capsule": _input_info(capsule_step)},
            "mesh_size_mm": mesh_size_mm,
            "bounds_mm": [points.min(axis=0).tolist(), points.max(axis=0).tolist()],
            "synthetic_example": False,
            "seal": seal,
            "seal_convention": "center_mm is cylinder base center; axis +Z",
            "reference_faces": reference_faces,
            "reference_face_id_convention": "Gmsh surface tags after conformal fragmentation; tied to these input hashes",
            "material_counts": {"powder": int(np.count_nonzero(material == 0)), "capsule": int(np.count_nonzero(material == 1))},
            "cad_volume_mm3": {"powder": powder_volume, "capsule": capsule_volume},
            "mesh_volume_mm3": {"powder": float(element_volumes[material == 0].sum()), "capsule": float(element_volumes[material == 1].sum())},
            "pressure_enclosed_volume_mm3": volumes[outer_index],
            "unloaded_boundary_components": len(components) - 1,
            "overlap_volume_mm3": overlap,
        }
        metadata["relative_volume_error"] = {
            name: metadata["mesh_volume_mm3"][name] / metadata["cad_volume_mm3"][name] - 1
            for name in ("powder", "capsule")
        }
        provenance_path = capsule_step.with_suffix(".provenance.json")
        if provenance_path.is_file():
            provenance = json.loads(provenance_path.read_text())
            if provenance.get("capsule", {}).get("sha256") != metadata["inputs"]["capsule"]["sha256"]:
                raise ValueError("Capsule provenance hash does not match the STEP file.")
            metadata["synthetic_example"] = bool(provenance.get("synthetic_example"))
            metadata["provenance"] = provenance
    return SimulationMesh(points, tetrahedra, material, exterior[outer], powder_triangles, powder_face_ids, metadata)
