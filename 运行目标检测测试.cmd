@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 请先开启传感器模式，在“YOLOv10 目标检测”中添加示例椅子并开始检测。
echo 测试期间保持机器狗停止，不操作方向按钮。
where py >nul 2>nul
if not errorlevel 1 (
  py -3 "%~dp0baseline\desktop_install\vm.py" verify-vision
) else (
  python "%~dp0baseline\desktop_install\vm.py" verify-vision
)
pause
