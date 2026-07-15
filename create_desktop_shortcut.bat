@echo off
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$s=(New-Object -COM WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\ETF Monitor.lnk');" ^
  "$s.TargetPath='%CD%\Start ETF Monitor.vbs';" ^
  "$s.WorkingDirectory='%CD%';" ^
  "$s.Description='ETF Monitor Local Control Center';" ^
  "$s.Save()"
echo Desktop shortcut created.
pause
