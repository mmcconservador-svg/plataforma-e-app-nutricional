@echo off
rem Nutri Saude Integral - inicia em qualquer PC Windows (dados e chaves ficam nas pastas deste HD)
setlocal
cd /d "%~dp0"
set "VENV=%LOCALAPPDATA%\nutri_saude_venv"

where py >nul 2>nul && (set "PY=py -3") || (set "PY=python")
%PY% --version >nul 2>nul || (echo Python 3.10+ nao encontrado. Instale em https://www.python.org/downloads/ e marque "Add to PATH". & pause & exit /b 1)

if not exist "%VENV%\Scripts\python.exe" (
  echo Preparando o ambiente neste computador - na 1a vez precisa de internet...
  %PY% -m venv "%VENV%" || (pause & exit /b 1)
)
if exist wheels (
  "%VENV%\Scripts\python.exe" -m pip install -q --no-index --find-links wheels -r requirements.txt
) else (
  "%VENV%\Scripts\python.exe" -m pip install -q -r requirements.txt
)
if errorlevel 1 (echo Falha ao instalar dependencias. & pause & exit /b 1)

"%VENV%\Scripts\python.exe" -m flask --app app garantir-profissional
start "" http://127.0.0.1:5000
"%VENV%\Scripts\python.exe" servir.py
pause
