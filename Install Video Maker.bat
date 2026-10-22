@echo off
rem Sets up the Video Maker on this computer. Safe to run again. See docs\INSTALL.md.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
echo.
pause
