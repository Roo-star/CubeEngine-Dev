@echo off
setlocal
cd /d "%~dp0"
set "CUBEENGINE_PYTHON=C:\Users\Yingr\.pyenv\pyenv-win\versions\3.9.1\python.exe"
if not exist "%CUBEENGINE_PYTHON%" (
    echo CubeEngine Python 3.9.1 was not found:
    echo %CUBEENGINE_PYTHON%
    echo.
    pause
    exit /b 1
)
"%CUBEENGINE_PYTHON%" -m srtp.workbench
if errorlevel 1 (
    echo.
    echo CubeEngine Workbench closed with an error.
    pause
)
endlocal
