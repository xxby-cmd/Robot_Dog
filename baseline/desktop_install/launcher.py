"""GUI for the configured desktop instance; no laptop path assumptions."""
import argparse
from pathlib import Path
import threading
import tkinter as tk
from tkinter import messagebox
import queue
import subprocess
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
    root.geometry('530x530')
    status = tk.StringVar(value='准备启动本机的独立仿真环境。')
    tk.Label(root, text='Go2 行走仿真', font=('Microsoft YaHei UI',22,'bold')).pack(pady=(22,8))
    tk.Label(root, text='Ubuntu 24.04 · ROS2 Jazzy · Gazebo Harmonic · CHAMP').pack(pady=5)
    busy = False
    messages = queue.Queue()
    cancelled = threading.Event()
    buttons = []
    def run(action):
        nonlocal busy
        if busy:
            return
        busy = True
        cancelled.clear()
        for button in buttons:
            button.configure(state='disabled')
        status.set('正在处理，请稍候……')
        def work():
            try:
                if action in ('start', 'compatible'):
                    selected = dict(config, display='gtk') if action == 'compatible' else config
                    vm.start(selected, progress=lambda text:messages.put(('status', text)))
                    text = vm.wait_for_graphics(selected, progress=lambda text:messages.put(('status', text)),
                                                cancel_event=cancelled)
                elif action == 'stop':
                    vm.shutdown(config)
                    text = '已正常关闭，磁盘已释放。'
                else:
                    result = vm.remote(config, 'DISPLAY=:0 glxinfo -B', timeout=20)
                    text = next((line for line in result.stdout.splitlines() if 'OpenGL renderer string:' in line),
                                '尚未取得渲染器信息，请等待 Ubuntu 图形桌面启动。')
                messages.put(('status', text))
            except Exception as error:
                message = str(error)
                messages.put(('status', message))
            finally:
                messages.put(('done', None))
        threading.Thread(target=work, daemon=True).start()
    for label, action in [('启动行走环境','start'),('兼容显示启动（黑屏时尝试）','compatible'),('安全关闭虚拟机','stop'),('检查实际显卡','gpu')]:
        button = tk.Button(root, text=label, width=30, pady=6, command=lambda action=action:run(action))
        button.pack(pady=5)
        buttons.append(button)
    tk.Button(root, text='查看启动检查记录', width=30, pady=6,
              command=lambda:subprocess.Popen(['notepad.exe',str(Path(config['runtime'])/'jazzy/startup-events.jsonl')])).pack(pady=5)
    tk.Label(root, text=f'{config["cpus"]} 核 · {config["memory_mib"]//1024}GB 内存\n按住方向按钮移动、松开停止；Ctrl+Alt+G 释放鼠标。').pack(pady=8)
    tk.Label(root, textvariable=status, wraplength=465).pack(pady=8)
    def poll():
        nonlocal busy
        while not messages.empty():
            kind, text = messages.get_nowait()
            if kind == 'status':
                status.set(text)
            elif kind == 'done':
                busy = False
                for button in buttons:
                    button.configure(state='normal')
        root.after(100, poll)
    def close():
        cancelled.set()
        root.destroy()
    root.protocol('WM_DELETE_WINDOW', close)
    root.after(100, poll)
    root.mainloop()

if __name__ == '__main__':
    main()
