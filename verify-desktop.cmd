@echo off
chcp 65001 >nul
setlocal
echo ============================================
echo  PicturePick 桌面版自检
echo ============================================
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\verify-desktop.ps1" %*
set "CODE=%ERRORLEVEL%"
echo.
if "%CODE%"=="0" (
  echo 自检通过：桌面版可以正常启动。
) else (
  echo 自检失败（exit %CODE%），请把上面的输出、截图路径和日志发给开发者。
)
echo.
pause
exit /b %CODE%
