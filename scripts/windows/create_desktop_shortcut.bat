@echo off
set "PROJECT_ROOT=%~dp0\..\.."
for %%I in ("%PROJECT_ROOT%") do set "PROJECT_ROOT=%%~fI"
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$s=(New-Object -COM WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\ETF Monitor.lnk');" ^
  "$s.TargetPath='%PROJECT_ROOT%\Start ETF Monitor.bat';" ^
  "$s.WorkingDirectory='%PROJECT_ROOT%';" ^
  "$s.Description='ETF Monitor Local Control Center';" ^
  "$s.Save()"
if errorlevel 1 (
  echo Failed to create the desktop shortcut.
  pause
  exit /b 1
)
echo Desktop shortcut created.
pause
