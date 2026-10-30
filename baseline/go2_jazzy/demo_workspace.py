#!/usr/bin/env python3
"""One resizable live workspace for the original simulator and feature panels."""
import fcntl
import importlib.util
import os
from pathlib import Path
import signal
import subprocess
import time
import tkinter as tk
os.environ['ROS_DOMAIN_ID'] = '42'
import rclpy
from rclpy.executors import SingleThreadedExecutor
from rclpy.qos import qos_profile_sensor_data
from nav_msgs.msg import Odometry
from sensor_msgs.msg import Image
from rosgraph_msgs.msg import Clock
from sensor_measurements import centre_depth
from gazebo_viewport import GazeboViewport
import slam_panel
import vision_panel


def main():
    cache=Path.home()/'.cache'
    cache.mkdir(exist_ok=True)
    lock=(cache/'robotdog-demo-workspace.lock').open('a')
    try:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:
        return
    # Avoid acquiring only half the views when a standalone control owns them.
    probes=[]
    for name in ('robot-dog-jazzy-panel.lock','robot-dog-slam-panel.lock','robotdog-vision-panel.lock'):
        probe=(cache/name).open('a')
        try:
            fcntl.flock(probe,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            probe.close()
            for item in probes:
                item.close()
            raise SystemExit('请先关闭单独的行走、建图和检测面板，再打开演示总览。')
        probes.append(probe)
    for probe in probes:
        probe.close()
    rclpy.init()
    root=tk.Tk()
    root.title('Go2 · 演示总览')
    width,height=min(1440,root.winfo_screenwidth()-24),min(900,root.winfo_screenheight()-72)
    root.geometry(f'{width}x{height}+8+28')
    root.minsize(1000,640)
    root.configure(bg='#e8edf3')
    root.option_add('*Font',('Noto Sans CJK SC',10))
    toolbar=tk.Frame(root,bg='#11283f')
    toolbar.pack(fill='x')
    tk.Label(toolbar,text='Go2  仿真演示',font=('Noto Sans CJK SC',14,'bold'),bg='#11283f',fg='white').pack(side='left',padx=12,pady=7)
    view_status=tk.StringVar(value='正在等待三维场景……')
    tk.Label(toolbar,textvariable=view_status,bg='#11283f',fg='#d8e8f5').pack(side='left',padx=10)
    tk.Button(toolbar,text='全屏 / 退出全屏',command=lambda:root.attributes('-fullscreen',not bool(root.attributes('-fullscreen')))).pack(side='right',padx=8)
    root.bind('<F11>',lambda _:root.attributes('-fullscreen',not bool(root.attributes('-fullscreen'))))
    root.bind('<Escape>',lambda _:root.attributes('-fullscreen',False))
    status=tk.StringVar(value='等机器狗站稳后操作；建图和检测分别点击开始。')
    footer=tk.Label(root,textvariable=status,anchor='w',bg='#e8edf3',fg='#33465b',padx=10,pady=5)
    footer.pack(side='bottom',fill='x')
    panes=tk.PanedWindow(root,orient='horizontal',sashwidth=7,bg='#e8edf3',bd=0,opaqueresize=True)
    panes.pack(fill='both',expand=True,padx=6,pady=5)
    left,right=tk.Frame(panes),tk.PanedWindow(panes,orient='vertical',sashwidth=6,bg='#e8edf3',bd=0,opaqueresize=True)
    panes.add(left,minsize=590,stretch='always',width=int(width*.66))
    panes.add(right,minsize=360,stretch='always',width=int(width*.34))
    native=tk.Frame(left,bg='#192839',container=False)
    native.pack(fill='both',expand=True)
    waiting=tk.Label(native,text='正在加载真实三维仿真画面',bg='#192839',fg='white')
    waiting.place(relx=.5,rely=.5,anchor='center')
    controls=tk.Frame(left,bg='#f5f7fa')
    controls.pack(side='bottom',fill='x')
    map_host,vision_host=tk.Frame(right),tk.Frame(right)
    right.add(map_host,minsize=240,stretch='always',height=int(height*.43))
    right.add(vision_host,minsize=285,stretch='always',height=int(height*.48))
    spec=importlib.util.spec_from_file_location('robotdog_walking_panel',Path(__file__).with_name('robot-dog-panel.py'))
    walking=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(walking)
    views=[walking.main(controls,compact=True),slam_panel.main(map_host,compact=True),vision_panel.main(vision_host,compact=True)]
    tk.Button(toolbar,text='跟随机器狗',command=views[0].actions['follow']).pack(side='right',padx=4)
    children=[]
    def sensors():
        children[:]=[child for child in children if child.poll() is None]
        if children:
            status.set('详细传感器窗口已经打开，可从桌面任务栏切换。')
            return
        children.append(subprocess.Popen(['python3',str(Path.home()/'bin/sensor_viewer.py')]))
    tk.Button(toolbar,text='详细传感器',command=sensors).pack(side='right',padx=4)
    node=rclpy.create_node('robotdog_demo_metrics')
    executor=SingleThreadedExecutor()
    executor.add_node(node)
    metrics={}
    clock_samples=[]
    def clock(message):
        stamp=message.clock.sec+message.clock.nanosec*1e-9
        now=time.monotonic()
        if clock_samples and stamp<clock_samples[-1][1]:
            clock_samples.clear()
        clock_samples.append((now,stamp))
        while len(clock_samples)>2 and clock_samples[0][0]<now-5:
            clock_samples.pop(0)
    def odom(message):
        p=message.pose.pose.position
        metrics['odom']=(time.monotonic(),f'里程位置 {p.x:.2f}, {p.y:.2f} m')
    def depth(message):
        value=centre_depth(message)
        metrics['depth']=(time.monotonic(),f'相机中心距离 {value:.2f} m' if value is not None else '相机中心无有效深度')
    node.create_subscription(Clock,'/clock',clock,qos_profile_sensor_data)
    node.create_subscription(Odometry,'/odom',odom,qos_profile_sensor_data)
    node.create_subscription(Image,'/d455/depth_image',depth,qos_profile_sensor_data)
    viewport=GazeboViewport()
    closing=False
    detached=False
    def close():
        nonlocal closing
        if closing:
            return
        closing=True
        status.set('正在停止本窗口启动的检测、建图和行走，请稍候……')
        viewport.detach()
        for child in children:
            if child.poll() is None:
                child.terminate()
        for view in reversed(views):
            if view:
                view.close()
    def separate():
        nonlocal detached
        detached=not detached
        if detached:
            viewport.detach()
            view_status.set('三维画面已独立；再次点击可放回总览')
        else:
            view_status.set('正在放回三维画面……')
    tk.Button(toolbar,text='三维独立 / 放回',command=separate).pack(side='right',padx=4)
    def tick():
        if closing:
            if not controls.winfo_children() and not map_host.winfo_children() and not vision_host.winfo_children() and all(child.poll() is not None for child in children):
                viewport.close()
                executor.remove_node(node)
                node.destroy_node()
                executor.shutdown(timeout_sec=0)
                rclpy.shutdown()
                lock.close()
                root.destroy()
                return
        else:
            for _ in range(12):
                executor.spin_once(timeout_sec=0)
            if not detached:
                try:
                    attached=viewport.attach(native.winfo_id(),native.winfo_width(),native.winfo_height())
                    if attached:
                        waiting.place_forget()
                        view_status.set('三维实景 · 实时建图 · 相机检测')
                except RuntimeError as error:
                    viewport.detach()
                    view_status.set(str(error))
            now=time.monotonic()
            fields=[]
            if len(clock_samples)>1 and now-clock_samples[-1][0]<3:
                first,last=clock_samples[0],clock_samples[-1]
                fields.append(f'仿真倍率 {(last[1]-first[1])/max(.01,last[0]-first[0]):.2f}×')
            else:
                fields.append('等待仿真时钟')
            for key in ('odom','depth'):
                fields.append(metrics[key][1] if key in metrics and now-metrics[key][0]<3 else ('等待里程计' if key=='odom' else '等待相机深度'))
            status.set('   |   '.join(fields))
        root.after(200,tick)
    root.protocol('WM_DELETE_WINDOW',close)
    signal.signal(signal.SIGTERM,lambda *_:root.after_idle(close))
    signal.signal(signal.SIGINT,lambda *_:root.after_idle(close))
    root.after(500,tick)
    root.mainloop()


if __name__=='__main__':
    main()
