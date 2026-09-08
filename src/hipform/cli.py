"""Local command line for reproducible simulation experiments."""

import argparse
import csv
import hashlib
import json
import sys
import time
from pathlib import Path

from .config import load_config, write_default_config


def _json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def _save_mesh(output, mesh):
    import meshio
    import numpy as np

    np.savez_compressed(output / "mesh.npz", points=mesh.points, tetrahedra=mesh.tetrahedra,
        material=mesh.material, pressure_triangles=mesh.pressure_triangles,
        powder_triangles=mesh.powder_triangles, powder_face_ids=mesh.powder_face_ids)
    _json(output / "mesh-info.json", mesh.metadata)
    meshio.write(output / "initial-mesh.vtu", meshio.Mesh(mesh.points,
        [("tetra", mesh.tetrahedra)], cell_data={"material": [mesh.material]}))


def _export_result(output, mesh, result):
    import meshio
    import numpy as np
    import trimesh

    predicted = mesh.points + result.displacement
    meshio.write(output / "predicted-assembly.vtu", meshio.Mesh(predicted,
        [("tetra", mesh.tetrahedra)], point_data={"displacement_mm": result.displacement},
        cell_data={"material": [mesh.material], "relative_density": [result.density]}))
    for name, points in (("reference-powder", mesh.points), ("predicted-powder", predicted)):
        surface = trimesh.Trimesh(points, mesh.powder_triangles, process=False)
        surface.remove_unreferenced_vertices()
        surface.export(output / f"{name}.stl")
    np.savez_compressed(output / "solution.npz", displacement_mm=result.displacement,
                        relative_density=result.density)
    with (output / "history.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(result.history[0]))
        writer.writeheader()
        writer.writerows(result.history)


def _run(args):
    from .geometry import build_mesh

    config = load_config(args.config)
    output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"Output directory is not empty: {output}; choose a new run directory")
    output.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    payload = config.model_dump(mode="json")
    _json(output / "config.resolved.json", payload)
    try:
        print("Preparing conformal capsule/powder mesh...", flush=True)
        mesh = build_mesh(args.cavity, args.capsule, config.solver.mesh_size_mm,
                          config.seal.model_dump() if config.seal else None)
        _save_mesh(output, mesh)
        print(f"Mesh: {len(mesh.points)} nodes, {len(mesh.tetrahedra)} tetrahedra", flush=True)
        if args.command == "mesh":
            _json(output / "result.json", {"execution_status": "mesh_prepared", "engineering_acceptance": "not_assessed"})
            return 0
        from .comparison import compare_surfaces
        from .report import write_report
        from .solver import simulate

        last_update = [0.0]

        def progress(i, total, row):
            now = time.monotonic()
            if i in (0, total-1) or now-last_update[0] >= 10:
                print(f"Step {i+1}/{total}: {row['time_s']/60:.1f} min, {row['temperature_c']:.0f} C, "
                      f"{row['pressure_mpa']:.1f} MPa, mean relative density {row['mean_relative_density']:.4f}", flush=True)
                last_update[0] = now

        result = simulate(mesh, config, progress)
        _export_result(output, mesh, result)
        tolerance = config.tolerances
        comparison = compare_surfaces(mesh.points, mesh.points + result.displacement,
            mesh.powder_triangles, mesh.powder_face_ids, flat_mm=tolerance.flat_mm,
            angular_mm=tolerance.angular_mm, angular_face_ids=tolerance.angular_face_ids,
            sample_spacing_mm=tolerance.sample_spacing_mm, max_samples=tolerance.max_samples)
        _json(output / "comparison.json", comparison)
        _json(output / "solver.json", {"metadata": result.metadata, "warnings": result.warnings,
                                      "history": result.history})
        write_report(output / "report.html", mesh, result, comparison, payload)
        summary = {"execution_status": "completed", "calibrated": False,
            "engineering_acceptance": "not_assessed", "model_validity": result.metadata["model_validity"],
            "sampled_tolerance_status": comparison["status"],
            "comparison_basis": "initial compensated cavity, not uncompensated final-part target",
            "geometry": mesh.metadata, "final_state": result.metadata["final_state"],
            "elapsed_s": round(time.monotonic()-started, 2),
            "config_sha256": hashlib.sha256((output / "config.resolved.json").read_bytes()).hexdigest(),
            "warnings": result.warnings + comparison["warnings"],
            "artifacts": {"report": "report.html", "predicted_surface": "predicted-powder.stl",
                "predicted_volume": "predicted-assembly.vtu", "history": "history.csv",
                "comparison": "comparison.json", "mesh": "mesh-info.json"}}
        _json(output / "result.json", summary)
        print(f"Report: {output / 'report.html'}")
        print(f"Sampled tolerance: {comparison['status']}; engineering acceptance: not assessed")
        return 0
    except Exception as exc:
        _json(output / "result.json", {"execution_status": "failed", "engineering_acceptance": "not_assessed",
            "error": str(exc), "elapsed_s": round(time.monotonic()-started, 2)})
        raise


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="HIPForm: configurable uncalibrated HIP approximation (mm/MPa/s)")
    commands = parser.add_subparsers(dest="command", required=True)
    publish = commands.add_parser("publish", help="Export a completed run for GitHub Pages")
    publish.add_argument("--run", type=Path, required=True)
    publish.add_argument("--output", type=Path, required=True)
    init = commands.add_parser("init", help="Write editable ideal defaults")
    init.add_argument("--output", type=Path, required=True)
    example = commands.add_parser("example", help="Create an explicitly synthetic sealed box capsule")
    example.add_argument("--cavity", type=Path, required=True)
    example.add_argument("--output", type=Path, required=True)
    example.add_argument("--wall-mm", type=float, default=3.0)
    for name in ("mesh", "run"):
        command = commands.add_parser(name)
        command.add_argument("--cavity", type=Path, required=True)
        command.add_argument("--capsule", type=Path, required=True)
        command.add_argument("--config", type=Path)
        command.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "publish":
            from .publishing import publish_run
            print(f"Pages report: {publish_run(args.run, args.output).resolve()}")
            return 0
        if args.command == "init":
            write_default_config(args.output)
            print(f"Configuration: {args.output.resolve()}")
            return 0
        if args.command == "example":
            from .geometry import generate_example
            paths = generate_example(args.cavity, args.output, args.wall_mm)
            print(json.dumps({k: str(v) for k, v in paths.items()}, indent=2))
            return 0
        return _run(args)
    except (ValueError, OSError, RuntimeError, ImportError) as exc:
        print(f"HIP simulation error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
