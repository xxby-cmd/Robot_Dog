#!/usr/bin/env python3
"""Graphical teleoperation for the CHAMP controller inside the Jazzy VM."""
import fcntl
import math
import os
from pathlib import Path
import signal
import subprocess
import threading
import time
import tkinter as tk
from tkinter.scrolledtext import ScrolledText

os.environ['ROS_DOMAIN_ID'] = '42'
import rclpy
from geometry_msgs.msg import Twist
from sensor_msgs.msg import JointState, Imu
from controller_manager_msgs.srv import ListControllers

lock_file = Path.home() / '.cache' / 'robot-dog-jazzy-panel.lock'
lock_file.parent.mkdir(exist_ok=True)
lock_handle = lock_file.open('w')
try:
    fcntl.flock(lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    raise SystemExit(0)

rclpy.init()
node = rclpy.create_node('go2_graphical_teleop')
publisher = node.create_publisher(Twist, '/cmd_vel', 10)
controller_client = node.create_client(ListControllers, '/controller_manager/list_controllers')
root = tk.Tk()
root.title('Go2 行走控制 · ROS2 CHAMP')
root.geometry('410x580+10+35')
root.attributes('-topmost', True)
root.configure(bg='#f5f7fa')
status = tk.StringVar(value='准备启动行走仿真。')
sensor_status = tk.StringVar(value='等待关节与 IMU 数据……')
process = None
stopping = False
target = (0.0, 0.0)
pressed_at = 0.0
limit_reached = False
key_release_job = None
stop_until = 0.0
joint_count = 0
last_joint_time = 0.0
controller_ready = False
controller_since = 0.0
controller_future = None
next_controller_query = 0.0
roll = pitch = 0.0
log_path = Path.home() / '.ros' / 'robot-dog-jazzy.log'
log_path.parent.mkdir(exist_ok=True)

def joints_received(message):
    global joint_count, last_joint_time
    joint_count = len(message.name)
    last_joint_time = time.monotonic()

def imu_received(message):
    global roll, pitch
    q = message.orientation
    roll = math.atan2(2*(q.w*q.x+q.y*q.z), 1-2*(q.x*q.x+q.y*q.y))
    pitch = math.asin(max(-1.0, min(1.0, 2*(q.w*q.y-q.z*q.x))))

node.create_subscription(JointState, '/joint_states', joints_received, 10)
node.create_subscription(Imu, '/imu/data', imu_received, rclpy.qos.qos_profile_sensor_data)

def send(forward, turn):
    command = Twist()
    command.linear.x = forward
    command.angular.z = turn
    publisher.publish(command)

def halt(event=None):
    global target, stop_until, limit_reached
    target = (0.0, 0.0)
    limit_reached = False
    stop_until = time.monotonic() + 0.3
    send(0.0, 0.0)

def move(forward, turn):
    global target, pressed_at
    if limit_reached:
        status.set('已达 10 秒，请松开后重新按住。')
        return
    if stopping or not controller_ready or time.monotonic()-controller_since < 2 or time.monotonic() - last_joint_time > 2:
        status.set('控制器尚未就绪，请等待关节数据。')
        return
    if abs(roll) > 0.45 or abs(pitch) > 0.45:
        status.set('机身倾斜，请停止仿真后重新启动。')
        return
    if target != (forward, turn):
        pressed_at = time.monotonic()
    target = (forward, turn)
    status.set('按住移动，松开停止。空格也可停止。')

def start():
    global process
    if stopping:
        return
    if process is not None and process.poll() is None:
        status.set('仿真正在运行。等待机器狗站稳后，按住方向按钮。')
        return
    log = log_path.open('a')
    process = subprocess.Popen(['/home/robot/bin/start-simulation.sh'], stdout=log,
                               stderr=subprocess.STDOUT, start_new_session=True)
    log.close()
    status.set('正在加载 Go2、物理场景和 CHAMP 控制器……')

def stop():
    global stopping
    halt()
    if process is None or process.poll() is not None or stopping:
        return
    stopping = True
    status.set('正在停止仿真……')
    current = process
    os.killpg(current.pid, signal.SIGINT)
    def wait_exit():
        global process, stopping
        try:
            current.wait(timeout=25)
        except subprocess.TimeoutExpired:
            os.killpg(current.pid, signal.SIGTERM)
            current.wait(timeout=8)
        process = None
        stopping = False
        root.after(0, lambda: status.set('仿真已停止。'))
    threading.Thread(target=wait_exit, daemon=True).start()

def logs():
    window = tk.Toplevel(root)
    window.title('Go2 仿真日志')
    area = ScrolledText(window, width=95, height=28)
    area.pack(fill='both', expand=True)
    area.insert('end', log_path.read_text(errors='replace')[-24000:] if log_path.exists() else '暂无日志')
    area.see('end')

def focus_robot():
    def work():
        try:
            subprocess.run(['python3', '/home/robot/bin/focus_robot.py'],
                           check=True, timeout=20, capture_output=True)
            root.after(0, lambda: status.set('观察视角已跟随机器狗。'))
        except (subprocess.SubprocessError, OSError):
            root.after(0, lambda: status.set('视角暂未就绪，请等待仿真加载后重试。'))
    threading.Thread(target=work, daemon=True).start()

tk.Label(root, text='Go2 行走控制', font=('Noto Sans CJK SC', 19, 'bold'), bg='#f5f7fa').pack(pady=(15, 3))
tk.Label(root, text='ROS2 Jazzy · Gazebo Harmonic · CHAMP', bg='#f5f7fa').pack(pady=(0, 12))
tk.Button(root, text='启动仿真', command=start, width=26, pady=6).pack(pady=4)
pad = tk.Frame(root, bg='#f5f7fa')
pad.pack(pady=10)
for text, row, col, velocity in [
    ('前进 ↑', 0, 1, (0.15, 0.0)), ('左转 ←', 1, 0, (0.0, 0.25)),
    ('右转 →', 1, 2, (0.0, -0.25)), ('后退 ↓', 2, 1, (-0.12, 0.0)),
]:
    button = tk.Button(pad, text=text, width=10, height=2)
    button.grid(row=row, column=col, padx=3, pady=3)
    button.bind('<ButtonPress-1>', lambda event, velocity=velocity: move(*velocity))
    button.bind('<ButtonRelease-1>', halt)
tk.Button(pad, text='停止', command=halt, width=10, height=2, bg='#ffd9d9').grid(row=1, column=1)
tk.Label(root, text='按住按钮或方向键移动；松开即停止\n空格停止，连续按住最多 10 秒', bg='#f5f7fa').pack(pady=4)
tk.Label(root, textvariable=sensor_status, bg='#f5f7fa', fg='#526071').pack(pady=5)

tk.Button(root, text='居中并跟随机器狗', command=focus_robot, width=26).pack(pady=4)
tk.Button(root, text='停止仿真', command=stop, width=26).pack(pady=4)
tk.Button(root, text='查看日志', command=logs, width=26).pack(pady=4)
tk.Label(root, textvariable=status, wraplength=360, bg='#f5f7fa', fg='#334155').pack(pady=10)
def key_pressed(velocity):
    global key_release_job
    if key_release_job is not None:
        root.after_cancel(key_release_job)
        key_release_job = None
    move(*velocity)

def key_released(event):
    global key_release_job
    # X11 auto-repeat emits a release immediately before the next press.
    key_release_job = root.after(40, halt)

for key, velocity in {'Up':(0.15,0.0), 'Down':(-0.12,0.0), 'Left':(0.0,0.25), 'Right':(0.0,-0.25)}.items():
    root.bind(f'<KeyPress-{key}>', lambda event, velocity=velocity: key_pressed(velocity))
    root.bind(f'<KeyRelease-{key}>', key_released)
root.bind('<space>', halt)
def check_focus():
    focused = root.focus_displayof()
    if focused is None or focused.winfo_toplevel() != root:
        halt()

root.bind('<FocusOut>', lambda event: root.after_idle(check_focus))

def tick():
    global controller_ready, controller_since, controller_future, next_controller_query, limit_reached
    for _ in range(4):
        rclpy.spin_once(node, timeout_sec=0)
    now = time.monotonic()
    if controller_future is not None and controller_future.done():
        try:
            active = any(item.name == 'joint_group_effort_controller' and item.state == 'active'
                         for item in controller_future.result().controller)
            if active and not controller_ready:
                controller_since = now
                status.set('控制器已激活，站稳后可按住方向按钮。')
            controller_ready = active
        except Exception:
            controller_ready = False
        controller_future = None
    if now >= next_controller_query and controller_future is None and controller_client.service_is_ready():
        controller_future = controller_client.call_async(ListControllers.Request())
        next_controller_query = now+1
    if target != (0.0, 0.0) and now - pressed_at > 10:
        halt()
        limit_reached = True
        status.set('已达 10 秒，请松开后重新按住。')
    if target != (0.0, 0.0) and (now - last_joint_time > 2
                               or not controller_ready or abs(roll) > 0.45 or abs(pitch) > 0.45):
        halt()
    if target != (0.0, 0.0) or now < stop_until:
        send(*target)
    fresh = now - last_joint_time < 2
    sensor_status.set(f'关节：{joint_count if fresh else 0}/12    横滚：{math.degrees(roll):.1f}°    俯仰：{math.degrees(pitch):.1f}°')
    root.after(50, tick)

def close():
    halt()
    stop()
    if stopping:
        root.after(200, close)
        return
    node.destroy_node()
    rclpy.shutdown()
    root.destroy()

root.protocol('WM_DELETE_WINDOW', close)
root.after(50, tick)
root.after(4000, start)
root.mainloop()
