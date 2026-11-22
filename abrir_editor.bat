@echo off
chcp 65001 >nul
cd /d "%~dp0"

if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" "editor_controle.py"
) else (
    start "" pythonw "editor_controle.py"
)
