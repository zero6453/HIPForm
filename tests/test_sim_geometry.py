"""Geometric checks use small analytic solids, independent of the CAD example."""

import json
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
gmsh = pytest.importorskip("gmsh")

from hipform import geometry  # noqa: E402 - optional CAD dependencies must be checked first


def _assembly(tmp_path: Path, *, opening=False, overlap=False, top_gap=0, vent_height=0, presealed=False):
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
            inner = gmsh.model.occ.addBox(-5, -5, -5, 10, 10, 10 + top_gap)
            shell, _ = gmsh.model.occ.cut([(3, outer)], [(3, inner)])
            if vent_height:
                tube = gmsh.model.occ.addCylinder(0, 0, 5.5, 0, 0, vent_height + 0.5, 2)
                shell, _ = gmsh.model.occ.fuse(shell, [(3, tube)])
            if opening:
                bore_height = 1 + vent_height - 0.5 if presealed else 2 + vent_height
                bore = gmsh.model.occ.addCylinder(0, 0, 5, 0, 0, bore_height, 1)
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


@pytest.mark.parametrize("provenance", [[], {"capsule": []}])
def test_cli_rejects_malformed_provenance_with_failure_report(tmp_path, provenance):
    from hipform.cli import main

    cavity, capsule = _assembly(tmp_path)
    capsule.with_suffix(".provenance.json").write_text(json.dumps(provenance))
    output = tmp_path / "run"
    assert main(["mesh", "--cavity", str(cavity), "--capsule", str(capsule),
                 "--output", str(output)]) == 2
    result = json.loads((output / "result.json").read_text())
    assert result["error_code"] == "invalid_geometry"
    assert "provenance" in result["error"].lower()
    assert (output / "report.html").is_file()


def test_separated_powder_is_rejected_before_meshing(tmp_path, monkeypatch):
    cavity, capsule = _assembly(tmp_path)
    gmsh.initialize()
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.model.occ.addBox(-4, -4, -4, 8, 8, 8)
        gmsh.model.occ.synchronize()
        gmsh.write(str(cavity))
    finally:
        gmsh.finalize()

    def no_meshing(*args):
        pytest.fail("Disconnected solids should be rejected before mesh generation")

    monkeypatch.setattr(gmsh.model.mesh, "generate", no_meshing)
    with pytest.raises(ValueError, match="Assembly contains disconnected material bodies in the mesh") as error:
        geometry.build_mesh(cavity, capsule, mesh_size_mm=3)
    assert error.value.code == "material_gap"
    assert error.value.details["minimum_gap_mm"] == pytest.approx(1)


def test_open_capsule_exposes_powder_and_is_rejected(tmp_path):
    cavity, capsule = _assembly(tmp_path, opening=True)
    with pytest.raises(ValueError, match="(?i)(exposed|open|exterior)"):
        geometry.build_mesh(cavity, capsule, mesh_size_mm=2)


def test_open_vent_is_rejected_at_cad_stage_even_with_coarse_mesh(tmp_path, monkeypatch):
    from hipform.errors import GeometryError

    cavity, capsule = _assembly(tmp_path, opening=True, vent_height=5)

    def unexpected_mesh(*args, **kwargs):
        pytest.fail("An open CAD vent must be rejected before mesh generation")

    monkeypatch.setattr(gmsh.model.mesh, "generate", unexpected_mesh)
    with pytest.raises(GeometryError, match="Powder is exposed") as error:
        geometry.build_mesh(cavity, capsule, mesh_size_mm=40)
    assert error.value.code == "open_capsule"


def test_partial_powder_wall_gap_is_rejected_before_meshing(tmp_path, monkeypatch):
    from hipform.errors import GeometryError

    cavity, capsule = _assembly(tmp_path, top_gap=0.5)

    def unexpected_mesh(*args, **kwargs):
        pytest.fail("A partial material gap must be rejected before mesh generation")

    monkeypatch.setattr(gmsh.model.mesh, "generate", unexpected_mesh)
    with pytest.raises(GeometryError) as error:
        geometry.build_mesh(cavity, capsule, mesh_size_mm=2)
    assert error.value.code == "material_gap"
    assert str(error.value) == "Assembly contains disconnected material bodies in the mesh."


