"""Windows GUI for the independent ROS2 walking environment."""
import importlib.util
from pathlib import Path
import subprocess
import threading
import tkinter as tk
import queue

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('go2_jazzy_vm', HERE / 'vm.py')
vm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vm)

def main():
    root = tk.Tk()
    root.title('Go2 行走仿真启动器')
    root.geometry('520x520')
    root.configure(bg='#f5f7fa')
    status = tk.StringVar(value='准备启动独立的机器狗行走环境。')
    tk.Label(root, text='Go2 行走仿真', font=('Microsoft YaHei UI', 22, 'bold'), bg='#f5f7fa').pack(pady=(22, 5))
    tk.Label(root, text='Ubuntu 24.04 · ROS2 Jazzy · CHAMP', bg='#f5f7fa', fg='#526071').pack(pady=(0, 18))
    busy = False
    messages = queue.Queue()
    buttons = []
    verification_cancel = threading.Event()

    def start(display_backend='sdl'):
        nonlocal busy
        if busy:
            return
        busy = True
        for button in buttons[:3]:
            button.configure(state='disabled')
        verification_cancel.clear()
        status.set('正在应用独显配置并启动，随后核对实际显卡……')
        def work():
            try:
                vm.start(display_backend=display_backend, progress=lambda text: messages.put(('status', text)))
                messages.put(('status', '正在等待 Ubuntu，并检查实际渲染器……'))
                gpu = vm.wait_for_gpu(cancel_event=verification_cancel)
                if verification_cancel.is_set():
                    return
                text = ('实际显卡：RTX 4060，独显验证通过。Ubuntu 将自动打开行走面板。'
                        if gpu['status']=='pass' else
                        '检测到核显或软件渲染。请安全关闭虚拟机后重新启动；未按独显验证通过。')
                messages.put(('status', text))
            except Exception as error:
                message = str(error)
                if not verification_cancel.is_set():
                    messages.put(('status', '启动或显卡核对未完成：'+message))
            finally:
                messages.put(('done', None))
        threading.Thread(target=work, daemon=True).start()

    def shutdown():
        nonlocal busy
        if busy:
            return
        busy = True
        for button in buttons[:3]:
            button.configure(state='disabled')
        verification_cancel.set()
        def work():
            try:
                messages.put(('status', '正在保存并正常关闭 Ubuntu，请稍候……'))
                vm.shutdown()
                messages.put(('status', '虚拟机已安全关闭。'))
            except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
                messages.put(('status', '尚未确认关机完成：'+str(error)))
            finally:
                messages.put(('done', None))
        threading.Thread(target=work, daemon=True).start()

    for text, callback in [('启动 Go2 行走环境', start), ('兼容显示启动（黑屏时尝试）', lambda:start('gtk')), ('安全关闭虚拟机', shutdown),
                           ('查看启动日志', lambda: subprocess.Popen(['notepad.exe', str(vm.ROOT / 'qemu.log')]))]:
        button = tk.Button(root, text=text, command=callback, width=30, font=('Microsoft YaHei UI', 11), pady=8)
        button.pack(pady=5)
        buttons.append(button)
    tk.Label(root, text='在 Ubuntu 面板中按住方向按钮移动，松开停止。', bg='#f5f7fa', fg='#526071').pack(pady=(15, 0))
    tk.Label(root, text='8 核 · 12GB 内存 · 启动后核对实际显卡\n双击标题栏可最大化；Ctrl＋Alt＋G 释放鼠标。', bg='#f5f7fa', fg='#526071').pack(pady=3)
    tk.Label(root, textvariable=status, wraplength=440, bg='#f5f7fa', fg='#334155').pack(pady=10)
    def close():
        verification_cancel.set()
        root.destroy()
    root.protocol('WM_DELETE_WINDOW', close)
    def poll():
        nonlocal busy
        while not messages.empty():
            kind, text = messages.get_nowait()
            if kind == 'status':
                status.set(text)
            elif kind == 'done':
                busy = False
                for button in buttons[:3]:
                    button.configure(state='normal')
        root.after(100, poll)
    root.after(100, poll)
    root.mainloop()

if __name__ == '__main__':
    main()
