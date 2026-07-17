@echo off
cd /d "C:\Users\flywi\Desktop\gitRepos\ETF-Monitor"
if not exist "logs" mkdir "logs"
set "PYTHON_EXE=C:\Users\flywi\Desktop\gitRepos\ETF-Monitor\.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"
echo [%date% %time%] Scheduled plan 8eff2011-bf08-425e-bd12-1ea8e4bc8bbb started. >> "logs\scheduled_tasks.log"
"%PYTHON_EXE%" run.py --mode auto --slot 14:55 >> "logs\scheduled_tasks.log" 2>&1
echo [%date% %time%] Scheduled plan 8eff2011-bf08-425e-bd12-1ea8e4bc8bbb finished with code %errorlevel%. >> "logs\scheduled_tasks.log"
