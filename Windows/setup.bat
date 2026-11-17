@echo off
setlocal
cd /d "%~dp0"
py -3.11 -m venv .venv
if errorlevel 1 goto :error
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :error
echo Installation complete. Run start.bat.
pause
exit /b 0
:error
echo Setup failed. Check Python 3.11 x64, pip and your network connection.
pause
exit /b 1
