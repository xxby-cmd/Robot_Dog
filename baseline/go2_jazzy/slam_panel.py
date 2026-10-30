#!/usr/bin/env python3
"""Live mapping controls alongside the original walking panel."""
import datetime
import fcntl
import math
import os
from pathlib import Path
import queue
import signal
import subprocess
import threading
import time
import tkinter as tk
from panel_surface import PanelSurface
from tkinter import filedialog

import numpy as np
import rclpy
from geometry_msgs.msg import Twist
from nav_msgs.msg import OccupancyGrid, Odometry
from sensor_msgs.msg import LaserScan
from rclpy.qos import QoSProfile, DurabilityPolicy
from tf2_ros import Buffer, TransformListener, TransformException
from slam_session import MAPS, save_session, load_session


def main(parent=None, compact=False):
    lock = (Path.home()/'.cache/robot-dog-slam-panel.lock').open('a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:
        return
    panel = PanelSurface(parent, 'Go2 · SLAM 建图', 760, 690)
    node = rclpy.create_node('robotdog_mapping_panel')
    stop_pub = node.create_publisher(Twist,'/cmd_vel',10)
    frames, received = {}, {}

    def remember(name, message):
        frames[name], received[name] = message, time.monotonic()

    node.create_subscription(OccupancyGrid,'/map',lambda msg:remember('map',msg),
        QoSProfile(depth=1,durability=DurabilityPolicy.TRANSIENT_LOCAL))
    node.create_subscription(LaserScan,'/slam/scan',lambda msg:remember('scan',msg),
                             rclpy.qos.qos_profile_sensor_data)
    node.create_subscription(Odometry,'/odom',lambda msg:remember('odom',msg),10)
    buffer = Buffer(node=node)
    listener = TransformListener(buffer,node)
    panel.attach(node)
    root = panel.root
    process, closing, busy = None, False, False
    messages = queue.Queue()
    status = tk.StringVar(value='先在行走面板启动传感器仿真，再点击“开始建图”。')
    detail = tk.StringVar(value='等待激光与里程计')
    tk.Label(root,text='SLAM 建图',font=('Noto Sans CJK SC',12 if compact else 18,'bold')).pack(pady=(3,1) if compact else (10,4))
    tk.Label(root,text='白：空地 · 黑：障碍 · 灰：未知 · 箭头：估计位置',font=('Noto Sans CJK SC',9)).pack()
    canvas = tk.Canvas(root,bg='#adb5bd',highlightthickness=0)
    footer = tk.Frame(root)
    footer.pack(side='bottom',fill='x',padx=6,pady=3)
    controls = tk.Frame(footer)
    controls.pack(fill='x',pady=2)
    buttons = []
    canvas.pack(fill='both',expand=True,padx=6,pady=3)
    last_map, photo = None, None

    def pose():
        transform = buffer.lookup_transform('map','base_footprint',rclpy.time.Time())
        p,q = transform.transform.translation,transform.transform.rotation
        return p.x,p.y,math.atan2(2*(q.w*q.z+q.x*q.y),1-2*(q.y*q.y+q.z*q.z))

    def stop_motion():
        for _ in range(3):
            stop_pub.publish(Twist())

    def work(operation):
        nonlocal busy
        if busy:
            return
        busy = True
        for button in buttons:
            button.configure(state='disabled')
        def run():
            worker = rclpy.create_node('robotdog_map_files_'+str(os.getpid()))
            try:
                messages.put(('status',operation(worker)))
            except Exception as error:
                messages.put(('status','操作未完成：'+str(error)))
            finally:
                worker.destroy_node()
                messages.put(('done',None))
        threading.Thread(target=run,daemon=True).start()

    def start():
        nonlocal process
        if process is not None and process.poll() is None:
            status.set('建图正在运行；使用左侧行走面板缓慢移动。')
            return
        if time.monotonic()-received.get('odom',0)>3:
            status.set('未收到新鲜里程计。请先启用传感器并启动行走仿真。')
            return
        # An external session is never silently replaced.
        if time.monotonic()-received.get('scan',0)<3:
            status.set('已有建图会话运行，当前窗口显示该会话。')
            return
        log = (Path.home()/'.ros/slam.log').open('a')
        process = subprocess.Popen([str(Path.home()/'bin/start-slam.sh')],
            stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        log.close()
        status.set('正在启动建图，请缓慢移动。' if compact else '正在等待扫描、坐标转换与地图；可在行走面板缓慢移动。')

    def stop():
        nonlocal process
        stop_motion()
        if process is None or process.poll() is not None:
            status.set('当前窗口未启动独立建图进程。')
            return
        current = process
        def operation(worker):
            os.kill(current.pid,signal.SIGINT)
            current.wait(timeout=30)
            return '建图已停止；已保存的地图保留，行走仿真仍可使用。'
        work(operation)

    def save():
        if time.monotonic()-received.get('map',0)>10 or 'map' not in frames:
            status.set('尚无新鲜地图，请先开始建图。')
            return
        try:
            current_pose = pose()
        except TransformException:
            status.set('尚无有效地图坐标，请等待建图就绪。')
            return
        stop_motion()
        directory = MAPS/datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        status.set('正在保存栅格地图和可继续建图的会话……')
        work(lambda worker:'已保存：'+str(save_session(worker,directory,current_pose)))

    def load():
        path = filedialog.askopenfilename(parent=root,title='在保存位置附近加载地图并继续建图',
            initialdir=str(MAPS),filetypes=[('建图会话','session.json')])
        if not path:
            return
        stop_motion()
        status.set('正在加载地图；机器狗需接近该会话保存时的位置和朝向……')
        work(lambda worker:'已加载，可继续建图：'+str(load_session(worker,path)))

    for index,(label,command) in enumerate([('开始建图',start),('保存地图',save),('加载并继续',load),('停止建图',stop)]):
        button = tk.Button(controls,text=label,command=command,width=11,pady=2 if compact else 7)
        if compact:
            button.grid(row=index//2,column=index%2,sticky='ew',padx=2,pady=1)
        else:
            button.pack(side='left',padx=4)
        buttons.append(button)
    controls.columnconfigure((0,1),weight=1)
    tk.Label(footer,textvariable=detail,wraplength=400).pack(fill='x',pady=2)
    tk.Label(footer,textvariable=status,wraplength=400,justify='left').pack(padx=12,pady=5)
    if not compact:
        tk.Label(footer,text='方向控制沿用行走面板；加载前先开始建图，并保持在保存位置附近。\n地图保存在本机 Ubuntu 的 ~/.local/share/robotdog/maps/',
             wraplength=720).pack(pady=(2,10))

    def resize(event):
        if event.widget is not root:
            return
        for widget in footer.winfo_children():
            if isinstance(widget,tk.Label):
                widget.configure(wraplength=max(160,event.width-16))
    root.bind('<Configure>',resize,add='+')

    def tick():
        nonlocal last_map,photo,busy
        for _ in range(60):
            panel.spin(1)
        while not messages.empty():
            kind,text = messages.get_nowait()
            if kind == 'status':
                status.set(text)
            else:
                busy = False
                for button in buttons:
                    button.configure(state='normal')
        if not busy:
            buttons[0].configure(state='normal' if time.monotonic()-received.get('odom',0)<3 else 'disabled')
        message = frames.get('map')
        if message is not None:
            if last_map is None and process is None:
                status.set('正在显示已有建图会话，可保存地图。' if compact else '正在显示已有建图会话；使用行走面板缓慢移动，可保存或加载地图。')
            width,height = canvas.winfo_width(),canvas.winfo_height()
            key = (id(message),width,height)
            if key != last_map and width>10 and height>10:
                data=np.asarray(message.data,dtype=np.int16).reshape(message.info.height,message.info.width)
                scale=min(width/message.info.width,height/message.info.height)
                w,h=max(1,int(message.info.width*scale)),max(1,int(message.info.height*scale))
                xi=np.minimum((np.arange(w)/scale).astype(int),message.info.width-1)
                yi=np.minimum((np.arange(h)/scale).astype(int),message.info.height-1)
                grey=np.where(data<0,173,255-(data*255//100)).astype(np.uint8)[::-1][yi[:,None],xi]
                rgb=np.repeat(grey[:,:,None],3,axis=2)
                photo=tk.PhotoImage(data=f'P6\n{w} {h}\n255\n'.encode()+rgb.tobytes(),format='PPM')
                canvas.delete('all')
                canvas.create_image(width/2,height/2,image=photo)
                last_map=key
            canvas.delete('robot')
            try:
                x,y,yaw=pose()
                info=message.info
                scale=min(width/info.width,height/info.height)
                ox=(width-info.width*scale)/2
                oy=(height-info.height*scale)/2
                # OccupancyGrid from SLAM Toolbox has an axis-aligned origin.
                px=ox+(x-info.origin.position.x)/info.resolution*scale
                py=oy+(info.height-(y-info.origin.position.y)/info.resolution)*scale
                canvas.create_line(px,py,px+16*math.cos(yaw),py-16*math.sin(yaw),
                    fill='#1769aa',width=4,arrow='last',tags='robot')
                position=f'估计位置 {x:.2f}, {y:.2f} 米'
            except TransformException:
                position='等待地图坐标'
            age=time.monotonic()-received['map']
            detail.set((f'{message.info.resolution:g} 米/格 · {position}' if compact else
                        f'{message.info.width}×{message.info.height} 格 · {message.info.resolution:g} 米/格 · '+
                        f'{position} · 地图更新于 {age:.1f} 秒前'))
        elif process is not None and process.poll() is not None:
            status.set('建图进程已退出，请查看 ~/.ros/slam.log；可重新开始。')
        if closing and not busy:
            if process is not None and process.poll() is None:
                stop()
                panel.after(100,tick)
                return
            panel.finish()
            lock.close()
            return
        panel.after(100,tick)

    def close():
        nonlocal closing
        stop_motion()
        if not busy and process is not None and process.poll() is None:
            stop()
        closing=True
    panel.after(100,tick)
    return panel.run(close)


if __name__ == '__main__':
    main()
