# Capsule-derived finished-part verification

Confirmed contract: cavity.step is the finished target, capsule.step is capsule
material only. No assembly upload. Recognize one straight circular vent and seal
the simulation copy at its mouth, excluding the bore from the powder domain.
Compare the predicted cavity+<jobid>.step to the target in source coordinates.
Negative deviation is undersize; planar limits are [0, 10] mm and nonplanar
limits [0, 20] mm. Return machine-readable upstream advice on rejection.

Workflow: coding-standards, codebase-design, TDD, code-simplification,
code-review-and-quality; karpathy-guidelines throughout.

Public behavior under test:
- Configuration round trip of supplied strengths, density and 900 C / 120 MPa /
  10800 s hold. Strength metadata does not calibrate the existing surrogate.
- Capsule preparation yields an independent, positive, closed powder solid and
  a sealed capsule copy; unsupported or ambiguous openings fail explicitly.
- Target comparison accepts independent topology, automatically classifies CAD
  planes, detects undersize and over-limit sizes and returns localized advice.
- CLI verify and HTTP jobs export a reimportable faceted solid STEP, reports and
  validation status. Execution completion and dimensional acceptance differ.

Implementation remains in focused CAD/comparison modules and existing CLI,
report and job orchestration. Legacy run/mesh retain initial-powder semantics.
No new solver model or implicit best-fit alignment. Predicted STEP is a faceted
BREP of the FEM surface, not reconstruction of analytic design features.

Validation: red-green behavior slices, analytic solids and rotated vent cases,
real supplied STEP pair, API/report/artifact checks, full regression suite and
independent quality review. Small-strain and uncalibrated limitations must remain
visible even when the configured dimensional checks pass.
