"""Report what the bonded solver can establish about capsule contact."""

import numpy as np

from .geometry import material_boundary


def assess_capsule_contact(mesh, result, tolerances):
    """Do not turn a shared-node constraint into a physical gap acceptance."""
    if result.metadata.get("model") != "small_strain_thermoviscoelastic_densification_surrogate":
        raise ValueError("Contact assessment requires the known bonded-interface solver")
    displacement = np.asarray(result.displacement)
    if displacement.shape != mesh.points.shape or not np.isfinite(displacement).all():
        raise ValueError("Contact assessment requires finite displacement at every mesh node")
    capsule_faces = {tuple(sorted(face)) for face in material_boundary(mesh, 1)}
    interface = np.array([tuple(sorted(face)) in capsule_faces for face in mesh.powder_triangles])
    if not interface.any():
        raise ValueError("No shared powder/capsule interface was found")
    reference_faces = mesh.metadata["reference_faces"]
    types = np.array([reference_faces[str(tag)]["type"] for tag in mesh.powder_face_ids])
    regions = {}
    for name, mask, limit in (("planar", types == "Plane", tolerances.flat_mm),
                              ("nonplanar", types != "Plane", tolerances.angular_mm)):
        count = int(np.count_nonzero(interface & mask))
        regions[name] = {"status": "not_assessed" if count else "no_interface",
                         "interface_triangle_count": count, "lower_limit_mm": 0., "upper_limit_mm": float(limit)}
    capsule_nodes = np.unique(mesh.tetrahedra[mesh.material == 1])
    return {
        "status": "not_assessed", "reason_code": "bonded_interface_prevents_separation",
        "basis": "formed_powder_to_capsule_inner_wall", "units": "mm",
        "interface_model": "bonded_shared_nodes", "imposed_interface_gap_mm": 0.,
        "reason": "粉末与包套内壁共用节点，间隙被绑定约束为 0 mm；当前模型不能预测分离，不能据此判断间隙合格。",
        "classification": "source powder CAD face types, preserved through deformation",
        "interface_triangle_count": int(interface.sum()),
        "excluded_powder_triangle_count": int((~interface).sum()),
        "regions": regions,
        "capsule": {"element_count": int(np.count_nonzero(mesh.material == 1)),
                    "node_count": len(capsule_nodes),
                    "max_displacement_mm": float(np.linalg.norm(displacement[capsule_nodes], axis=1).max())},
    }
