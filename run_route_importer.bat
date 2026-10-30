@echo off
cd /d "%~dp0"
start "" pythonw -m route_importer.gui
if errorlevel 1 python -m route_importer.gui
