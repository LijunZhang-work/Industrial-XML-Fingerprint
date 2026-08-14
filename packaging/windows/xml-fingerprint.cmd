@echo off
setlocal
"%~dp0runtime\python.exe" -I -B -m xml_fingerprint %*
exit /b %ERRORLEVEL%
