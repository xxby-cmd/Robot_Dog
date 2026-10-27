"""Local graphical launcher for the isolated Robot Dog virtual machine."""
from pathlib import Path
import json
import socket
import subprocess
import threading
import time
import tkinter as tk
from tkinter import messagebox

ROOT = Path(__file__).resolve().parent
QEMU = ROOT / 'qemu' / 'qemu-system-x86_64.exe'
MONITOR_PORT = 2256
SSH = r'C:\Windows\System32\OpenSSH\ssh.exe'
SSH_ARGS = [SSH, '-i', str(ROOT / 'robot_vm_key'), '-p', '2255',
            '-o', 'StrictHostKeyChecking=accept-new',
            '-o', f'UserKnownHostsFile={ROOT / "known_hosts"}',
            '-o', 'ConnectTimeout=5', 'robot@127.0.0.1']

def qmp(execute, arguments=None):
    with socket.create_connection(('127.0.0.1', MONITOR_PORT), timeout=3) as sock:
        stream = sock.makefile('rwb', buffering=0)
        stream.readline()
        stream.write(b'{"execute":"qmp_capabilities"}\n')
        while 'return' not in json.loads(stream.readline()):
            pass
        request = {'execute': execute}
        if arguments is not None:
            request['arguments'] = arguments
        stream.write(json.dumps(request).encode() + b'\n')
        while True:
            result = json.loads(stream.readline())
            if 'return' in result or 'error' in result:
                return result

def running():
    try:
        return 'return' in qmp('query-status')
    except (OSError, ValueError):
        return False

def installing():
    marker = ROOT / 'installing'
    return marker.exists() and marker.read_text(encoding='utf-8').strip() != 'complete'

def start_vm():
    if running():
        return False
    args = [str(QEMU), '-name', 'Robot Dog - Ubuntu ROS Gazebo',
            '-accel', 'whpx', '-machine', 'pc', '-smp', '4', '-m', '6144',
            '-drive', f'file={ROOT / "robot-dog.qcow2"},format=qcow2,if=virtio',
            '-drive', f'file={ROOT / "robot-dog-seed.iso"},media=cdrom,readonly=on',
            '-netdev', 'user,id=net0,hostfwd=tcp:127.0.0.1:2255-:22',
            '-device', 'virtio-net-pci,netdev=net0',
            '-device', 'virtio-vga-gl', '-device', 'qemu-xhci', '-device', 'usb-tablet',
            '-display', 'sdl,gl=on',
            '-serial', f'file:{ROOT / "serial.log"}',
            '-qmp', f'tcp:127.0.0.1:{MONITOR_PORT},server=on,wait=off',
            '-rtc', 'base=utc']
    (ROOT / 'vm-command.json').write_text(json.dumps(args, indent=2), encoding='utf-8')
    with (ROOT / 'qemu.log').open('a', encoding='utf-8') as log:
        subprocess.Popen(args, cwd=QEMU.parent, stdout=log, stderr=subprocess.STDOUT,
                         creationflags=subprocess.CREATE_NO_WINDOW)
    return True

def main():
    window = tk.Tk()
    window.title('机器狗仿真启动器')
    window.geometry('480x360')
    window.configure(bg='#f5f7fa')
    state = tk.StringVar(value='正在检查虚拟机状态……')
    tk.Label(window, text='机器狗仿真', font=('Microsoft YaHei UI', 22, 'bold'), bg='#f5f7fa').pack(pady=(22, 5))
    tk.Label(window, text='独立 Ubuntu 环境 · Go2 · ROS · Gazebo', bg='#f5f7fa', fg='#526071').pack(pady=(0, 18))

    def start():
        try:
            state.set('正在启动，Ubuntu 桌面将在独立窗口中打开……' if start_vm() else '虚拟机已经运行，请切换到 Ubuntu 窗口。')
        except Exception as error:
            messagebox.showerror('启动失败', str(error))

    def shutdown():
        if installing():
            state.set('首次环境安装正在进行，请保持虚拟机运行。完成后会自动打开控制面板。')
            return
        def power_off():
            try:
                result = subprocess.run(SSH_ARGS + ['sudo -n systemctl poweroff'],
                                        capture_output=True, timeout=10,
                                        creationflags=subprocess.CREATE_NO_WINDOW)
                if result.returncode not in (0, 255):
                    qmp('system_powerdown')
                window.after(0, lambda: state.set('已发送正常关机请求，正在等待 Ubuntu 保存并关闭。'))
            except (OSError, subprocess.TimeoutExpired):
                window.after(0, lambda: state.set('暂时无法连接 Ubuntu，请等待启动完成后重试。'))
        threading.Thread(target=power_off, daemon=True).start()

    def show_logs():
        subprocess.Popen(['notepad.exe', str(ROOT / 'qemu.log')])

    for text, action in [('启动机器狗虚拟机', start), ('安全关闭虚拟机', shutdown), ('查看启动日志', show_logs)]:
        tk.Button(window, text=text, command=action, width=30, font=('Microsoft YaHei UI', 11), pady=8).pack(pady=5)
    tk.Label(window, textvariable=state, wraplength=430, bg='#f5f7fa', fg='#334155').pack(pady=18)
    if installing():
        state.set('首次环境安装正在进行。请保持虚拟机运行，完成后会自动打开控制面板。')
    elif running():
        state.set('虚拟机正在运行。请在 Ubuntu 桌面点击“机器狗仿真”。')
    else:
        state.set('准备就绪。点击上方按钮启动。')
    def refresh_status():
        if installing():
            state.set('首次环境安装正在进行。请保持虚拟机运行，完成后会自动打开控制面板。')
        elif running():
            state.set('虚拟机已运行。Ubuntu 桌面中的控制面板会自动启动 Go2 仿真。')
        else:
            state.set('虚拟机已关闭。点击上方按钮可以再次启动。')
        window.after(5000, refresh_status)
    window.after(5000, refresh_status)
    window.mainloop()

if __name__ == '__main__':
    main()
