@echo off
rem Makes Windows save a backup of the accounts, settings and video details every Sunday evening.
rem The backup goes to Documents\video-backups (the newest 8 are kept). It never contains the .env file or the session key.
rem It does hold password hashes: copy the zip files only to somewhere private (your own OneDrive is fine).
rem   Schedule Backup.bat          asks whether to turn it on or off
rem   Schedule Backup.bat remove   turns it off          Schedule Backup.bat run   makes one backup now
cd /d "%~dp0"
if /i "%~1"=="run" goto dobackup
if /i "%~1"=="remove" goto remove
if not "%~1"=="" goto add
echo.
echo  1 = set up the weekly backup
echo  2 = turn the weekly backup off
echo.
choice /c 12 /n /m "Type 1 or 2: "
if errorlevel 2 goto remove

:add
schtasks /Create /F /SC WEEKLY /D SUN /ST 20:00 /TN "Alex Video Maker backup" /TR "\"%~dp0Schedule Backup.bat\" run" >nul
if errorlevel 1 (
  echo.
  echo  Windows would not set it up. Tell the maintainer.
  goto end
)
echo.
echo  Done. A backup is saved every Sunday at 8 pm, as long as the computer is on then.
echo  To turn it off, run this again and type 2.
goto end

:remove
schtasks /Delete /F /TN "Alex Video Maker backup" >nul 2>&1
echo.
echo  The weekly backup is turned off.
goto end

:dobackup
if not exist out mkdir out
echo [%date% %time%] backup>> out\backup.log
venv\Scripts\python maintain.py backup >> out\backup.log 2>&1
exit /b %errorlevel%

:end
if "%~1"=="" pause
