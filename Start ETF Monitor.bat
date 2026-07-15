@echo off
setlocal EnableExtensions
cd /d "%~dp0"

title ETF Monitor Control Server

echo ============================================================
echo                 ETF Monitor Launcher
echo ============================================================
echo Project folder:
echo %CD%
echo.

if not exist "logs" mkdir "logs"
set "BOOT_LOG=%CD%\logs\launcher.log"

echo [%date% %time%] Launcher started.>>"%BOOT_LOG%"

rem ------------------------------------------------------------
rem Find a usable base Python installation.
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
rem Create the project virtual environment when missing.
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
    echo [ERROR] .venv was created, but python.exe is missing.
    echo [ERROR] .venv Python executable is missing.>>"%BOOT_LOG%"
    pause
    exit /b 1
)
echo.

rem ------------------------------------------------------------
rem Check and install project dependencies.
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
rem Make sure the local server can be imported before starting it.
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
rem Start the visible server process.
rem Do not close this window while using the control console.
rem ------------------------------------------------------------
echo [5/5] Starting ETF Monitor...
echo.
echo The browser should open automatically at:
echo http://127.0.0.1:8765/
echo.
echo Keep this window open while using the control console.
echo Press Ctrl+C in this window to stop the server.
echo.
echo [%date% %time%] Starting control_server.py.>>"%BOOT_LOG%"

"%PYTHON_EXE%" "control_server.py"
set "SERVER_EXIT=%ERRORLEVEL%"

echo.
echo ============================================================
echo ETF Monitor stopped. Exit code: %SERVER_EXIT%
echo ============================================================
echo [%date% %time%] Control server stopped with code %SERVER_EXIT%.>>"%BOOT_LOG%"

if not "%SERVER_EXIT%"=="0" (
    echo An error occurred. Review the messages above and:
    echo %BOOT_LOG%
    echo.
    pause
)

exit /b %SERVER_EXIT%
