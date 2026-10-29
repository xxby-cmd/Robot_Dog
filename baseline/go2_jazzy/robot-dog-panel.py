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
import teleop_settings

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
root.title('Go2 行走与传感器 · ROS2 CHAMP')
root.geometry('430x735+10+5')
root.attributes('-topmost', True)
root.configure(bg='#f5f7fa')
status = tk.StringVar(value='准备启动行走仿真。')
with_sensors = tk.BooleanVar(value=True)
sensor_status = tk.StringVar(value='等待关节与 IMU 数据……')
gpu_status = tk.StringVar(value='显卡：等待实际检测')
gpu_verified = False
gpu_checking = False
gpu_attempts = 0
process = None
stopping = False
target = (0.0, 0.0)
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
try:
    speeds = teleop_settings.load()
except (OSError, ValueError, KeyError, TypeError):
    speeds = dict(teleop_settings.DEFAULTS)
    status.set('速度配置无法读取，当前使用默认值；可在“速度调参”中重新保存。')
speed_status = tk.StringVar()
settings_window = None

def refresh_speeds():
    speed_status.set(f'前进 {speeds["forward"]:g} m/s · 后退 {speeds["reverse"]:g} m/s\n'
                     f'转向 {speeds["turn"]:g} rad/s')

refresh_speeds()

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
    global target, stop_until
    target = (0.0, 0.0)
    stop_until = time.monotonic() + 0.3
    send(0.0, 0.0)

def move(forward, turn):
    global target
    if stopping or not controller_ready or time.monotonic()-controller_since < 2 or time.monotonic() - last_joint_time > 2:
        status.set('控制器尚未就绪，请等待关节数据。')
        return
    if abs(roll) > 0.45 or abs(pitch) > 0.45:
        status.set('机身倾斜，请停止仿真后重新启动。')
        return
    target = (forward, turn)
    status.set('按住移动，松开停止。空格也可停止。')

def move_action(action):
    move(*teleop_settings.command(action, speeds))

def tune_speeds():
    global settings_window
    halt()
    if settings_window is not None and settings_window.winfo_exists():
        settings_window.lift()
        return
    window = settings_window = tk.Toplevel(root)
    window.title('行走速度调参')
    window.geometry('440x360')
    window.transient(root)
    window.configure(bg='#f5f7fa')
    tk.Label(window, text='行走速度', font=('Noto Sans CJK SC', 16, 'bold'), bg='#f5f7fa').pack(pady=(16, 10))
    fields = {}
    form = tk.Frame(window, bg='#f5f7fa')
    form.pack(padx=20, fill='x')
    for row, (key, label, unit) in enumerate([
        ('forward', '前进速度', 'm/s'), ('reverse', '后退速度', 'm/s'), ('turn', '转向角速度', 'rad/s')
    ]):
        tk.Label(form, text=label, bg='#f5f7fa').grid(row=row, column=0, sticky='w', pady=9)
        value = fields[key] = tk.StringVar(value=str(speeds[key]))
        tk.Entry(form, textvariable=value, width=15).grid(row=row, column=1, padx=12)
        tk.Label(form, text=unit, bg='#f5f7fa').grid(row=row, column=2, sticky='w')
    feedback = tk.StringVar(value='数值不设上下限，可填零或负数；负值反转对应方向。保存后立即生效。')
    tk.Label(window, textvariable=feedback, wraplength=390, bg='#f5f7fa', fg='#334155').pack(pady=(12, 5))
    def apply():
        global speeds
        try:
            updated = teleop_settings.save({key: value.get() for key, value in fields.items()})
        except (ValueError, OSError) as error:
            feedback.set('未保存：请输入有限数字，并确认配置可写入。')
            return
        halt()
        speeds = updated
        for key, value in fields.items():
            value.set(str(updated[key]))
        refresh_speeds()
        feedback.set('已保存并生效。机器狗保持停止，重新按住方向即可移动。')
        status.set('行走速度已更新。')
    def defaults():
        for key, value in teleop_settings.DEFAULTS.items():
            fields[key].set(str(value))
        feedback.set('已填入默认值，点击“保存并应用”后生效。')
    actions = tk.Frame(window, bg='#f5f7fa')
    actions.pack(pady=10)
    tk.Button(actions, text='恢复默认值', command=defaults, width=12, pady=7).pack(side='left', padx=6)
    tk.Button(actions, text='保存并应用', command=apply, width=14, pady=7).pack(side='left', padx=6)

def start():
    global process, gpu_checking, gpu_attempts
    if stopping:
        return
    if process is not None and process.poll() is None:
        status.set('仿真正在运行。等待机器狗站稳后，按住方向按钮。')
        return
    if not gpu_verified:
        if gpu_checking:
            return
        gpu_checking = True
        gpu_attempts += 1
        status.set('正在核对实际渲染显卡……')
        def check():
            try:
                result = subprocess.run(['glxinfo', '-B'], capture_output=True, text=True, timeout=10)
                renderer = next((line.split(':',1)[1].strip() for line in result.stdout.splitlines()
                                 if line.startswith('OpenGL renderer string:')), '')
                valid = result.returncode == 0 and 'NVIDIA' in renderer and 'Accelerated: yes' in result.stdout
            except (OSError, subprocess.SubprocessError):
                renderer, valid = '', False
            root.after(0, lambda: gpu_checked(renderer, valid))
        threading.Thread(target=check, daemon=True).start()
        return
    log = log_path.open('a')
    entry = 'start-sensors.sh' if with_sensors.get() else 'start-simulation.sh'
    process = subprocess.Popen(['/home/robot/bin/'+entry], stdout=log,
                               stderr=subprocess.STDOUT, start_new_session=True)
    log.close()
    status.set('正在加载 Go2、物理场景和 CHAMP 控制器……')

