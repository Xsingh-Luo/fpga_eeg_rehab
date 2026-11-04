@echo off
chcp 65001 >nul
cd /d "%~dp0.."
".venv\Scripts\python.exe" "tools\mi_arrow.py" %*
if errorlevel 1 pause
