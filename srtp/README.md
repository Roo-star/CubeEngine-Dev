# CubeEngine SRTP

Function 1 now ingests a complete source-game project instead of treating a
hand-written Rule Schema example as proof of source-game conversion.

Run the Workbench:

```powershell
cd E:\CubeEngine\CubeEngine-SRTP
C:\Users\Yingr\.pyenv\pyenv-win\versions\3.9.1\python.exe -m srtp.workbench
```

Key modules:

- `source_importer.py` — whole-project inventory, Python AST/data analysis,
  runtime/dependency discovery and source-backed parameters.
- `source_game.py` — Source Game Package, coverage and transformation plan.
- `source_runner.py` — supervised original-game fidelity runtime and Windows
  best-effort embedding.
- `variant.py` — reversible variants for isolated safe data settings.
- `runtime_bootstrap.py` — logged framework compatibility boundary; never used
  during static import.
- `parser.py` / `extractors/` — lower-level single-file Rule Schema extraction.
- `stal_adapter.py` — the proven declarative subset only.
- `reference_games/` — unmodified, licensed, runnable third-party source games.

The Workbench deliberately blocks a generic Ursina cube when source-specific
mechanic and renderer lifts have not been compiled.

See `SRTP_FUNCTION_1.md`, `SRTP_SOURCE_GAME_PACKAGE.md`,
`SRTP_RULE_SCHEMA_V1.md` and `THIRD_PARTY_NOTICES.md` in the repository root.
