@echo off
cd /d "%~dp0"
py -3.13 setup_editor.py
if errorlevel 1 pause
