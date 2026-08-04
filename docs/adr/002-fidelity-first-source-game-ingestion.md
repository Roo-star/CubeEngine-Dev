# ADR 002: Fidelity-first source game ingestion

- Status: Accepted
- Date: 2026-08-04

## Context

The first Function 1 implementation parsed isolated JSON/Python rule snippets and immediately offered generic topology, anchor, flow and randomness overrides. That demonstrated a Rule Schema but did not demonstrate that CubeEngine understood a complete existing game. Opening the result in a generic STAL cube discarded source rendering, timing, assets and mechanics.

## Decision

1. The primary input is a complete source-game project or runnable entrypoint.
2. The unchanged original runtime is retained as the fidelity reference.
3. Rule Schema becomes an intermediate representation inside a Source Game Package.
4. Original runtime, rule-understanding coverage and 3D transform readiness are separate states.
5. Workbench only exposes parameters proven to exist in the source and labels their edit mode.
6. X/Y are preserved by default. Z and every affected mechanic require explicit lift records.
7. A generic STAL cube must not be shown when source-specific lifts or renderer adapters are incomplete.
8. Unsupported or unresolved semantics are routed to Function 2; no filename/game-name guessing is permitted.
9. Source modifications are applied only to reversible derived copies.

## Consequences

- Function 1 can honestly validate complete playable games.
- Framework/language importer plugins become required for broad coverage.
- Full arbitrary conversion is not claimed.
- 3D output arrives later than a generic topology preview, but it is meaningfully tied to the source game.
- A Qt preview shell or framebuffer protocol is preferred if stable embedding of foreign runtime windows becomes mandatory.
