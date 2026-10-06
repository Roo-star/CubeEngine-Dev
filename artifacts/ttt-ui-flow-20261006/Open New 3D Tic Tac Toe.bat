@echo off
setlocal
cd /d "%~dp0..\.."
".venv-workbench\Scripts\python.exe" -m srtp.studio.app --source "srtp/reference_games/pygame_tictactoe/main.py" --project ".cubeengine_llm/tic.tac.toe/target_20261006-220937/project.manifest.json"
endlocal
