@echo off
cd /d "%~dp0"
if exist ".venv-editor\Scripts\python.exe" (
  ".venv-editor\Scripts\python.exe" launch.py
) else (
  py -3.13 launch.py
)
if errorlevel 1 pause
