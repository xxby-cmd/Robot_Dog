"""Windows GUI for the independent ROS2 walking environment."""
import importlib.util
from pathlib import Path
import subprocess
import threading
import tkinter as tk
from tkinter import messagebox

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('go2_jazzy_vm', HERE / 'vm.py')
vm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vm)

def main():
    root = tk.Tk()
    root.title('Go2 行走仿真启动器')
    root.geometry('500x425')
    root.configure(bg='#f5f7fa')
    status = tk.StringVar(value='准备启动独立的机器狗行走环境。')
    tk.Label(root, text='Go2 行走仿真', font=('Microsoft YaHei UI', 22, 'bold'), bg='#f5f7fa').pack(pady=(22, 5))
    tk.Label(root, text='Ubuntu 24.04 · ROS2 Jazzy · CHAMP', bg='#f5f7fa', fg='#526071').pack(pady=(0, 18))

    def start():
        try:
            vm.start()
            status.set('虚拟机已启动。Ubuntu 登录后会自动打开行走面板。')
        except Exception as error:
            messagebox.showerror('启动失败', str(error))

    def shutdown():
        def work():
            try:
                root.after(0, lambda: status.set('正在保存并正常关闭 Ubuntu，请稍候……'))
                vm.shutdown()
                root.after(0, lambda: status.set('虚拟机已安全关闭。'))
            except (OSError, RuntimeError, subprocess.TimeoutExpired):
                root.after(0, lambda: status.set('尚未确认关机完成，请检查虚拟机窗口或等待 Ubuntu 启动后重试。'))
        threading.Thread(target=work, daemon=True).start()

    for text, callback in [('启动 Go2 行走环境', start), ('安全关闭虚拟机', shutdown),
                           ('查看启动日志', lambda: subprocess.Popen(['notepad.exe', str(vm.ROOT / 'qemu.log')]))]:
        tk.Button(root, text=text, command=callback, width=30, font=('Microsoft YaHei UI', 11), pady=8).pack(pady=5)
    tk.Label(root, text='在 Ubuntu 面板中按住方向按钮移动，松开停止。', bg='#f5f7fa', fg='#526071').pack(pady=(15, 0))
    tk.Label(root, text='8 核 · 12GB 内存 · 独显加速 · 流畅画面\n双击标题栏可最大化；Ctrl＋Alt＋G 释放鼠标。', bg='#f5f7fa', fg='#526071').pack(pady=3)
    tk.Label(root, textvariable=status, wraplength=440, bg='#f5f7fa', fg='#334155').pack(pady=10)
    root.mainloop()

if __name__ == '__main__':
    main()
