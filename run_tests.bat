@echo off
REM Runs the automated checks. Run run.bat once first so the environment exists.
cd /d "%~dp0"
".venv\Scripts\python.exe" -m pytest
pause
