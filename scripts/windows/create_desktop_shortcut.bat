@echo off
setlocal EnableExtensions
set "SCRIPT_DIR=%~dp0"
set "PROJECT_ROOT=%SCRIPT_DIR%\..\.."
for %%I in ("%PROJECT_ROOT%") do set "PROJECT_ROOT=%%~fI"
set "ICON_PATH=%SCRIPT_DIR%icons\etf-monitor.ico"
set "GENERATED_ICON_DIR=%SCRIPT_DIR%icons\generated"

if not exist "%ICON_PATH%" (
  echo Icon file not found:
  echo %ICON_PATH%
  echo Place etf-monitor.ico in scripts\windows\icons and run this script again.
  pause
  exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%prepare_shortcut_icon.ps1" -SourceIcon "%ICON_PATH%" -DestinationDirectory "%GENERATED_ICON_DIR%" > "%TEMP%\etf-monitor-icon-path.txt"
if errorlevel 1 (
  echo Failed to prepare a Windows-compatible shortcut icon.
  pause
  exit /b 1
)

set /p SHORTCUT_ICON_PATH=<"%TEMP%\etf-monitor-icon-path.txt"
if not exist "%SHORTCUT_ICON_PATH%" (
  echo Failed to prepare a Windows-compatible shortcut icon.
  pause
  exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$iconPath='%SHORTCUT_ICON_PATH%';" ^
  "$s=(New-Object -COM WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\ETF Monitor.lnk');" ^
  "$s.TargetPath='%PROJECT_ROOT%\Start ETF Monitor.bat';" ^
  "$s.WorkingDirectory='%PROJECT_ROOT%';" ^
  "$s.IconLocation=($iconPath+',0');" ^
  "$s.Description='ETF Monitor Local Control Center';" ^
  "$s.Save();" ^
  "$iconRefresh=Join-Path $env:SystemRoot 'System32\ie4uinit.exe';" ^
  "if (Test-Path $iconRefresh) { & $iconRefresh -show }"
if errorlevel 1 (
  echo Failed to create the desktop shortcut.
  pause
  exit /b 1
)
echo Desktop shortcut created with the current icon.
pause
