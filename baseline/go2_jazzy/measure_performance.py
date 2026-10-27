#!/usr/bin/env python3
"""Observe simulation speed and process CPU use without commanding motion."""
import argparse
import json
import math
import os
from pathlib import Path
import time

os.environ['ROS_DOMAIN_ID'] = '42'
import rclpy
from nav_msgs.msg import Odometry
from rclpy.qos import QoSProfile, ReliabilityPolicy

def processes():
    result = {}
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit():
            continue
        try:
            value = (entry/'stat').read_text()
            parts = value[value.rfind(')')+2:].split()
            result[entry.name] = ((entry/'comm').read_text().strip(), int(parts[11])+int(parts[12]))
        except (OSError, ValueError, IndexError):
            pass
    return result

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--seconds', type=float, default=10)
    parser.add_argument('--label', default='performance')
    args = parser.parse_args()
    rclpy.init()
    node = rclpy.create_node('go2_performance_observer')
    data = [None]
    def receive(message):
        data[0] = message
    node.create_subscription(Odometry, '/odom/ground_truth', receive,
                             QoSProfile(depth=1, reliability=ReliabilityPolicy.BEST_EFFORT))
    deadline = time.monotonic()+20
    while data[0] is None and time.monotonic() < deadline:
        rclpy.spin_once(node, timeout_sec=0.1)
    if data[0] is None:
        raise RuntimeError('No simulation odometry available')
    def stamp():
        value = data[0].header.stamp
        return value.sec+value.nanosec*1e-9
    initial = stamp()
    start = time.monotonic()
    before = processes()
    while time.monotonic()-start < args.seconds:
        rclpy.spin_once(node, timeout_sec=0.05)
    elapsed = time.monotonic()-start
    after = processes()
    ticks = os.sysconf('SC_CLK_TCK')
    cpu = [{'pid': pid, 'name': name, 'cpu_percent_one_core': round(100*(count-before[pid][1])/ticks/elapsed, 1)}
           for pid, (name,count) in after.items() if pid in before]
    cpu.sort(key=lambda item: item['cpu_percent_one_core'], reverse=True)
    q = data[0].pose.pose.orientation
    roll = math.atan2(2*(q.w*q.x+q.y*q.z), 1-2*(q.x*q.x+q.y*q.y))
    pitch = math.asin(max(-1, min(1, 2*(q.w*q.y-q.z*q.x))))
    report = {'label': args.label, 'wall_seconds': elapsed, 'simulation_seconds': stamp()-initial,
              'real_time_factor': (stamp()-initial)/elapsed, 'top_cpu_processes': cpu[:8],
              'body_height_m': data[0].pose.pose.position.z, 'roll_rad': roll, 'pitch_rad': pitch,
              'measurement_only': True}
    path = Path.home()/f'performance-{args.label}.json'
    path.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    node.destroy_node()
    rclpy.shutdown()

if __name__ == '__main__':
    main()
