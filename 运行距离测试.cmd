@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 请先开启传感器模式，等待关节就绪，并在测试期间保持机器狗停止。
where py >nul 2>nul
if not errorlevel 1 (
  py -3 "%~dp0baseline\desktop_install\vm.py" verify-distance
) else (
  python "%~dp0baseline\desktop_install\vm.py" verify-distance
)
pause
