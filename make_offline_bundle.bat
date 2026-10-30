@echo off
REM Run this on a PC WITH internet (same Python version as the work laptop).
REM It downloads every package into .\wheels so install.bat can work with no internet.
cd /d "%~dp0"
python -m pip download -r requirements.txt -d wheels
echo.
echo Copy the whole Route Importer folder (with "wheels") to the work laptop and run install.bat
pause
