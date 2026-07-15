@echo off
cd /d "%~dp0"

if not exist "logs" mkdir "logs"
set "BOOT_LOG=%CD%\logs\launcher.log"

set "BASE_PYTHON=python"
where python >nul 2>nul
if errorlevel 1 (
  echo [%date% %time%] Python was not found. >> "%BOOT_LOG%"
  start "" "https://www.python.org/downloads/windows/"
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo [%date% %time%] Creating virtual environment... >> "%BOOT_LOG%"
  %BASE_PYTHON% -m venv .venv >> "%BOOT_LOG%" 2>&1
)

set "PYTHON_EXE=%CD%\.venv\Scripts\python.exe"

"%PYTHON_EXE%" -c "import flask, pandas, requests, akshare, msal" >nul 2>nul
if errorlevel 1 (
  echo [%date% %time%] Installing or repairing dependencies... >> "%BOOT_LOG%"
  "%PYTHON_EXE%" -m pip install --upgrade pip >> "%BOOT_LOG%" 2>&1
  "%PYTHON_EXE%" -m pip install -r requirements.txt >> "%BOOT_LOG%" 2>&1
)

echo [%date% %time%] Starting ETF Monitor control server. >> "%BOOT_LOG%"
"%PYTHON_EXE%" control_server.py >> "%BOOT_LOG%" 2>&1
