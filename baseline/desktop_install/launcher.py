"""GUI for the configured desktop instance; no laptop path assumptions."""
import argparse
from pathlib import Path
import threading
import tkinter as tk
from tkinter import messagebox
from common import DEFAULT_CONFIG, load_config
import vm

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path, default=DEFAULT_CONFIG)
    try:
        config = load_config(parser.parse_args().config)
    except (RuntimeError, OSError, ValueError) as error:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror('需要先部署', '请先运行“首次部署机器狗.cmd”。\n'+str(error))
        root.destroy()
        return
    root = tk.Tk()
    root.title('Go2 行走仿真 · 本机部署')
    root.geometry('510x390')
    status = tk.StringVar(value='准备启动本机的独立仿真环境。')
    tk.Label(root, text='Go2 行走仿真', font=('Microsoft YaHei UI',22,'bold')).pack(pady=(22,8))
    tk.Label(root, text='Ubuntu 24.04 · ROS2 Jazzy · Gazebo Harmonic · CHAMP').pack(pady=5)
    busy = False
    def run(action):
        nonlocal busy
        if busy:
            return
        busy = True
        status.set('正在处理，请稍候……')
        def work():
            nonlocal busy
            try:
                if action == 'start':
                    vm.start(config)
                    text = '已启动；等待行走面板显示关节 12/12、控制器激活，再操作。'
                elif action == 'stop':
                    vm.shutdown(config)
                    text = '已正常关闭，磁盘已释放。'
                else:
                    result = vm.remote(config, 'DISPLAY=:0 glxinfo -B', timeout=20)
                    text = next((line for line in result.stdout.splitlines() if 'OpenGL renderer string:' in line),
                                '尚未取得渲染器信息，请等待 Ubuntu 图形桌面启动。')
                root.after(0, lambda: status.set(text))
            except Exception as error:
                message = str(error)
                root.after(0, lambda: status.set(message))
            finally:
                busy = False
        threading.Thread(target=work, daemon=True).start()
    for label, action in [('启动行走环境','start'),('安全关闭虚拟机','stop'),('检查实际显卡','gpu')]:
        tk.Button(root, text=label, width=30, pady=6, command=lambda action=action:run(action)).pack(pady=5)
    tk.Label(root, text=f'{config["cpus"]} 核 · {config["memory_mib"]//1024}GB 内存\n按住方向按钮移动、松开停止；Ctrl+Alt+G 释放鼠标。').pack(pady=8)
    tk.Label(root, textvariable=status, wraplength=465).pack(pady=8)
    root.mainloop()

if __name__ == '__main__':
    main()
