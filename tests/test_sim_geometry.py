"""Geometric checks use small analytic solids, independent of the CAD example."""

import json
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
gmsh = pytest.importorskip("gmsh")

from hipform import geometry


def _assembly(tmp_path: Path, *, opening=False, overlap=False):
    cavity = tmp_path / "cavity.step"
    capsule = tmp_path / "capsule.step"
    gmsh.initialize()
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.model.add("cavity")
        gmsh.model.occ.addBox(-5, -5, -5, 10, 10, 10)
        gmsh.model.occ.synchronize()
        gmsh.write(str(cavity))
        gmsh.model.add("capsule")
        outer = gmsh.model.occ.addBox(-6, -6, -6, 12, 12, 12)
        if not overlap:
            inner = gmsh.model.occ.addBox(-5, -5, -5, 10, 10, 10)
            shell, _ = gmsh.model.occ.cut([(3, outer)], [(3, inner)])
            if opening:
                bore = gmsh.model.occ.addCylinder(0, 0, 5, 0, 0, 2, 1)
                gmsh.model.occ.cut(shell, [(3, bore)])
        gmsh.model.occ.synchronize()
        gmsh.write(str(capsule))
    finally:
        gmsh.finalize()
    return cavity, capsule


def _enclosed_volume(points, triangles):
    vertices = points[triangles]
    return np.einsum("ij,ij->i", vertices[:, 0], np.cross(vertices[:, 1], vertices[:, 2])).sum() / 6


def test_sealed_assembly_has_conformal_materials_and_outward_surfaces(tmp_path):
    cavity, capsule = _assembly(tmp_path)
    mesh = geometry.build_mesh(cavity, capsule, mesh_size_mm=3)
    assert set(mesh.material) == {0, 1}
    assert np.unique(mesh.tetrahedra).size == len(mesh.points)
    assert len(mesh.powder_triangles) == len(mesh.powder_face_ids)
    assert len(np.unique(mesh.powder_face_ids)) == 6
    assert _enclosed_volume(mesh.points, mesh.pressure_triangles) == pytest.approx(1728)
    assert _enclosed_volume(mesh.points, mesh.powder_triangles) == pytest.approx(1000)
    for triangles in (mesh.pressure_triangles, mesh.powder_triangles):
        p = mesh.points[triangles]
        normals = np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0])
        assert np.all(np.einsum("ij,ij->i", p.mean(axis=1), normals) > 0)
    material_faces = []
    for material in (0, 1):
        tetrahedra = mesh.tetrahedra[mesh.material == material]
        faces = np.concatenate([tetrahedra[:, face] for face in ([0, 1, 2], [0, 1, 3], [0, 2, 3], [1, 2, 3])])
        unique, counts = np.unique(np.sort(faces, axis=1), axis=0, return_counts=True)
        material_faces.append({tuple(face) for face in unique[counts == 1]})
    assert material_faces[0] <= material_faces[1]
    assert mesh.metadata["inputs"]["cavity"]["sha256"]
    assert len(mesh.metadata["reference_faces"]) == 6


def test_overlapping_capsule_and_powder_are_rejected(tmp_path):
    cavity, capsule = _assembly(tmp_path, overlap=True)
    with pytest.raises(ValueError, match="overlap"):
        geometry.build_mesh(cavity, capsule, mesh_size_mm=3)


def test_open_capsule_exposes_powder_and_is_rejected(tmp_path):
    cavity, capsule = _assembly(tmp_path, opening=True)
    with pytest.raises(ValueError, match="(?i)(exposed|open|exterior)"):
        geometry.build_mesh(cavity, capsule, mesh_size_mm=2)


def test_explicit_seal_closes_opening_and_keeps_internal_vacuum_unloaded(tmp_path):
    cavity, capsule = _assembly(tmp_path, opening=True)
    mesh = geometry.build_mesh(
        cavity,
        capsule,
        mesh_size_mm=2,
        seal={"center_mm": [0, 0, 6], "radius_mm": 1.1, "height_mm": 1.5},
    )
    assert mesh.metadata["unloaded_boundary_components"] == 1
    assert mesh.metadata["seal"]["center_mm"] == [0, 0, 6]
    assert _enclosed_volume(mesh.points, mesh.pressure_triangles) > 1728


