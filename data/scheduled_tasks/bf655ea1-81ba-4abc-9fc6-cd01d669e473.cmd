@echo off
cd /d "C:\Users\flywi\Desktop\gitRepos\ETF-Monitor"
if not exist "logs" mkdir "logs"
set "PYTHON_EXE=C:\Users\flywi\Desktop\gitRepos\ETF-Monitor\.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"
echo [%date% %time%] Scheduled plan bf655ea1-81ba-4abc-9fc6-cd01d669e473 started. >> "logs\scheduled_tasks.log"
"%PYTHON_EXE%" run.py --mode auto --slot 14:45 >> "logs\scheduled_tasks.log" 2>&1
echo [%date% %time%] Scheduled plan bf655ea1-81ba-4abc-9fc6-cd01d669e473 finished with code %errorlevel%. >> "logs\scheduled_tasks.log"
