@echo off
cd /d "%~dp0"
echo Starting Alex's Video Maker... leave this window open.
start "" http://localhost:8000
venv\Scripts\python -m uvicorn web.app:app --port 8000
