@echo off
chcp 65001 >nul
title 修复 VirtualBox VT-x 冲突

fltmc >nul 2>&1
if not "%errorlevel%"=="0" (
    echo 正在请求管理员权限...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

echo.
echo 正在关闭 Windows Hyper-V/VBS 相关组件...

bcdedit /set hypervisorlaunchtype off
dism.exe /Online /Disable-Feature:Microsoft-Hyper-V-All /NoRestart
dism.exe /Online /Disable-Feature:VirtualMachinePlatform /NoRestart
dism.exe /Online /Disable-Feature:HypervisorPlatform /NoRestart
dism.exe /Online /Disable-Feature:Containers-DisposableClientVM /NoRestart

reg add "HKLM\SYSTEM\CurrentControlSet\Control\DeviceGuard" /v EnableVirtualizationBasedSecurity /t REG_DWORD /d 0 /f
reg add "HKLM\SYSTEM\CurrentControlSet\Control\DeviceGuard\Scenarios\HypervisorEnforcedCodeIntegrity" /v Enabled /t REG_DWORD /d 0 /f
reg add "HKLM\SYSTEM\CurrentControlSet\Control\Lsa" /v LsaCfgFlags /t REG_DWORD /d 0 /f

echo.
echo 修复命令已执行完成。
echo 没有卸载 VirtualBox，也没有修改或删除任何虚拟机文件。
echo 必须重启 Windows 后设置才会生效。
echo.
choice /C YN /N /M "现在立即重启电脑吗？[Y/N] "
if errorlevel 2 goto end
shutdown /r /t 10 /c "VirtualBox VT-x 修复完成，系统将在 10 秒后重启。"

:end
echo 请稍后手动重启电脑，然后再次启动 Robot_Dog。
pause
