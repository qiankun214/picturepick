@echo off
setlocal
cd /d "%~dp0"
set "LOG=%~dp0build-electron.log"
echo ==== PicturePick Electron build %DATE% %TIME% ==== > "%LOG%"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\build-electron.ps1" %* >> "%LOG%" 2>&1
set "CODE=%ERRORLEVEL%"
echo exit=%CODE% >> "%LOG%"
echo.
echo ================ BUILD OUTPUT ================
type "%LOG%"
echo ==============================================
echo.
if "%CODE%"=="0" echo BUILD OK. Log: %LOG%
if not "%CODE%"=="0" echo BUILD FAILED exit=%CODE%. Log: %LOG%
echo.
pause
exit /b %CODE%
