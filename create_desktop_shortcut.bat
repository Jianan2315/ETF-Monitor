@echo off
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$s=(New-Object -COM WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\ETF Monitor.lnk');" ^
  "$s.TargetPath='%CD%\Start ETF Monitor.bat';" ^
  "$s.WorkingDirectory='%CD%';" ^
  "$s.Description='ETF Monitor Local Control Center';" ^
  "$s.Save()"
if errorlevel 1 (
  echo Failed to create the desktop shortcut.
  pause
  exit /b 1
)
echo Desktop shortcut created.
pause
