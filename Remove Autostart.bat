@echo off
powershell -NoProfile -Command "Remove-Item -ErrorAction SilentlyContinue ([Environment]::GetFolderPath('Startup')+'\Alex Video Maker.lnk')"
echo Done. The website no longer starts by itself.
pause
