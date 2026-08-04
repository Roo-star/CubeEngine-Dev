# CubeEngine SRTP

Run Function 1 Workbench:

```powershell
cd E:\CubeEngine\CubeEngine-SRTP
C:\Users\Yingr\.pyenv\pyenv-win\versions\3.9.1\python.exe -m srtp.workbench
```

Core files:

- `rule-schema-v1.schema.json` — canonical Rule Schema contract.
- `parser.py` — safe file pipeline, validation, provenance and Function 2 handoff.
- `extractors/json_source.py` — canonical and alias-based JSON extraction.
- `extractors/python_source.py` — AST-only Python inspection; never imports a source script.
- `stal_adapter.py` — compiles the proven declarative subset into STAL/Ursina.
- `workbench.py` — designer acceptance UI and safe parameter overrides.
- `preview.py` — one-command source file → Rule Schema → Ursina acceptance path.
- `examples/` — complete, intersection, Python and partial/LLM-boundary fixtures.

See `SRTP_FUNCTION_1.md` and `SRTP_RULE_SCHEMA_V1.md` in the project root for
product decisions and the full field model.

Direct preview:

```powershell
C:\Users\Yingr\.pyenv\pyenv-win\versions\3.9.1\python.exe -m srtp.preview --source-file srtp\examples\placement_line_3x3.json
```
