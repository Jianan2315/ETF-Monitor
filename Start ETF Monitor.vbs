Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
folder = fso.GetParentFolderName(WScript.ScriptFullName)
command = "cmd /c """ & folder & "\launch_monitor.bat"""
shell.Run command, 0, False
