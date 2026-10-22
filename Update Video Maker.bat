@echo off
rem Gets the latest version of the Video Maker and re-checks the install. Your settings, sign-in and videos are kept.
cd /d "%~dp0"
echo Getting the latest version...
git pull --ff-only
if errorlevel 1 (
  echo.
  echo Could not update. Check the internet connection. Nothing was changed.
  pause
  exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
echo.
pause
