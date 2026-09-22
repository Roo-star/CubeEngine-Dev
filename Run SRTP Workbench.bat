@echo off
setlocal
cd /d "%~dp0"
for /f "delims=" %%b in ('git branch --show-current') do set "CUBEENGINE_BRANCH=%%b"
if not "%CUBEENGINE_BRANCH%"=="SRTP" (
    echo This launcher requires the SRTP development branch.
    echo Current branch: %CUBEENGINE_BRANCH%
    pause
    exit /b 1
)
echo CubeEngine local development - branch SRTP
set "CUBEENGINE_PYTHON=C:\Users\Yingr\.pyenv\pyenv-win\versions\3.9.1\python.exe"
if not exist "%CUBEENGINE_PYTHON%" (
    echo Python was not found at %CUBEENGINE_PYTHON%
    pause
    exit /b 1
)
"%CUBEENGINE_PYTHON%" -m srtp.workbench
if errorlevel 1 pause
endlocal
