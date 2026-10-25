@echo off
chcp 65001 >nul
setlocal
echo ============================================
echo  PicturePick 一键打包
echo ============================================
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\build.ps1" %*
set "CODE=%ERRORLEVEL%"
echo.
if "%CODE%"=="0" (
  echo 打包完成：release\PicturePick-WebView2\PicturePick.exe
) else (
  echo 打包失败（exit %CODE%），请把上面的输出发给开发者。
)
echo.
pause
exit /b %CODE%
