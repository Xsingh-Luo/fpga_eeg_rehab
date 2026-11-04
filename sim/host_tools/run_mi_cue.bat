@echo off
chcp 65001 >nul
cd /d "%~dp0.."
set "PY=H:\FPGA2026QS\.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" "tools\mi_cue.py" %*
if errorlevel 1 pause
