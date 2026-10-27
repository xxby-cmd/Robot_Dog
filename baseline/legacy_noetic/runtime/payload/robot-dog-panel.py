#!/usr/bin/env python3
import os
import fcntl
from pathlib import Path
import signal
import subprocess
import threading
import time
import tkinter as tk
from tkinter import messagebox

lock_path = Path.home() / '.cache' / 'robot-dog-panel.lock'
lock_path.parent.mkdir(exist_ok=True)
panel_lock = lock_path.open('w')
try:
    fcntl.flock(panel_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    raise SystemExit(0)
root = tk.Tk()
root.title('Go2 机器狗仿真')
root.geometry('390x420+20+40')
root.configure(bg='#f5f7fa')
root.attributes('-topmost', True)
status = tk.StringVar(value='准备就绪。点击启动仿真。')
sim = None
servo = None
stopping = False
ros_setup = 'source /opt/ros/noetic/setup.bash; source /home/robot/catkin_ws/devel/setup.bash; '
log_path = Path.home() / '.ros' / 'robot-dog-simulation.log'
log_path.parent.mkdir(exist_ok=True)

def update(text):
    root.after(0, lambda: status.set(text))

def command(text):
    return subprocess.run(['bash', '-c', ros_setup + text], capture_output=True, text=True, timeout=15)

def stand():
    global servo
    if stopping:
        update('正在停止，请稍候。')
        return
    if servo is not None and servo.poll() is None:
        update('站立控制正在运行。')
        return
    if sim is None or sim.poll() is not None:
        update('请先启动仿真。')
        return
    log = log_path.open('a')
    servo = subprocess.Popen(['bash', '-c', ros_setup + 'exec rosrun unitree_controller unitree_servo'],
                             stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    update('站立控制已启动。可在 Gazebo 中旋转和缩放视角。')

def wait_ready(process):
    for _ in range(120):
        if sim is not process:
            return
        if process.poll() is not None:
            update('启动失败。点击查看日志获取详情。')
            return
        try:
            result = command('rosservice call /go2_gazebo/controller_manager/list_controllers "{}"')
            if result.returncode == 0 and result.stdout.count('state: "running"') >= 13:
                root.after(0, stand)
                return
        except subprocess.TimeoutExpired:
            pass
        time.sleep(2)
    update('仿真仍在加载。可查看日志，或稍后点击站立。')

def start():
    global sim
    if stopping:
        update('正在停止，请稍候再启动。')
        return
    if sim is not None and sim.poll() is None:
        update('仿真正在运行。')
        return
    log = log_path.open('a')
    sim = subprocess.Popen(['/home/robot/bin/start-simulation.sh'], stdout=log,
                           stderr=subprocess.STDOUT, start_new_session=True)
    update('正在加载 Go2 模型与关节控制器……')
    threading.Thread(target=wait_ready, args=(sim,), daemon=True).start()

def stop():
    global sim, servo, stopping
    if stopping:
        return
    stopping = True
    processes = [proc for proc in (servo, sim) if proc is not None]
    for proc in processes:
        if proc is not None and proc.poll() is None:
            os.killpg(proc.pid, signal.SIGINT)
    update('正在停止仿真……')
    def finish():
        global sim, servo, stopping
        for proc in processes:
            try:
                proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGTERM)
                proc.wait(timeout=5)
        servo = None
        sim = None
        stopping = False
        update('仿真已停止。')
    threading.Thread(target=finish, daemon=True).start()

def reset():
    def work():
        try:
            result = command('rosservice call /gazebo/reset_world "{}"')
            update('场景已重置。' if result.returncode == 0 else '请先启动仿真。')
        except subprocess.TimeoutExpired:
            update('重置超时，请查看日志。')
    threading.Thread(target=work, daemon=True).start()

tk.Label(root, text='Go2 机器狗仿真', bg='#f5f7fa', font=('Noto Sans CJK SC', 17, 'bold')).pack(pady=(18, 4))
tk.Label(root, text='Ubuntu · ROS Noetic · Gazebo', bg='#f5f7fa', fg='#546174').pack(pady=(0, 12))
for text, callback in [('启动仿真', start), ('站立控制', stand), ('重置场景', reset), ('停止仿真', stop)]:
    tk.Button(root, text=text, command=callback, width=24, pady=3).pack(pady=3)
tk.Button(root, text='查看日志', command=lambda: subprocess.Popen(['xfce4-terminal', '-e', f'tail -n 100 -f {log_path}'])).pack(pady=3)
tk.Label(root, textvariable=status, wraplength=310, bg='#f5f7fa', fg='#334155').pack(pady=10)
def close_panel():
    stop()
    root.destroy()
root.protocol('WM_DELETE_WINDOW', close_panel)
root.after(4000, start)
root.mainloop()
