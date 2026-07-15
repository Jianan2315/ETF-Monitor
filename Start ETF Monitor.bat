@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

title ETF Monitor Control Server

if not exist "logs" mkdir "logs"
set "BOOT_LOG=%CD%\logs\launcher.log"
set "SERVER_LOG=%CD%\logs\control_server_console.log"

echo ============================================================
echo                 ETF Monitor Launcher V10.4
echo ============================================================
echo Project folder:
echo %CD%
echo.
echo [%date% %time%] Launcher started.>>"%BOOT_LOG%"

rem ------------------------------------------------------------
rem Detect an already-running service on port 8765.
rem This was the main reason the old BAT appeared and disappeared:
rem control_server.py saw the occupied port, opened the old page,
rem then exited normally.
rem ------------------------------------------------------------
set "EXISTING_PID="
for /f "usebackq delims=" %%P in (`powershell -NoProfile -Command "$c=Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue ^| Select-Object -First 1; if($c){$c.OwningProcess}"`) do (
    set "EXISTING_PID=%%P"
)

if defined EXISTING_PID (
    echo [NOTICE] Port 8765 is already in use by process PID !EXISTING_PID!.
    echo [NOTICE] This usually means an older ETF Monitor server is still running.
    echo [%date% %time%] Existing listener found on port 8765, PID=!EXISTING_PID!.>>"%BOOT_LOG%"
    echo.
    choice /C YN /N /M "Stop the existing server and start this version? [Y/N]: "
    if errorlevel 2 (
        echo.
        echo The existing server was left running.
        echo Open http://127.0.0.1:8765/ to use it.
        echo.
        start "" "http://127.0.0.1:8765/"
        echo Press any key to close this launcher.
        pause >nul
        exit /b 0
    )

    echo.
    echo Stopping PID !EXISTING_PID!...
    taskkill /PID !EXISTING_PID! /T /F
    if errorlevel 1 (
        echo [ERROR] Could not stop the existing process.
        echo [ERROR] Failed to stop PID !EXISTING_PID!.>>"%BOOT_LOG%"
        echo Run this BAT as administrator, or close the old command window manually.
        echo.
        pause
        exit /b 1
    )

    echo Existing server stopped.
    echo [%date% %time%] Existing server PID !EXISTING_PID! stopped.>>"%BOOT_LOG%"
    timeout /t 2 /nobreak >nul
    echo.
)

rem ------------------------------------------------------------
rem Find a usable Python installation.
rem ------------------------------------------------------------
set "BASE_PYTHON="
where py >nul 2>nul
if not errorlevel 1 (
    py -3 --version >nul 2>nul
    if not errorlevel 1 set "BASE_PYTHON=py -3"
)

if not defined BASE_PYTHON (
    where python >nul 2>nul
    if not errorlevel 1 set "BASE_PYTHON=python"
)

if not defined BASE_PYTHON (
    echo [ERROR] Python 3 was not found.
    echo [ERROR] Python 3 was not found.>>"%BOOT_LOG%"
    echo.
    echo Install Python 3 and select "Add Python to PATH" during setup.
    echo Then run this BAT file again.
    echo.
    pause
    exit /b 1
)

echo [1/5] Python was found:
%BASE_PYTHON% --version
if errorlevel 1 (
    echo [ERROR] Python exists but could not be started.
    echo [ERROR] Base Python could not start.>>"%BOOT_LOG%"
    pause
    exit /b 1
)
echo.

rem ------------------------------------------------------------
rem Create or reuse the project virtual environment.
rem ------------------------------------------------------------
if not exist ".venv\Scripts\python.exe" (
    echo [2/5] Creating .venv...
    echo [%date% %time%] Creating virtual environment.>>"%BOOT_LOG%"
    %BASE_PYTHON% -m venv ".venv"
    if errorlevel 1 (
        echo [ERROR] Failed to create .venv.
        echo [ERROR] Failed to create virtual environment.>>"%BOOT_LOG%"
        pause
        exit /b 1
    )
) else (
    echo [2/5] Existing .venv found.
)

set "PYTHON_EXE=%CD%\.venv\Scripts\python.exe"

if not exist "%PYTHON_EXE%" (
    echo [ERROR] .venv Python executable is missing.
    echo [ERROR] .venv Python executable is missing.>>"%BOOT_LOG%"
    pause
    exit /b 1
)
echo.

rem ------------------------------------------------------------
rem Check and install dependencies.
rem ------------------------------------------------------------
echo [3/5] Checking dependencies...
"%PYTHON_EXE%" -c "import flask, pandas, requests, akshare, msal" >nul 2>nul
if errorlevel 1 (
    echo Some packages are missing. Installing requirements...
    echo [%date% %time%] Installing requirements.>>"%BOOT_LOG%"

    "%PYTHON_EXE%" -m pip install --upgrade pip
    if errorlevel 1 (
        echo [ERROR] Failed to upgrade pip.
        echo [ERROR] pip upgrade failed.>>"%BOOT_LOG%"
        pause
        exit /b 1
    )

    "%PYTHON_EXE%" -m pip install -r "requirements.txt"
    if errorlevel 1 (
        echo [ERROR] Failed to install requirements.
        echo [ERROR] requirements installation failed.>>"%BOOT_LOG%"
        pause
        exit /b 1
    )
) else (
    echo Dependencies are ready.
)
echo.

rem ------------------------------------------------------------
rem Validate the control server before starting.
rem ------------------------------------------------------------
echo [4/5] Checking control server...
"%PYTHON_EXE%" -m py_compile "control_server.py"
if errorlevel 1 (
    echo [ERROR] control_server.py contains an error.
    echo [ERROR] control_server.py compile check failed.>>"%BOOT_LOG%"
    pause
    exit /b 1
)
echo Control server check passed.
echo.

rem ------------------------------------------------------------
rem Start the server.
rem Output is shown in this window and also appended to a log file.
rem ------------------------------------------------------------
echo [5/5] Starting ETF Monitor...
echo.
echo Browser address:
echo http://127.0.0.1:8765/
echo.
echo Keep this window open while using the control console.
echo Press Ctrl+C to stop the server.
echo All server output is also written to:
echo %SERVER_LOG%
echo.
echo [%date% %time%] Starting control_server.py.>>"%BOOT_LOG%"
echo.>>"%SERVER_LOG%"
echo ============================================================>>"%SERVER_LOG%"
echo [%date% %time%] Starting ETF Monitor Control Server V10.4>>"%SERVER_LOG%"
echo ============================================================>>"%SERVER_LOG%"

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "& '%PYTHON_EXE%' 'control_server.py' 2^>^&1 | Tee-Object -FilePath '%SERVER_LOG%' -Append; exit $LASTEXITCODE"

set "SERVER_EXIT=%ERRORLEVEL%"

echo.
echo ============================================================
echo ETF Monitor stopped. Exit code: %SERVER_EXIT%
echo ============================================================
echo [%date% %time%] Control server stopped with code %SERVER_EXIT%.>>"%BOOT_LOG%"
echo [%date% %time%] Control server stopped with code %SERVER_EXIT%.>>"%SERVER_LOG%"
echo.
echo The window will stay open so you can review all messages.
echo Press any key to close it.
pause >nul

exit /b %SERVER_EXIT%
