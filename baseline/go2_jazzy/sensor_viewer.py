#!/usr/bin/env python3
"""Read-only ROS sensor display. Images and points come from live subscriptions."""
import math
import os
import signal
import time
import tkinter as tk
import numpy as np
os.environ['ROS_DOMAIN_ID'] = '42'
import rclpy
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import Image, PointCloud2
from sensor_msgs_py import point_cloud2
from nav_msgs.msg import Odometry
from tf2_ros import Buffer, TransformListener
from sensor_measurements import body_points, centre_depth, depth_array, forward_clearance


def main():
    rclpy.init()
    node = rclpy.create_node('go2_sensor_viewer', parameter_overrides=[rclpy.parameter.Parameter('use_sim_time', value=True)])
    frames, received = {}, {}
    def remember(name, message):
        frames[name] = message
        received[name] = time.monotonic()
    for name, kind, topic in [('rgb', Image, '/d455/image'), ('depth', Image, '/d455/depth_image'),
                              ('cloud', PointCloud2, '/unitree_lidar/points'), ('odom', Odometry, '/odom')]:
        node.create_subscription(kind, topic, lambda msg, name=name: remember(name, msg), qos_profile_sensor_data)
    buffer = Buffer(node=node)
    listener = TransformListener(buffer, node)
    root = tk.Tk()
    root.title('Go2 传感器 · 实时数据')
    signal.signal(signal.SIGTERM, lambda *_: root.after(0, root.destroy))
    signal.signal(signal.SIGINT, lambda *_: root.after(0, root.destroy))
    root.geometry('730x680+440+30')
    labels = []
    for index, title in enumerate(['彩色图像', '深度图（近红、远蓝；黑色无效）']):
        tk.Label(root, text=title).grid(row=0, column=index)
        label = tk.Label(root, text='等待数据', width=320, height=240)
        label.grid(row=1, column=index)
        labels.append(label)
    # Explicit pixel-sized frames prevent Tk text character units making huge windows.
    for label in labels:
        label.configure(width=0, height=0)
    canvas = tk.Canvas(root, width=680, height=260, bg='#14202e')
    canvas.grid(row=2, column=0, columnspan=2, pady=10)
    status = tk.StringVar(value='等待 /odom 与传感器……')
    distances = tk.StringVar(value='距离：等待有效测量……')
    tk.Label(root,textvariable=distances,font=('Noto Sans CJK SC',11,'bold'),wraplength=700).grid(row=3,column=0,columnspan=2,pady=4)
    tk.Label(root, textvariable=status, justify='left', wraplength=700).grid(row=4, column=0, columnspan=2)
    tk.Label(root, text='点云上方为前方；激光仅显示前方 0.5 米宽区域的回波，盲区或无回波不代表安全。',wraplength=700).grid(row=5, column=0, columnspan=2)
    last = {}
    clearance = None
    depth_distance = None

    def draw():
        nonlocal clearance,depth_distance
        for _ in range(30):
            rclpy.spin_once(node, timeout_sec=0)
        for name, label in zip(('rgb', 'depth'), labels):
            message = frames.get(name)
            if message is None or last.get(name) is message:
                continue
            last[name] = message
            stride = max(1, message.width//320)
            if name == 'rgb' and message.encoding == 'rgb8':
                image = np.frombuffer(bytes(message.data), dtype=np.uint8).reshape(message.height, message.step)[:, :message.width*3].reshape(message.height, message.width, 3)[::stride, ::stride].copy()
            elif name == 'depth' and message.encoding == '32FC1':
                values = depth_array(message)[::stride, ::stride]
                depth_distance = centre_depth(message)
                valid = np.isfinite(values) & (values > 0)
                scaled = np.clip(np.nan_to_num(values, nan=10, posinf=10, neginf=0)/10, 0, 1)
                image = np.zeros((*values.shape, 3), np.uint8)
                image[..., 0] = ((1-scaled)*255).astype(np.uint8)
                image[..., 2] = (scaled*255).astype(np.uint8)
                image[~valid] = 0
            else:
                label.configure(text='不支持的图像编码：'+message.encoding)
                continue
            height, width = image.shape[:2]
            photo = tk.PhotoImage(data=f'P6\n{width} {height}\n255\n'.encode()+image.tobytes(), format='PPM')
            label.configure(image=photo)
            label.image = photo
        cloud = frames.get('cloud')
        if cloud is not None and last.get('cloud') is not cloud:
            last['cloud'] = cloud
            canvas.delete('all')
            canvas.create_line(340, 10, 340, 250, fill='#415263')
            canvas.create_line(100, 130, 580, 130, fill='#415263')
            try:
                transform = buffer.lookup_transform('base_link', cloud.header.frame_id,
                    rclpy.time.Time.from_msg(cloud.header.stamp))
                points = point_cloud2.read_points_numpy(cloud, field_names=('x','y','z'), skip_nans=True).reshape(-1,3)
                points = points[np.isfinite(points).all(axis=1)]
                points = body_points(points,transform.transform)
                clearance = forward_clearance(points)
                points = points[::8]
                points = points[(points[:,2] > -0.15) & (np.linalg.norm(points[:,:2], axis=1) < 10)]
                for x, y, z in points:
                    px, py = 340-y*12, 130-x*12
                    canvas.create_oval(px, py, px+2, py+2, fill='#51e0b5', outline='')
                canvas.create_polygon(340, 118, 333, 137, 347, 137, fill='#ffcf66')
            except Exception as error:
                clearance = None
                canvas.create_text(340, 130, text='等待点云坐标变换：'+str(error)[:65], fill='white')
        def value(name,distance):
            if time.monotonic()-received.get(name,0)>3:return '等待/超时'
            return f'{distance:.2f} m' if distance is not None else '无有效回波'
        distances.set('相机中心光轴距离：'+value('depth',depth_distance)+'\n'
                      '激光前方障碍距头部：'+value('cloud',clearance))
        odom = frames.get('odom')
        freshness = '  '.join(f'{name}：'+('在线' if time.monotonic()-received.get(name, 0)<3 else '等待/超时') for name in ('rgb','depth','cloud','odom'))
        if odom is not None:
            p, q = odom.pose.pose.position, odom.pose.pose.orientation
            heading = math.atan2(2*(q.w*q.z+q.x*q.y),1-2*(q.y*q.y+q.z*q.z))
            freshness += f'\n里程计  x={p.x:.3f} m  y={p.y:.3f} m  高度={p.z:.3f} m  朝向={math.degrees(heading):.1f}°'
        status.set(freshness)
        root.after(200, draw)
    root.after(100, draw)
    try:
        root.mainloop()
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
