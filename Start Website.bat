@echo off
rem Starts the Video Maker website and the tunnel that gives it a web address for her phone.
rem If the website or the tunnel stops (a crash, or the Wi-Fi dropping) each one starts again by itself.
rem The website's own messages go to out\server.log.
if /i "%~1"=="server" goto serverloop
cd /d "%~dp0"
title Alex Video Maker - keep this window open
set OK=0
if exist users.json set OK=1
findstr /r /c:"^APP_PASSWORD=..*" .env >nul 2>&1 && set OK=1
if "%OK%"=="0" (
  echo.
  echo  There are no accounts yet. Add one first, then start this again:
  echo      venv\Scripts\python manage_users.py add NAME --admin
  echo.
  pause
  exit /b 1
)
if not exist out mkdir out
rem Stop any older copy of the site that is still running, so the new code is the one she gets
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name like 'python%%'\" | Where-Object { $_.CommandLine -like '*uvicorn*web.app:app*--port 8000*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
call :killvideos
echo Starting Alex's Video Maker website...
start "Video Maker server" /min cmd /c ""%~f0" server"
timeout /t 4 >nul
:tunnelloop
echo Starting the tunnel that gives it a web address (a free one gets a NEW address every time)...
venv\Scripts\python tunnel.py
if errorlevel 42 (
  echo.
  echo  The tunnel program isn't installed on this computer. Tell the maintainer.
  pause
  exit /b 1
)
echo.
echo  The connection stopped (Wi-Fi dropped, or the tunnel closed). Making a NEW link in 5 seconds...
echo  The new link will be shown below and saved in current-link.txt. Send that one to Alex.
echo [%date% %time%] tunnel stopped, starting again>> out\server.log
timeout /t 5 >nul
goto tunnelloop

:serverloop
cd /d "%~dp0"
:serverrun
rem If something else already has the site's address (port 8000), do NOT start again and do NOT stop videos: wait.
powershell -NoProfile -Command "try { $c = New-Object Net.Sockets.TcpClient; $c.Connect('127.0.0.1', 8000); $c.Close(); exit 1 } catch { exit 0 }"
if errorlevel 1 (
  echo [%date% %time%] port 8000 is already in use, not starting a second website. Waiting.>> out\server.log
  timeout /t 30 >nul
  goto serverrun
)
call :killvideos
echo [%date% %time%] website starting>> out\server.log
venv\Scripts\python -m uvicorn web.app:app --port 8000 >> out\server.log 2>&1
echo [%date% %time%] website stopped, starting again in 5 seconds>> out\server.log
timeout /t 5 >nul
goto serverrun

:killvideos
rem A video that was being made when the site stopped can keep running by itself: stop those leftovers
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name like 'python%%'\" | Where-Object { $_.CommandLine -like '*video_tool.pipeline*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
exit /b 0
