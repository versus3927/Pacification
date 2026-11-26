@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    where py >nul 2>nul
    if %errorlevel%==0 (
        py -3 -m venv .venv
    ) else (
        python -m venv .venv
    )
)

".venv\Scripts\python.exe" -m pip install -e .
".venv\Scripts\python.exe" -m pacification

endlocal
