"""Capsule extraction preserves manufactured walls in source coordinates."""

import gmsh
import numpy as np
import pytest
import trimesh

from hipform.cad_workflow import derive_powder_domain, export_faceted_step
from hipform.geometry import _model, _import_solid, build_mesh


def make_capsule(path, *, vent=False, sealed=False, transform=False):
    with _model("analytic capsule"):
        outer = gmsh.model.occ.addBox(-8, -9, -10, 16, 18, 20)
        inner = gmsh.model.occ.addBox(-5, -6, -7, 10, 12, 14)
        wall, _ = gmsh.model.occ.cut([(3, outer)], [(3, inner)])
        if vent:
            tube = gmsh.model.occ.addCylinder(0, 0, 9, 0, 0, 7, 2)
            wall, _ = gmsh.model.occ.fuse(wall, [(3, tube)])
            bore = gmsh.model.occ.addCylinder(0, 0, 7, 0, 0, 8 if sealed else 9, 1)
            wall, _ = gmsh.model.occ.cut(wall, [(3, bore)])
        if transform:
            gmsh.model.occ.rotate(wall, 0, 0, 0, 1, 2, 3, .73)
            gmsh.model.occ.translate(wall, 80, -40, 25)
        gmsh.model.occ.synchronize()
        mass = gmsh.model.occ.getMass(*wall[0])
        gmsh.write(str(path))
    return mass


def measure(path):
    with _model("read extracted solid"):
        solid, volume = _import_solid(path, "Extracted")
        gmsh.model.occ.synchronize()
        types = [gmsh.model.getType(2, tag) for dim, tag in gmsh.model.getEntities(2)]
        return volume, types


@pytest.mark.parametrize("transform", [False, True])
def test_closed_capsule_preserves_wall_and_extracts_inner_void(tmp_path, transform):
    source = tmp_path / "capsule.step"
    original_volume = make_capsule(source, transform=transform)
    original_bytes = source.read_bytes()
    powder, sealed = tmp_path / "powder.step", tmp_path / "sealed.step"
    result = derive_powder_domain(source, powder, sealed)
    assert measure(powder)[0] == pytest.approx(1680)
    assert measure(sealed)[0] == pytest.approx(original_volume)
    assert result["seal"] is None
    center = np.asarray(result["body_bounds_mm"]).reshape(2, 3).mean(axis=0)
    assert center == pytest.approx([80, -40, 25] if transform else [0, 0, 0], abs=1e-7)
    assert source.read_bytes() == original_bytes
    mesh = build_mesh(powder, sealed, mesh_size_mm=4)
    assert mesh.metadata["sealed_vent_voids"] == []


@pytest.mark.parametrize("transform", [False, True])
def test_open_straight_vent_is_capped_at_mouth_without_changing_wall(tmp_path, transform):
    source = tmp_path / "capsule.step"
    original_volume = make_capsule(source, vent=True, transform=transform)
    original_bytes = source.read_bytes()
    powder, sealed = tmp_path / "powder.step", tmp_path / "sealed.step"
    result = derive_powder_domain(source, powder, sealed)
    assert measure(powder)[0] == pytest.approx(1680)
    sealed_volume, face_types = measure(sealed)
    assert "Cylinder" in face_types
    assert sealed_volume == pytest.approx(original_volume + np.pi * 2**2 * result["seal"]["height_mm"])
    assert source.read_bytes() == original_bytes
    mesh = build_mesh(powder, sealed, mesh_size_mm=3)
    assert mesh.metadata["sealed_vent_voids"][0]["radius_mm"] == pytest.approx(1)
    assert mesh.metadata["sealed_vent_voids"][0]["height_mm"] == pytest.approx(9)
    assert np.asarray(result["seal"]["center_mm"]) - 9 * np.asarray(result["seal"]["axis"]) == pytest.approx(
        mesh.metadata["sealed_vent_voids"][0]["base_center_mm"], abs=1e-7)


@pytest.mark.parametrize("transform", [False, True])
def test_presealed_vent_residual_is_excluded_without_adding_material(tmp_path, transform):
    source = tmp_path / "capsule.step"
    original_volume = make_capsule(source, vent=True, sealed=True, transform=transform)
    powder, sealed = tmp_path / "powder.step", tmp_path / "sealed.step"
    result = derive_powder_domain(source, powder, sealed)
    assert result["seal"] is None
    assert measure(powder)[0] == pytest.approx(1680)
    assert measure(sealed)[0] == pytest.approx(original_volume)
    mesh = build_mesh(powder, sealed, mesh_size_mm=3)
    assert mesh.metadata["sealed_vent_voids"][0]["height_mm"] == pytest.approx(8)


def test_rectangular_opening_is_rejected_without_fabricating_a_capsule(tmp_path):
    source = tmp_path / "open.step"
    with _model("unsupported opening"):
        outer = gmsh.model.occ.addBox(-8, -9, -10, 16, 18, 20)
        inner = gmsh.model.occ.addBox(-5, -6, -7, 10, 12, 18)
        gmsh.model.occ.cut([(3, outer)], [(3, inner)])
        gmsh.model.occ.synchronize()
        gmsh.write(str(source))
    powder, sealed = tmp_path / "powder.step", tmp_path / "sealed.step"
    with pytest.raises(ValueError, match="open or ambiguous"):
        derive_powder_domain(source, powder, sealed)
    assert not powder.exists()
    assert not sealed.exists()


def test_faceted_export_preserves_closed_surface_volume_and_all_faces(tmp_path):
    surface = trimesh.creation.icosphere(subdivisions=5, radius=4)
    output = tmp_path / "predicted.step"
    export_faceted_step(surface.vertices, surface.faces, output)
    volume, faces = measure(output)
    assert len(faces) == len(surface.faces)
    assert volume == pytest.approx(surface.volume, rel=1e-9)


@pytest.mark.parametrize("invalid", ["open", "fractional", "negative", "duplicate", "nonfinite"])
def test_invalid_faceted_surface_is_rejected_before_export(tmp_path, invalid):
    surface = trimesh.creation.box(extents=[3, 4, 5])
    points, faces = surface.vertices.copy(), surface.faces.copy()
    if invalid == "open":
        faces = faces[:-1]
    elif invalid == "fractional":
        faces = faces.astype(float) + .1
    elif invalid == "negative":
        faces[0, 0] = -1
    elif invalid == "duplicate":
        faces[0] = faces[1]
    else:
        points[0, 0] = np.nan
    output = tmp_path / "invalid.step"
    with pytest.raises(ValueError):
        export_faceted_step(points, faces, output)
    assert not output.exists()
