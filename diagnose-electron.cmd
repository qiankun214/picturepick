@echo off
setlocal
cd /d "%~dp0"
echo ============================================
echo  PicturePick Electron ??
echo ============================================
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\diagnose-electron.ps1"
echo.
echo ?????%~dp0electron-diagnose.log
echo ????????????
echo.
pause
