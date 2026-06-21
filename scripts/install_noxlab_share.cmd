@echo off
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_noxlab_share.ps1"
exit /b %ERRORLEVEL%
