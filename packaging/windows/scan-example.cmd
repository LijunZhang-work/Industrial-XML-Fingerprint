@echo off
setlocal DisableDelayedExpansion
"%~dp0runtime\python.exe" -I -B "%~dp0app\portable_launcher.py" "%~dp0." "%~dp0examples\synthetic-plcopen-demo.xml"
set "RESULT=%ERRORLEVEL%"
pause
exit /b %RESULT%
