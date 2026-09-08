"""Render saved numerical arrays directly with Matplotlib, without a browser."""

import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import Normalize
from matplotlib.cm import ScalarMappable
from mpl_toolkits.mplot3d.art3d import Poly3DCollection
import numpy as np


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args()
    root = args.run_dir
    with np.load(root / "mesh.npz") as mesh:
        points, triangles = mesh["points"], mesh["powder_triangles"]
    with np.load(root / "solution.npz") as solution:
        predicted = points + solution["displacement_mm"]
    comparison = json.loads((root / "comparison.json").read_text())
    solver = json.loads((root / "solver.json").read_text())
    geometry = json.loads((root / "mesh-info.json").read_text())
    values = np.asarray(comparison["fields"]["predicted_triangle_max_mm"])
    maximum = comparison["global"]["max_mm"]
    norm = Normalize(0, max(maximum, 1e-9))
    fig = plt.figure(figsize=(13, 7.4), layout="constrained", facecolor="white")
    bounds = points[np.unique(triangles)]
    center = (bounds.min(axis=0)+bounds.max(axis=0))/2
    ranges = bounds.max(axis=0)-bounds.min(axis=0)
    for index, (coordinates, title) in enumerate(((points, "Reference compensated cavity"),
                                                 (predicted, "Predicted powder surface"))):
        ax = fig.add_subplot(1, 2, index+1, projection="3d")
        collection = Poly3DCollection(coordinates[triangles], linewidths=0.05,
            edgecolors="#536170" if index == 0 else "none",
            facecolors="#b7c6d2" if index == 0 else plt.colormaps["YlOrRd"](norm(values)))
        ax.add_collection3d(collection)
        ax.set(xlim=(center[0]-ranges[0]*.6, center[0]+ranges[0]*.6),
               ylim=(center[1]-ranges[1]*.6, center[1]+ranges[1]*.6),
               zlim=(center[2]-ranges[2]*.55, center[2]+ranges[2]*.55),
               xlabel="X (mm)", ylabel="Y (mm)", zlabel="Z (mm)", title=title)
        ax.set_box_aspect(ranges.copy())
        ax.view_init(elev=18, azim=42)
        if index:
            fig.colorbar(ScalarMappable(norm, plt.colormaps["YlOrRd"]), ax=ax,
                         shrink=.65, pad=.10, label="Sampled distance to reference (mm)")
    synthetic = "SYNTHETIC CAPSULE | " if geometry.get("synthetic_example") else ""
    state = solver["metadata"]["model_validity"]
    fig.suptitle(f"{synthetic}UNCALIBRATED HIP PROTOTYPE\n"
                 f"Sampled max deviation: {maximum:.3f} mm | Validity: {state}\n"
                 "Capsule attached; engineering acceptance not assessed", fontsize=13)
    fig.savefig(root / "preview.png", dpi=150)
    print(root / "preview.png")


if __name__ == "__main__":
    main()