def test_presealed_vent_residual_space_is_allowed_without_an_added_seal(tmp_path):
    cavity, capsule = _assembly(tmp_path, opening=True, vent_height=3, presealed=True)
    mesh = geometry.build_mesh(cavity, capsule, mesh_size_mm=2)
    assert mesh.metadata["seal"] is None
    assert mesh.metadata["unloaded_boundary_components"] == 1
    vents = mesh.metadata["sealed_vent_voids"]
    assert len(vents) == 1
    assert vents[0]["radius_mm"] == pytest.approx(1)
    assert vents[0]["height_mm"] == pytest.approx(3.5)


def test_presealed_vent_can_be_rotated_with_the_input_assembly(tmp_path):
    cavity, capsule = _assembly(tmp_path, opening=True, vent_height=3, presealed=True)
    for path in (cavity, capsule):
        gmsh.initialize()
        try:
            gmsh.option.setNumber("General.Terminal", 0)
            solids = gmsh.model.occ.importShapes(str(path))
            gmsh.model.occ.rotate(solids, 0, 0, 0, 0, 1, 0, np.pi / 2)
            gmsh.model.occ.synchronize()
            gmsh.write(str(path))
        finally:
            gmsh.finalize()
    mesh = geometry.build_mesh(cavity, capsule, mesh_size_mm=2)
    assert mesh.metadata["sealed_vent_voids"][0]["axis"] == pytest.approx([1, 0, 0], abs=1e-10)


def test_annular_powder_clearance_is_not_a_sealed_vent(tmp_path):
    from hipform.errors import GeometryError

    cavity, capsule = tmp_path / "cavity.step", tmp_path / "capsule.step"
    gmsh.initialize()
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.model.add("powder")
        gmsh.model.occ.addCylinder(0, 0, -5, 0, 0, 10, 4)
        gmsh.model.occ.synchronize()
        gmsh.write(str(cavity))
        gmsh.model.add("capsule")
        outer = gmsh.model.occ.addCylinder(0, 0, -6, 0, 0, 12, 6)
        inner = gmsh.model.occ.addCylinder(0, 0, -5, 0, 0, 10, 5)
        gmsh.model.occ.cut([(3, outer)], [(3, inner)])
        gmsh.model.occ.synchronize()
        gmsh.write(str(capsule))
    finally:
        gmsh.finalize()
    with pytest.raises(GeometryError) as error:
        geometry.build_mesh(cavity, capsule, mesh_size_mm=2)
    assert error.value.code == "material_gap"
    assert error.value.details["gap_type"] == "partial_interface_gap"


def test_cylindrical_powder_end_clearance_is_not_a_vent(tmp_path):
    from hipform.errors import GeometryError

    cavity, capsule = tmp_path / "cavity.step", tmp_path / "capsule.step"
    gmsh.initialize()
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.model.add("powder")
        gmsh.model.occ.addCylinder(0, 0, -5, 0, 0, 10, 4)
        gmsh.model.occ.synchronize()
        gmsh.write(str(cavity))
        gmsh.model.add("capsule")
        outer = gmsh.model.occ.addCylinder(0, 0, -6, 0, 0, 12, 5)
        inner = gmsh.model.occ.addCylinder(0, 0, -5, 0, 0, 10.5, 4)
        gmsh.model.occ.cut([(3, outer)], [(3, inner)])
        gmsh.model.occ.synchronize()
        gmsh.write(str(capsule))
    finally:
        gmsh.finalize()
    with pytest.raises(GeometryError) as error:
        geometry.build_mesh(cavity, capsule, mesh_size_mm=2)
    assert error.value.code == "material_gap"


def test_circular_blind_wall_pocket_is_not_a_vent(tmp_path):
    from hipform.errors import GeometryError

    cavity, capsule = _assembly(tmp_path, opening=True, presealed=True)
    with pytest.raises(GeometryError) as error:
        geometry.build_mesh(cavity, capsule, mesh_size_mm=2)
    assert error.value.code == "material_gap"


def test_void_within_capsule_material_is_not_a_powder_gap(tmp_path):
    cavity, capsule = _assembly(tmp_path)
    gmsh.initialize()
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        solids = gmsh.model.occ.importShapes(str(capsule))
        void = gmsh.model.occ.addSphere(5.5, 0, 0, 0.2)
        gmsh.model.occ.cut(solids, [(3, void)])
        gmsh.model.occ.synchronize()
        gmsh.write(str(capsule))
    finally:
        gmsh.finalize()
    mesh = geometry.build_mesh(cavity, capsule, mesh_size_mm=1)
    assert mesh.metadata["unloaded_boundary_components"] == 1
    assert mesh.metadata["sealed_vent_voids"] == []


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
