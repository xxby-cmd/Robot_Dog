# 机器狗算法应用开发

当前为 Baseline 复现阶段。**Go2 行走控制运动自测通过，待用户验收；完整 Baseline 尚未完成。**

## 获取本次交接版本

本次交接分支为 `codex/desktop-handoff-20261027`。新电脑可执行：

```powershell
git clone --branch codex/desktop-handoff-20261027 https://github.com/xxby-cmd/Robot_Dog.git
```

如果台式机已经有项目，先保留其未提交修改和既有仿真环境，再获取本分支。不要直接覆盖同名运行盘。

## 当前内容

- `baseline/go2_jazzy/`：Ubuntu 24.04、ROS2 Jazzy、Gazebo Harmonic、CHAMP 的 Go2 行走工程、启动工具与测试。
- `baseline/go2_jazzy/vendor/`：锁定版本的社区上游源码和本地修复，来源见 `upstream.json`。
- `baseline/go2_jazzy/verification/`：运动结果、遥测和截图；这些是笔记本上的历史验证证据。
- `baseline/legacy_noetic/`：原 Ubuntu 20.04 / Noetic 运行工具的源码备份。
- `AGENTS.md`：本阶段目标、分工、验收规则和限制。
- [台式机迁移交接说明](台式机迁移交接说明.txt)、[操作说明](Go2行走仿真使用说明.txt)、[验证记录](行走控制验证记录.txt)。

已实现前进、后退、左右转、停止、指令中断自动停止。四方向物理运动测试通过；尚未验收激光/相机/定位里程计链路、SLAM、YOLO、导航和自主探索。

## 台式机实测还需要运行环境

**Git 仓库包含源码与进度，不包含已安装的虚拟机磁盘、QEMU 二进制或 SSH 私钥。只克隆仓库不能直接启动现成虚拟机。**

最快方式是在笔记本正常关机、确认 QEMU 进程完全退出后，单独复制 `E:\RobotDogRuntime`。仅搬新环境时，至少保留：

1. `qemu/` 整个文件夹，含 QEMU 程序、DLL 和数据文件。
2. `jazzy/robot-dog-clean.qcow2`：当前有效运行盘。
3. `jazzy/robot-dog-seed.iso`：当前启动器挂载的介质。
4. `robot_vm_key`、`robot_vm_key.pub`：现有本机 SSH 连接所用密钥，通过用户自己的存储介质迁移。

不要把失败盘 `jazzy/robot-dog.qcow2`、恢复工作盘或仓库中原有的小型 `Robot_Dog.vdi` 当作当前运行盘。

当前入口 `运行Go2行走仿真.cmd` 使用 `D:\Python\pythonw.exe`；台式机需确认 Python 3 / Tkinter 路径。`baseline/go2_jazzy/vm.py` 中还需核对运行目录、QEMU 和私钥位置。新环境使用本机端口 2265 / 2266，旧环境为 2255 / 2256。

准备好运行环境后，双击该入口，点击启动，等待 Ubuntu 中的行走面板显示 12 个关节且控制器已激活，再按住方向按钮移动、松开停止。

如果无法搬运运行盘，可让 Codex 根据现有 `provision.sh`、`make_seed.py` 和 `vendor/` 重建环境。首次安装需要另备 QEMU、Ubuntu 基础镜像、本机 SSH 密钥等依赖；本交接不声称从 Git 克隆后的一键重建或台式机实测已通过。

## 性能问题尚在排查

笔记本已分配 8 vCPU / 12GB 内存，曾验证 VirGL 使用 RTX 4060，但后续启动又出现 Intel 核显渲染。既有两次图形测试平均实时因子约 0.45～0.76，不能保证全程实时；正常退出偶尔较慢。

性能相关代码仍在调试：交接快照包含 SDL / GTK 选择、虚拟机进程调度和性能测量工具，不等于这些调整已完成验收。请以获取分支时的代码为准，在台式机重新核对实际渲染器和运动结果。

不要为排查性能而直接运行仓库里历史遗留的虚拟化冲突修复脚本。影响 eNSP、VirtualBox 或 Windows 虚拟化/安全设置前，先与用户确认。验收当前任务前不进入下一项功能。