def test_example_is_generated_with_synthetic_provenance(tmp_path):
    cavity, _ = _assembly(tmp_path)
    original = cavity.read_bytes()
    generated = geometry.generate_example(cavity, tmp_path / "example", wall_mm=2)
    assert set(generated) == {"cavity_step", "capsule_step", "provenance"}
    assert all(path.is_file() for path in generated.values())
    assert cavity.read_bytes() == original
    provenance = json.loads(generated["provenance"].read_text())
    assert provenance["synthetic_example"] is True
    mesh = geometry.build_mesh(generated["cavity_step"], generated["capsule_step"], mesh_size_mm=3)
    assert mesh.metadata["synthetic_example"] is True
    assert _enclosed_volume(mesh.points, mesh.pressure_triangles) == pytest.approx(14**3, rel=1e-6)


@pytest.mark.parametrize("size", [0, -1, float("nan"), float("inf")])
def test_invalid_mesh_size_is_rejected_before_meshing(tmp_path, size):
    with pytest.raises(ValueError, match="mesh_size_mm"):
        geometry.build_mesh(tmp_path / "missing.step", tmp_path / "missing2.step", mesh_size_mm=size)


def test_missing_input_is_readable(tmp_path):
    with pytest.raises(ValueError, match="does not exist"):
        geometry.build_mesh(tmp_path / "missing.step", tmp_path / "missing2.step")


def test_disconnected_explicit_seal_is_rejected(tmp_path):
    cavity, capsule = _assembly(tmp_path, opening=True)
    with pytest.raises(ValueError, match="seal does not connect"):
        geometry.build_mesh(cavity, capsule, seal={"center_mm": [100, 0, 0], "radius_mm": 1, "height_mm": 1})


def test_missing_solid_is_rejected(tmp_path):
    cavity, capsule = _assembly(tmp_path)
    gmsh.initialize()
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.model.occ.addRectangle(0, 0, 0, 10, 10)
        gmsh.model.occ.synchronize()
        gmsh.write(str(cavity))
    finally:
        gmsh.finalize()
    with pytest.raises(ValueError, match="exactly one connected solid"):
        geometry.build_mesh(cavity, capsule)


def test_multiple_capsule_solids_are_rejected(tmp_path):
    cavity, capsule = _assembly(tmp_path)
    gmsh.initialize()
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.model.occ.addBox(10, 10, 10, 1, 1, 1)
        gmsh.model.occ.addBox(20, 20, 20, 1, 1, 1)
        gmsh.model.occ.synchronize()
        gmsh.write(str(capsule))
    finally:
        gmsh.finalize()
    with pytest.raises(ValueError, match="exactly one connected solid"):
        geometry.build_mesh(cavity, capsule)


def test_mesh_metadata_quantifies_volume_discretization(tmp_path):
    cavity, capsule = _assembly(tmp_path)
    mesh = geometry.build_mesh(cavity, capsule, mesh_size_mm=3)
    assert mesh.metadata["relative_volume_error"] == pytest.approx({"powder": 0, "capsule": 0}, abs=1e-12)
    np.testing.assert_allclose(mesh.metadata["bounds_mm"], [[-6, -6, -6], [6, 6, 6]])


@pytest.mark.parametrize("filename", ["compensated-cavity.step", "synthetic-capsule.step", "synthetic-capsule.provenance.json"])
def test_example_never_overwrites_existing_artifacts(tmp_path, filename):
    cavity, _ = _assembly(tmp_path)
    output = tmp_path / "example"
    output.mkdir()
    protected = output / filename
    protected.write_text("Existing user content")
    with pytest.raises(ValueError, match="already exists"):
        geometry.generate_example(cavity, output)
    assert protected.read_text() == "Existing user content"
    assert len(list(output.iterdir())) == 1


def test_synthetic_provenance_digest_must_match_capsule(tmp_path):
    cavity, _ = _assembly(tmp_path)
    paths = geometry.generate_example(cavity, tmp_path / "example")
    provenance = json.loads(paths["provenance"].read_text())
    provenance["capsule"]["sha256"] = "0" * 64
    paths["provenance"].write_text(json.dumps(provenance))
    with pytest.raises(ValueError, match="provenance hash does not match"):
        geometry.build_mesh(paths["cavity_step"], paths["capsule_step"], mesh_size_mm=3)
