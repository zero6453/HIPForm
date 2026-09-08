# HIPForm Code Quality Review

Scope: all maintained Python modules, scripts, tests, and the Pages workflow at
baseline `47a78da`. Preserve the simulation model, STEP semantics, HTTP contracts,
report outputs, and the user's existing files.

Workflow: coding-standards -> codebase-design -> tdd -> code-simplification ->
code-review-and-quality, with karpathy-guidelines throughout.

## Verification Interfaces

Use the existing HTTP API, CLI, geometry/solver functions, surface comparison,
and publication entry point. These are the behavior contracts already requested
by the user. Test externally observable behavior, not helper call order.

## Tasks

- [x] Establish the baseline: clean worktree, inventory all maintained code,
  inspect responsibilities, run the existing suite (98 passed).
- [x] Review all modules; reproduce concrete defects through public interfaces.
- [x] Fix confirmed defects with failing tests before implementation.
- [x] Simplify input persistence and artifact selection while preserving behavior.
- [x] Independently review changes, run appropriate tests/build checks, document
  results and model limitations, and verify the local service.

## Design

HTTP routes translate requests and responses. File persistence belongs to the
storage layer. STEP upload and local snapshot paths should share bounded file
copy and validation. Artifact availability should have one status-aware policy
and should not reread job metadata for every artifact.

Do not add a new framework, generic repository hierarchy, or speculative feature.
Only restructure geometry or numerical code when review demonstrates a concrete
benefit; the code's scientific invariants must remain explicit.

## Results

- 108 tests passed; Ruff E4/E7/E9/F checks, build, dependency check and diff check passed.
- Final independent review found no blocking regressions.
- Original STEP HTTP job `b60f66423d6f46d3b812c383e108cfdf` returned the expected
  `open_capsule` error and a readable report.
- Sealed synthetic demo HTTP job `ad79f451c9364d0ba39bc4ad11dbaaba` completed;
  report and STL downloads succeeded, and synthetic provenance was retained.
- Service is running at `http://127.0.0.1:8000/docs`.
- Full findings and limitations: `docs/code-quality-review.zh-CN.md`.
