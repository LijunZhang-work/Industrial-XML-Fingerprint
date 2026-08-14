@echo off
setlocal DisableDelayedExpansion
"%~dp0runtime\python.exe" -I -B "%~dp0app\portable_launcher.py" "%~dp0." "%~1"
set "RESULT=%ERRORLEVEL%"
pause
exit /b %RESULT%
