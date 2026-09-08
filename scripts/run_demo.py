"""Run an independent synthetic L-shaped cavity; no private CAD input is used."""

import argparse
from pathlib import Path

import gmsh

from hipform.cli import main as run_cli
from hipform.geometry import generate_example


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("simulation-runs/public-demo"))
    parser.add_argument("--config", type=Path, default=Path("config/hip-ideal.yaml"))
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("Demo output already exists; choose a new --output directory")
    args.output.mkdir(parents=True)
    cavity = args.output / "synthetic-l-cavity.step"
    gmsh.initialize(readConfigFiles=False)
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.model.add("HIPForm synthetic L-shaped cavity")
        block = gmsh.model.occ.addBox(0, 0, 0, 60, 40, 24)
        notch = gmsh.model.occ.addBox(30, 20, -1, 31, 21, 26)
        gmsh.model.occ.cut([(3, block)], [(3, notch)])
        gmsh.model.occ.synchronize()
        gmsh.write(str(cavity))
    finally:
        gmsh.finalize()
    inputs = generate_example(cavity, args.output / "inputs", wall_mm=3)
    code = run_cli(["run", "--cavity", str(inputs["cavity_step"]), "--capsule", str(inputs["capsule_step"]),
                    "--config", str(args.config), "--output", str(args.output / "run")])
    raise SystemExit(code)


if __name__ == "__main__":
    main()
