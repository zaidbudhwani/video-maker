@echo off
rem Starts the Video Maker website by itself each time you sign in to Windows (it opens minimized).
rem Only useful if the web address stays the same (see docs\HANDOVER.md): a free tunnel gets a new address every start.
cd /d "%~dp0"
powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Startup')+'\Alex Video Maker.lnk'); $s.TargetPath='%~dp0Start Website.bat'; $s.WorkingDirectory='%~dp0'; $s.WindowStyle=7; $s.Save()"
echo.
echo Done. The website will now start when you sign in to Windows.
echo To undo this, double-click "Remove Autostart.bat".
pause
