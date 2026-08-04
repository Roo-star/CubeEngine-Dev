# ADR-001: Canonical Rule Schema and evidence-aware safe ingestion

Status: Accepted for SRTP Function 1

## Context

CubeEngine must read heterogeneous JSON and scripts, but arbitrary source code has no common field names and cannot be
semantically reconstructed by file-name heuristics. Executing uploaded scripts would also mix parsing with untrusted runtime behaviour.

## Decision

1. All SRTP inputs produce `cubeengine.srtp/rule-schema-v1`.
2. Source parsing records per-field provenance and confidence.
3. Python ingestion uses AST inspection only and never imports or executes the source.
4. Only a small declarative operation set is compiled to STAL.
5. Unproven semantics remain unresolved and are packaged for Function 2/LLM.
6. Game classification is multi-axis, not one exclusive genre enum.
7. Designer overrides are explicit provenance entries and never mutate the original source.

## Consequences

- Complete simple placement/selection games can be previewed immediately.
- Complex real-time, physics, hidden-information and state-machine games produce useful partial schemas without false claims.
- Function 2 has a stable, narrow task: fill unresolved paths and pass the same validator.
- The operation library must grow deliberately as SRTP gains proven reusable semantics.
