"""Local command line for reproducible simulation experiments."""

import argparse
import csv
import hashlib
import json
import re
import sys
import time
import uuid
from pathlib import Path
import numpy as np

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

    verifying = args.command == "verify"
    job_id = (args.job_id or uuid.uuid4().hex) if verifying else None
    if verifying and not re.fullmatch(r"[a-f0-9]{32}", job_id):
        raise ValueError("job-id must be 32 lowercase hexadecimal characters")
    if args.output is None:
        args.output = Path("simulation-runs") / job_id
    output = args.output.resolve()
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError(f"Output directory is not empty: {output}; choose a new run directory")
    output.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    stage = "configuration"
    try:
        config = load_config(args.config)
        if verifying and (config.seal is not None or config.tolerances.angular_face_ids):
            raise ValueError("verify uses automatic vent sealing and target CAD face classification; "
                             "seal must be null and tolerances.angular_face_ids must be empty (legacy run options)")
        payload = config.model_dump(mode="json")
        _json(output / "config.resolved.json", payload)
        stage = "geometry"
        print("Preparing conformal capsule/powder mesh...", flush=True)
        target = None
        derived = None
        if args.command == "verify":
            from .assessment import assess_capsule_contact
            from .cad_workflow import derive_powder_domain, mesh_step_surface
            from .inputs import file_info, load_provenance
            inputs = {"cavity": file_info(args.cavity), "capsule": file_info(args.capsule)}
            provenance = load_provenance(args.capsule, inputs["capsule"]["sha256"])
            derived = derive_powder_domain(args.capsule, output / f"derived-cavity-{job_id}.step",
                                           output / f"sealed-capsule-{job_id}.step")
            target = mesh_step_surface(args.cavity, config.solver.mesh_size_mm)
            powder_path, capsule_path = derived["powder_step"], derived["sealed_capsule_step"]
            seal = None
        else:
            powder_path, capsule_path = args.cavity, args.capsule
            seal = config.seal.model_dump() if config.seal else None
        mesh = build_mesh(powder_path, capsule_path, config.solver.mesh_size_mm,
                          seal)
        if verifying:
            mesh.metadata["inputs"] = inputs
            mesh.metadata["input_roles"] = {"cavity": "finished_part_target", "capsule": "original_capsule_material"}
            mesh.metadata["preparation"] = {key: str(value) if isinstance(value, Path) else value
                                            for key, value in derived.items()}
            if provenance is not None:
                mesh.metadata.update(provenance=provenance, synthetic_example=bool(provenance.get("synthetic_example")))
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

        stage = "solver"
        result = simulate(mesh, config, progress)
        _export_result(output, mesh, result)
        _json(output / "solver.json", {"metadata": result.metadata, "warnings": result.warnings,
                                      "history": result.history})
        stage = "comparison"
        tolerance = config.tolerances
        if args.command == "verify":
            from .cad_workflow import export_faceted_step
            from .comparison import compare_target_surfaces
            from .report import write_verification_report
            predicted_step = output / f"cavity+{job_id}.step"
            predicted_nodes, inverse = np.unique(mesh.powder_triangles, return_inverse=True)
            export_faceted_step((mesh.points + result.displacement)[predicted_nodes], inverse.reshape(-1, 3), predicted_step)
            comparison = compare_target_surfaces(target[0], target[1],
                (mesh.points + result.displacement)[predicted_nodes], inverse.reshape(-1, 3), target[2],
                sample_spacing_mm=tolerance.sample_spacing_mm, max_samples=tolerance.max_samples)
            contact = assess_capsule_contact(mesh, result, tolerance)
            _json(output / "comparison.json", comparison)
            _json(output / "contact-assessment.json", contact)
            _json(output / "upstream-advice.json", {"job_id": job_id, "status": contact["status"],
                  "recommendations": [], "blocked_reason": contact["reason"],
                  "required_model": "Calibrated finite-strain powder/capsule model with separable contact"})
            stage = "report"
            write_verification_report(output / "report.html", comparison, payload,
                {"job_id": job_id, "target": str(args.cavity), "predicted_step": predicted_step.name},
                mesh=mesh, result=result, target=target, contact_assessment=contact)
            summary = {"execution_status": "completed", "validation_status": contact["status"],
                       "job_id": job_id, "calibrated": False, "model_validity": result.metadata["model_validity"],
                       "final_state": result.metadata["final_state"], "final_metrics": result.history[-1],
                       "engineering_acceptance": "not_assessed", "comparison_basis": "predicted cavity+jobid.step versus target cavity.step",
                       "predicted_step": predicted_step.name, "upstream_regeneration_advice": [],
                       "contact_assessment": contact, "target_diff_status": comparison["status"],
                       "volume_shrinkage_fraction": 1 - result.history[-1]["powder_volume_mm3"] / result.history[0]["powder_volume_mm3"],
                       "config_sha256": hashlib.sha256((output / "config.resolved.json").read_bytes()).hexdigest(),
                       "artifacts": {"report": "report.html", "predicted_step": predicted_step.name,
                           "comparison": "comparison.json", "contact_assessment": "contact-assessment.json",
                           "advice": "upstream-advice.json", "history": "history.csv"},
                       "geometry": mesh.metadata, "elapsed_s": round(time.monotonic()-started, 2), "warnings": result.warnings + comparison["warnings"]}
            _json(output / "result.json", summary)
            print(f"Report: {output / 'report.html'}")
            print(f"Predicted STEP: {predicted_step}")
            print(f"Inner-wall gap validation: {contact['status']}; target DIFF: {comparison['status']}")
            return 0
        comparison = compare_surfaces(mesh.points, mesh.points + result.displacement,
            mesh.powder_triangles, mesh.powder_face_ids, flat_mm=tolerance.flat_mm,
            angular_mm=tolerance.angular_mm, angular_face_ids=tolerance.angular_face_ids,
            sample_spacing_mm=tolerance.sample_spacing_mm, max_samples=tolerance.max_samples)
        _json(output / "comparison.json", comparison)
        stage = "report"
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
        from .failure_report import write_failure
        code = getattr(exc, "code", {"configuration": "invalid_configuration",
                                    "geometry": "invalid_geometry"}.get(stage, "simulation_failed"))
        write_failure(output, error=str(exc), error_code=code,
                      details=getattr(exc, "details", {}), stage=stage,
                      elapsed_s=time.monotonic() - started)
        raise


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="HIPForm: configurable uncalibrated HIP approximation (mm/MPa/s)")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="Start the local HTTP API and Swagger UI")
    serve.add_argument("--host", default="127.0.0.1", choices=["127.0.0.1", "localhost"])
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--data-dir", type=Path, default=Path("simulation-runs/api"))
    serve.add_argument("--input-dir", type=Path, default=Path("."), help="Directory containing cavity.step and capsule.step")
    serve.add_argument("--job-timeout", type=float, default=3600,
                       help="Maximum seconds per solver process (default: 3600)")
    publish = commands.add_parser("publish", help="Export a completed run for GitHub Pages")
    publish.add_argument("--run", type=Path, required=True)
    publish.add_argument("--output", type=Path, required=True)
    init = commands.add_parser("init", help="Write editable ideal defaults")
    init.add_argument("--output", type=Path, required=True)
    example = commands.add_parser("example", help="Create an explicitly synthetic sealed box capsule")
    example.add_argument("--cavity", type=Path, required=True)
    example.add_argument("--output", type=Path, required=True)
    example.add_argument("--wall-mm", type=float, default=3.0)
    for name in ("mesh", "run", "verify"):
        command = commands.add_parser(name)
        command.add_argument("--cavity", type=Path, default=Path("cavity.step"), required=name != "verify")
        command.add_argument("--capsule", type=Path, default=Path("capsule.step"), required=name != "verify")
        command.add_argument("--config", type=Path)
        command.add_argument("--output", type=Path, required=name != "verify")
        if name == "verify":
            command.add_argument("--job-id", help="Stable identifier used in cavity+<jobid>.step")
    args = parser.parse_args(argv)
    try:
        if args.command == "serve":
            from .api import create_app
            import uvicorn
            if not 1 <= args.port <= 65535 or not 0 < args.job_timeout < float("inf"):
                raise ValueError("port must be 1-65535 and job-timeout must be positive and finite")
            uvicorn.run(create_app(args.data_dir, job_timeout_s=args.job_timeout, source_dir=args.input_dir), host=args.host, port=args.port)
            return 0
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
