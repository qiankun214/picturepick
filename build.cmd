@echo off
setlocal
cd /d "%~dp0"
set "LOG=%~dp0build.log"
echo ==== PicturePick build %DATE% %TIME% ==== > "%LOG%"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\build.ps1" %* >> "%LOG%" 2>&1
set "CODE=%ERRORLEVEL%"
echo exit=%CODE% >> "%LOG%"
echo.
type "%LOG%"
echo.
echo Log: %LOG%
pause
exit /b %CODE%
