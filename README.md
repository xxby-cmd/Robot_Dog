# 机器狗算法应用开发

当前为 Baseline 复现阶段。**Go2 行走控制运动自测通过，待用户验收；完整 Baseline 尚未完成。**

## 新电脑从 GitHub 安装

**不需要笔记本运行盘的下载地址，也不需要传递旧 SSH 私钥。** 仓库已提供从公开依赖重建的 Windows x64 部署入口，以及项目规则、规划和接手上下文。

先安装 Python 3.11 以上（含 Tcl/Tk），再执行：

```powershell
git clone https://github.com/xxby-cmd/Robot_Dog.git Robot_Dog-Desktop
cd Robot_Dog-Desktop
py -3 baseline/desktop_install/bootstrap.py preflight
py -3 baseline/desktop_install/bootstrap.py install --start --wait
```

也可双击 `首次部署机器狗.cmd`；以后使用 `运行本机部署机器狗.cmd`。默认 `main` 和 `codex/desktop-handoff-20261027` 交接分支均包含部署入口。

完整步骤、缺少 OpenSSH / WHPX 时的处理、日志和重试方法见 [新机部署说明](新机部署说明.txt)。前置条件不满足时脚本会停下，不自动修改 Windows 虚拟化/安全设置。先保留台式机旧项目与虚拟机。

## 当前内容

- `baseline/go2_jazzy/`：Ubuntu 24.04、ROS2 Jazzy、Gazebo Harmonic、CHAMP 的 Go2 行走工程、启动工具与测试。
- `baseline/desktop_install/`：固定下载清单、校验、便携工具提取、建盘、首次安装与本机图形入口。
- `baseline/go2_jazzy/vendor/`：锁定版本的社区上游源码和本地修复，来源见 `upstream.json`。
- `baseline/go2_jazzy/verification/`：运动结果、遥测和截图；这些是笔记本上的历史验证证据。
- `baseline/legacy_noetic/`：原 Ubuntu 20.04 / Noetic 运行工具的源码备份。
- `AGENTS.md`：本阶段目标、分工、验收规则和限制。
- [当前任务](context/01-CURRENT-TASK.md)、[稳定决策](context/02-CURRENT-DECISIONS.md)、[项目规划](项目规划.txt)：另一台 Codex 的接手上下文。
- [台式机迁移交接说明](台式机迁移交接说明.txt)、[操作说明](Go2行走仿真使用说明.txt)、[验证记录](行走控制验证记录.txt)。

已实现前进、后退、左右转、停止、指令中断自动停止。四方向物理运动测试通过；尚未验收激光/相机/定位里程计链路、SLAM、YOLO、导航和自主探索。

## 如何恢复运行环境

部署脚本从 [固定依赖清单](baseline/desktop_install/assets.json) 下载 QEMU 11.1.0、Ubuntu 24.04 固定构建、7-Zip 和 pycdlib，并核对哈希。QEMU 便携解压，7-Zip MSI 仅作数据提取，ISO 库本地导入；不依赖全局 QEMU / 7-Zip 安装。

脚本使用仓库 `vendor/` 生成安装介质，在目标电脑生成 SSH 密钥、创建独立 60GiB 动态磁盘，再由 Ubuntu 首次启动安装依赖并编译控制代码。默认运行目录在仓库旁，CPU、内存和空闲端口按本机生成；不要求原笔记本的 D: / E: 盘。已有环境不会被覆盖。

Git 不存储私人系统盘、密钥或本机配置，GitHub Releases 也无需提供旧运行包。首次重建需要联网与下载/编译时间。只有想保留原已安装系统时，才需要在完全关机后另行安全拷盘。

安装后等待面板显示关节 12/12、控制器激活并站稳，按住方向按钮移动、松开停止。新机运行 `py -3 baseline/desktop_install/vm.py verify` 可保存自身运动证据；通过后再由用户验收。

## 性能问题尚在排查

笔记本已分配 8 vCPU / 12GB 内存，曾验证 VirGL 使用 RTX 4060，但后续启动又出现 Intel 核显渲染。既有两次图形测试平均实时因子约 0.45～0.76，不能保证全程实时；正常退出偶尔较慢。

最新笔记本优化复测的完整运动测试通过，平均实时因子约 0.572，仍待用户体验验收，详见 [性能记录](虚拟机性能优化记录.txt)。SDL、进程调度与较低开销的来宾桌面没有保证持续实时；台式机须重新核对实际渲染器和运动结果。

迁移工具已在隔离目录测试下载/校验、便携解压、独立建盘、ISO 内容和保护逻辑；这不等于已在目标台式机验证过首次启动、安装或运动，不得把历史证据直接当作新机结果。

不要为排查性能而直接运行仓库里历史遗留的虚拟化冲突修复脚本。影响 eNSP、VirtualBox 或 Windows 虚拟化/安全设置前，先与用户确认。验收当前任务前不进入下一项功能。