def gpu_checked(renderer, valid):
    global gpu_verified, gpu_checking
    gpu_checking = False
    gpu_verified = valid
    if valid:
        gpu_status.set('显卡：' + renderer)
        start()
    else:
        if not renderer:
            gpu_status.set('显卡：等待图形服务响应')
            if gpu_attempts < 6:
                status.set('图形服务尚未就绪，稍后自动重新核对……')
                root.after(2000, start)
            else:
                status.set('显卡检测超时，请稍候再点击“启动仿真”。')
            return
        gpu_status.set('显卡：未通过独显核对')
        status.set('未检测到 NVIDIA 独显加速。请在 Windows 启动器中安全关闭并重新启动虚拟机。'+
                   ('当前为 Intel 核显。' if 'Intel' in renderer else ''))

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

tk.Label(root, text='Go2 行走控制', font=('Noto Sans CJK SC', 19, 'bold'), bg='#f5f7fa').pack(pady=(10, 3))
tk.Label(root, text='ROS2 Jazzy · Gazebo Harmonic · CHAMP', bg='#f5f7fa').pack(pady=(0, 7))
tk.Label(root, textvariable=gpu_status, wraplength=400, bg='#f5f7fa', fg='#526071').pack(pady=(0, 4))
tk.Label(root, textvariable=speed_status, bg='#f5f7fa', fg='#526071').pack(pady=3)
tk.Button(root, text='速度调参', command=tune_speeds, width=26, pady=4).pack(pady=2)
tk.Button(root, text='启动仿真', command=start, width=26, pady=4).pack(pady=2)
tk.Checkbutton(root, text='启用激光、深度相机与里程计（重启仿真生效）', variable=with_sensors, bg='#f5f7fa').pack()
tk.Button(root, text='查看实时传感器', width=26,
          command=lambda: subprocess.Popen(['python3', '/home/robot/bin/sensor_viewer.py'])).pack(pady=2)
tk.Button(root, text='SLAM 建图与地图保存', width=26,
          command=lambda: subprocess.Popen(['python3', '/home/robot/bin/slam_panel.py'])).pack(pady=2)
pad = tk.Frame(root, bg='#f5f7fa')
pad.pack(pady=6)
for text, row, col, action in [
    ('前进 ↑', 0, 1, 'forward'), ('左转 ←', 1, 0, 'left'),
    ('右转 →', 1, 2, 'right'), ('后退 ↓', 2, 1, 'reverse'),
]:
    button = tk.Button(pad, text=text, width=10, height=2)
    button.grid(row=row, column=col, padx=3, pady=3)
    button.bind('<ButtonPress-1>', lambda event, action=action: move_action(action))
    button.bind('<ButtonRelease-1>', halt)
tk.Button(pad, text='停止', command=halt, width=10, height=2, bg='#ffd9d9').grid(row=1, column=1)
tk.Label(root, text='按住按钮或方向键移动；松开即停止\n空格停止，移开控制窗口也会停止', bg='#f5f7fa').pack(pady=2)
tk.Label(root, textvariable=sensor_status, bg='#f5f7fa', fg='#526071').pack(pady=3)

tk.Button(root, text='居中并跟随机器狗', command=focus_robot, width=26).pack(pady=2)
tk.Button(root, text='停止仿真', command=stop, width=26).pack(pady=2)
tk.Button(root, text='查看日志', command=logs, width=26).pack(pady=2)
tk.Label(root, textvariable=status, wraplength=360, bg='#f5f7fa', fg='#334155').pack(pady=5)
def key_pressed(action):
    global key_release_job
    if key_release_job is not None:
        root.after_cancel(key_release_job)
        key_release_job = None
    move_action(action)

def key_released(event):
    global key_release_job
    # X11 auto-repeat emits a release immediately before the next press.
    key_release_job = root.after(40, halt)

for key, action in {'Up':'forward', 'Down':'reverse', 'Left':'left', 'Right':'right'}.items():
    root.bind(f'<KeyPress-{key}>', lambda event, action=action: key_pressed(action))
    root.bind(f'<KeyRelease-{key}>', key_released)
root.bind('<space>', halt)
def check_focus():
    focused = root.focus_displayof()
    if focused is None or focused.winfo_toplevel() != root:
        halt()

root.bind('<FocusOut>', lambda event: root.after_idle(check_focus))

def tick():
    global controller_ready, controller_since, controller_future, next_controller_query
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
signal.signal(signal.SIGTERM, lambda *_: root.after_idle(close))
signal.signal(signal.SIGINT, lambda *_: root.after_idle(close))
root.after(50, tick)
root.after(4000, start)
root.mainloop()
