@echo off
setlocal
cd /d "%~dp0"
set "LOG=%~dp0verify-desktop.log"
echo ==== PicturePick verify %DATE% %TIME% ==== > "%LOG%"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\verify-electron.ps1" %* >> "%LOG%" 2>&1
set "CODE=%ERRORLEVEL%"
echo exit=%CODE% >> "%LOG%"
echo.
type "%LOG%"
echo.
echo Log: %LOG%
pause
exit /b %CODE%
