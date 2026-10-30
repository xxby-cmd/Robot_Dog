#!/usr/bin/env python3
"""Optional live detection window, independent of walking and SLAM controls."""
import argparse
import fcntl
import json
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
os.environ['ROS_DOMAIN_ID'] = '42'
import rclpy
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import Image
from std_msgs.msg import String
from yolo_runtime import rgb_array


def main(parent=None, compact=False):
    parser = argparse.ArgumentParser(description='Go2 YOLOv10 camera viewer')
    parser.add_argument('--start',action='store_true',help='Start detection after opening the window')
    options = parser.parse_args() if parent is None else argparse.Namespace(start=False)
    cache = Path.home()/'.cache'
    cache.mkdir(exist_ok=True)
    handle = (cache/'robotdog-vision-panel.lock').open('w')
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return
    panel = PanelSurface(parent, 'Go2 · YOLOv10 目标检测', 700, 690)
    node = rclpy.create_node('go2_yolo_viewer')
    panel.attach(node)
    root = panel.root
    status = tk.StringVar(value='先启动含相机的行走仿真，再点击“开始检测”。')
    confidence = tk.StringVar(value='0.25')
    frequency = tk.StringVar(value='5')
    heading = tk.Frame(root)
    heading.pack(fill='x',padx=8,pady=(4,2))
    tk.Label(heading,text='目标检测',font=('Noto Sans CJK SC',12 if compact else 17,'bold')).pack(side='left')
    form = tk.Frame(root)
    def parameters():
        if form.winfo_manager():
            form.pack_forget()
        else:
            form.pack(after=heading,fill='x',padx=5,pady=3)
    tk.Button(heading,text='参数',command=parameters,padx=6).pack(side='right')
    if not compact:
        form.pack(fill='x',padx=8,pady=3)
    for label, variable in [('置信度', confidence), ('最高帧/秒', frequency)]:
        tk.Label(form, text=label).pack(side='left',padx=3)
        tk.Entry(form,textvariable=variable,width=6).pack(side='left',padx=3)
    summary = tk.StringVar(value='等待检测画面')
    # Reserve controls first so resizing cannot push the stop button off-screen.
    footer = tk.Frame(root)
    footer.pack(side='bottom',fill='x',padx=6,pady=3)
    tk.Label(footer,textvariable=status,justify='left',wraplength=390,fg='#35516a').pack(fill='x',pady=2)
    actions = tk.Frame(footer)
    actions.pack(fill='x')
    demos = tk.Frame(footer)
    demos.pack(fill='x',pady=2)
    tk.Label(footer,textvariable=summary,justify='left',wraplength=390,anchor='w').pack(fill='x',pady=2)
    canvas = tk.Canvas(root,bg='#182332',highlightthickness=0,height=180 if compact else 360)
    canvas.pack(fill='both',expand=True,padx=6,pady=3)
    canvas.create_text(150,90,text='等待相机检测画面',fill='white',tags='picture')
    def resized(event):
        if event.widget is not root:
            return
        for widget in footer.winfo_children():
            if isinstance(widget,tk.Label):
                widget.configure(wraplength=max(150,event.width-14))
    root.bind('<Configure>',resized,add='+')
    process = None
    stopping = False
    closing = False
    last_picture = None
    last_image = 0.0
    image = None
    rendered_picture = None
    rendered_size = None
    logs = Path.home()/'.ros/yolo.log'
    logs.parent.mkdir(exist_ok=True)
    scene_process = None
    scene_busy = False
    scene_results = queue.SimpleQueue()

    def picture(message):
        nonlocal last_picture, last_image
        last_picture, last_image = message, time.monotonic()

    def report(message):
        try:
            data = json.loads(message.data)
            result = data.get('last_result') or {}
            boxes = result.get('boxes', [])
            if data.get('stale'):
                summary.set('等待新的检测结果；旧结果不再作为当前识别。')
                if process is not None and not stopping and not scene_busy:
                    status.set('等待相机或新的检测结果，请确认含相机的仿真正在运行。')
            elif data.get('error'):
                summary.set('本帧检测未完成，可查看检测日志。')
                status.set('检测遇到错误，详情见 ~/.ros/yolo.log。')
            else:
                names = [b['label']+' '+format(b['score'], '.2f')+
                         ('（备选：'+', '.join(h['label']+' '+format(h['score'],'.2f') for h in b.get('hypotheses',[])[1:])+'）'
                          if len(b.get('hypotheses',[]))>1 else '') for b in boxes]
                summary.set((' · '.join(names) if names else '当前帧未检出达到置信度的目标')+
                            f'\n已处理 {data["processed"]} 帧 · 推理 {result.get("inference_ms",0):.0f} 毫秒')
                if process is not None and not stopping and not scene_busy:
                    status.set('正在检测。修改参数后，停止并重新开始即可应用。')
        except (ValueError, KeyError, TypeError):
            summary.set('检测状态暂时不可读。')

    node.create_subscription(Image, '/yolo/image', picture, qos_profile_sensor_data)
    node.create_subscription(String, '/yolo/status', report, 1)

    def start():
        nonlocal process
        if process is not None and process.poll() is None:
            return
        try:
            score, rate = float(confidence.get()), float(frequency.get())
            if not math.isfinite(score) or not 0 <= score <= 1 or not math.isfinite(rate) or rate <= 0:
                raise ValueError()
        except ValueError:
            status.set('请输入0～1的置信度概率，以及正数处理频率。')
            return
        with logs.open('w') as log:
            process = subprocess.Popen(['bash', str(Path.home()/'bin/start-vision.sh'),
                '--confidence', str(score), '--max-fps', str(rate)], stdout=log,
                stderr=subprocess.STDOUT, start_new_session=True)
        status.set('正在准备并启动检测；首次使用会自动下载校验模型与独立推理环境。')
        begin.configure(state='disabled')

    def stop():
        nonlocal stopping
        if process is not None and process.poll() is None and not stopping:
            stopping = True
            os.killpg(process.pid, signal.SIGINT)
            status.set('正在停止检测，行走和建图保持独立。')

    def close():
        nonlocal closing
        closing = True
        stop()
        if scene_process is not None and scene_process.poll() is None:
            try:
                os.killpg(scene_process.pid, signal.SIGINT)
            except ProcessLookupError:
                pass

    def scene(action):
        nonlocal scene_busy
        if scene_busy:
            return
        scene_busy = True
        status.set('正在准备示例目标……' if action == 'add' else '正在移除示例目标……')
        add_scene.configure(state='disabled')
        remove_scene.configure(state='disabled')
        def work():
            nonlocal scene_process
            try:
                child = scene_process = subprocess.Popen(['python3',str(Path.home()/'bin/vision_scene.py'),action],
                    stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,start_new_session=True)
                if closing:
                    os.killpg(child.pid,signal.SIGINT)
                try:
                    output, error = child.communicate(timeout=210)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid,signal.SIGINT)
                    output, error = child.communicate(timeout=12)
                message = output.strip() if child.returncode == 0 else '示例操作未完成，请查看检测日志。'
                if child.returncode:
                    with logs.open('a') as log:
                        log.write(output+error)
            except (OSError,subprocess.TimeoutExpired):
                message = '示例模型尚未准备好，请确认网络与仿真状态。'
            finally:
                scene_process = None
            scene_results.put(message)
        threading.Thread(target=work,daemon=True).start()

    begin = tk.Button(actions, text='开始检测', width=12, pady=2 if compact else 7, command=start)
    begin.pack(side='left',padx=3,expand=True,fill='x')
    tk.Button(actions, text='停止检测', width=12, pady=2 if compact else 7, command=stop).pack(side='left',padx=3,expand=True,fill='x')
    add_scene = tk.Button(demos,text='添加示例椅子',width=16,command=lambda:scene('add'))
    add_scene.pack(side='left',padx=3,expand=True,fill='x')
    remove_scene = tk.Button(demos,text='移除示例椅子',width=16,command=lambda:scene('remove'))
    remove_scene.pack(side='left',padx=3,expand=True,fill='x')

    def tick():
        nonlocal process, stopping, last_picture, image, scene_busy, rendered_picture, rendered_size
        for _ in range(20):
            panel.spin(1)
        size = (max(1,canvas.winfo_width()),max(1,canvas.winfo_height()))
        if last_picture is not None or (rendered_picture is not None and size != rendered_size):
            if last_picture is not None:
                rendered_picture = last_picture
            pixels = rgb_array(rendered_picture)
            from PIL import Image as PILImage, ImageTk
            display = PILImage.fromarray(pixels)
            display.thumbnail(size)
            image = ImageTk.PhotoImage(display)
            canvas.delete('picture')
            canvas.create_image(size[0]/2,size[1]/2,image=image,tags='picture')
            last_picture = None
            rendered_size = size
        if last_image and time.monotonic()-last_image > 2:
            rendered_picture = None
            canvas.delete('picture')
            canvas.create_text(size[0]/2,size[1]/2,text='等待新的检测画面',fill='white',tags='picture')
        if process is not None and process.poll() is not None:
            code = process.returncode
            process = None
            status.set('检测已停止。' if stopping or code == 0 else '检测未能启动或已退出，详情见 ~/.ros/yolo.log。')
            stopping = False
            begin.configure(state='normal')
        while not scene_results.empty():
            message = scene_results.get()
            scene_busy = False
            status.set(message)
            add_scene.configure(state='normal')
            remove_scene.configure(state='normal')
        if closing and process is None and not scene_busy:
            panel.finish()
            handle.close()
            return
        panel.after(70, tick)

    panel.after(70, tick)
    if options.start:
        panel.after(300,start)
    return panel.run(close)


if __name__ == '__main__':
    main()
