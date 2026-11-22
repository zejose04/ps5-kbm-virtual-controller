@echo off
chcp 65001 >nul
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" "gta_controle.py" %*
) else (
    python "gta_controle.py" %*
)

if errorlevel 1 (
    echo.
    echo [AVISO] O script encerrou com erro. Veja a mensagem acima.
    pause
)
