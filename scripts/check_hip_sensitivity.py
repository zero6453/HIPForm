"""Run three independent resolutions; a sensitivity study, not convergence proof."""

import argparse
import json
from pathlib import Path

import yaml

from hipform.cli import main as run_cli
from hipform.config import load_config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cavity", type=Path, required=True)
    parser.add_argument("--capsule", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Use a new output directory for the sensitivity study")
    args.output.mkdir(parents=True)
    cfg = load_config(args.config)
    rows = []
    for name, mesh_factor, step_factor in (("baseline", 1, 1), ("coarse-mesh", 4/3, 1), ("coarse-time", 1, 2)):
        config = cfg.model_dump(mode="json")
        config["solver"]["mesh_size_mm"] *= mesh_factor
        config["solver"]["time_step_s"] *= step_factor
        path = args.output / f"{name}.yaml"
        path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
        output = args.output / name
        code = run_cli(["run", "--cavity", str(args.cavity), "--capsule", str(args.capsule),
                        "--config", str(path), "--output", str(output)])
        if code:
            raise RuntimeError(f"Study case {name} failed; inspect {output}")
        solver = json.loads((output / "solver.json").read_text())
        comparison = json.loads((output / "comparison.json").read_text())
        mesh = json.loads((output / "mesh-info.json").read_text())
        final = solver["history"][-1]
        row = {"case": name, "mesh_size_mm": config["solver"]["mesh_size_mm"],
               "time_step_s": config["solver"]["time_step_s"],
               "tetrahedra": solver["metadata"]["tetrahedra"],
               "max_sampled_deviation_mm": comparison["global"]["max_mm"],
               "mean_relative_density": final["mean_relative_density"],
               "powder_volume_mm3": final["powder_volume_mm3"],
               "powder_mesh_volume_relative_error": mesh["relative_volume_error"]["powder"],
               "model_validity": solver["metadata"]["model_validity"]}
        rows.append(row)
        print(json.dumps(row), flush=True)
    baseline = rows[0]
    for row in rows[1:]:
        row["max_deviation_difference_mm"] = row["max_sampled_deviation_mm"] - baseline["max_sampled_deviation_mm"]
        row["mean_density_difference"] = row["mean_relative_density"] - baseline["mean_relative_density"]
        row["final_volume_relative_difference"] = row["powder_volume_mm3"] / baseline["powder_volume_mm3"] - 1
    summary = {"kind": "separate_mesh_and_time_sensitivity", "convergence_proven": False,
               "engineering_acceptance": "not_assessed", "cases": rows}
    (args.output / "sensitivity.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == "__main__":
    main()
