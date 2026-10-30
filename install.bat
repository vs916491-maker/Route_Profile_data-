@echo off
REM Route Importer - one-time install of the Python packages.
REM If a "wheels" folder exists next to this file (made with make_offline_bundle.bat
REM on a PC with internet), installation runs fully offline from it.
cd /d "%~dp0"
if exist wheels (
    echo Installing from the local wheels folder (offline)...
    python -m pip install --no-index --find-links wheels -r requirements.txt
) else (
    echo Installing from the internet (one time only)...
    python -m pip install -r requirements.txt
)
echo.
echo Done. Start the tool with run_route_importer.bat
pause
