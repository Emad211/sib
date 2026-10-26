@echo off
setlocal
cd /d "%~dp0"
echo Starting Hesabdari (Flask) on http://127.0.0.1:8080/
echo Close this window to stop the server.
echo.
python start.py
endlocal
