# HIPForm Public Repository and Pages

**Goal:** Publish the standalone simulation module in zero6453/HIPForm and serve its report through GitHub Pages.

**Architecture:** A fresh repository contains the simulation code, configurable inputs, numerical tests and a synthetic L-shaped demonstration. A Python exporter rebuilds a static report from completed numerical output, retains validity findings, removes absolute provenance paths, and copies a fixed set of download files. GitHub Actions tests the code before publishing either the generated demo or an explicitly committed report in reports/latest.

**Tech stack:** Python 3.12, Gmsh, scikit-fem, SciPy, Trimesh, Plotly, uv, GitHub Actions and Pages.

- [x] Extract standalone module and existing numerical tests without upstream private CAD or git history.
- [x] Test completed-run export, invalid-run rejection, provenance redaction and output preservation.
- [x] Implement report export, independent synthetic demo, and Pages deployment workflow.
- [x] Run numerical tests and build; execute demo and inspect report output (58 tests passed; package built; 11 publication files and 9 download links verified).
- [ ] Review publication files; create public repository and push main.
- [ ] Enable Pages, verify deployment and public report links.
