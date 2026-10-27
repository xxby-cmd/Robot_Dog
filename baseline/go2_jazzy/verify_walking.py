#!/usr/bin/env python3
"""Measure actual simulated body motion; ground truth is used only for evaluation."""
import csv
import json
import math
import os
from pathlib import Path
import time

os.environ['ROS_DOMAIN_ID'] = '42'
import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy
from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry
from sensor_msgs.msg import Imu, JointState

def yaw(q):
    return math.atan2(2*(q.w*q.z+q.x*q.y), 1-2*(q.y*q.y+q.z*q.z))

def angle_difference(a, b):
    return math.atan2(math.sin(a-b), math.cos(a-b))

class Verification(Node):
    def __init__(self):
        super().__init__('go2_walking_verification')
        self.publisher = self.create_publisher(Twist, '/cmd_vel', 10)
        self.odom = self.imu = self.joints = None
        self.received = {}
        self.trace = []
        qos = QoSProfile(depth=1, reliability=ReliabilityPolicy.BEST_EFFORT)
        for name, kind, topic in [('odom', Odometry, '/odom/ground_truth'),
                                   ('imu', Imu, '/imu/data'), ('joints', JointState, '/joint_states')]:
            def callback(message, name=name):
                setattr(self, name, message)
                self.received[name] = time.monotonic()
            self.create_subscription(kind, topic, callback, qos)

    def pump(self):
        for _ in range(20):
            rclpy.spin_once(self, timeout_sec=0)

    def sample(self):
        assert all(getattr(self, name) is not None for name in ('odom', 'imu', 'joints')), 'Missing sensor data'
        assert all(time.monotonic()-t < 2 for t in self.received.values()), 'Stale sensor data'
        pose = self.odom.pose.pose
        q = self.imu.orientation
        stamp = self.odom.header.stamp
        return {'t':stamp.sec+stamp.nanosec*1e-9, 'x':pose.position.x, 'y':pose.position.y,
                'z':pose.position.z, 'yaw':yaw(pose.orientation),
                'roll':math.atan2(2*(q.w*q.x+q.y*q.z), 1-2*(q.x*q.x+q.y*q.y)),
                'pitch':math.asin(max(-1.0,min(1.0,2*(q.w*q.y-q.z*q.x))))}

    def command(self, forward=0.0, turn=0.0):
        message = Twist()
        message.linear.x = forward
        message.angular.z = turn
        self.publisher.publish(message)

    def phase(self, name, seconds, forward=0.0, turn=0.0, publish=True):
        first = self.sample()
        started = time.monotonic()
        while True:
            self.pump()
            if publish:
                self.command(forward, turn)
            sample = self.sample()
            self.trace.append({'phase':name, **sample, 'command_x':forward if publish else '',
                               'command_yaw':turn if publish else ''})
            assert all(math.isfinite(value) for value in sample.values()), 'Non-finite body state'
            assert sample['z'] > 0.14 and sample['z'] < 0.65, f'Body height out of range: {sample}'
            assert abs(sample['roll']) < 0.45 and abs(sample['pitch']) < 0.45, f'Body tilted: {sample}'
            if sample['t'] - first['t'] >= seconds:
                print(name, sample, flush=True)
                return first, sample
            if time.monotonic()-started > 150:
                raise TimeoutError('Simulation is too slow or its clock is not advancing')
            time.sleep(0.05)

def main():
    rclpy.init()
    node = Verification()
    report = {'result':'FAIL', 'measurement_source':'Gazebo ground truth for evaluation only; no pose commands',
              'controller':'CHAMP', 'tests':{}}
    evaluation_started = None
    try:
        deadline = time.monotonic()+90
        while any(getattr(node, name) is None for name in ('odom','imu','joints')):
            node.pump()
            if time.monotonic() > deadline:
                raise TimeoutError(f'Missing topics: {node.received.keys()}')
            time.sleep(0.05)
        assert len(node.joints.name) == 12
        assert all(math.isfinite(value) for value in node.joints.position)
        evaluation_started = time.monotonic()
        node.phase('settle', 3)
        a, b = node.phase('forward', 5, forward=0.15)
        dx, dy = b['x']-a['x'], b['y']-a['y']
        forward = math.cos(a['yaw'])*dx + math.sin(a['yaw'])*dy
        report['tests']['forward_distance_m'] = forward
        assert forward > 0.20, 'Forward command did not produce forward body motion'
        node.phase('stop_settle', 2)
        a, b = node.phase('stop_hold', 2)
        drift = math.hypot(b['x']-a['x'], b['y']-a['y'])
        report['tests']['stop_drift_m'] = drift
        assert drift < 0.10, 'Robot kept moving after stop'
        a, b = node.phase('turn_left', 4, turn=0.25)
        turn = angle_difference(b['yaw'], a['yaw'])
        report['tests']['left_turn_rad'] = turn
        assert turn > 0.25, 'Turn command did not produce left yaw rotation'
        node.phase('turn_stop_settle', 2)
        a, b = node.phase('reverse', 3, forward=-0.12)
        reverse = math.cos(a['yaw'])*(b['x']-a['x']) + math.sin(a['yaw'])*(b['y']-a['y'])
        report['tests']['reverse_distance_m'] = reverse
        assert reverse < -0.10, 'Reverse command did not produce backward body motion'
        node.phase('reverse_stop_settle', 2)
        a, b = node.phase('turn_right', 4, turn=-0.25)
        right = angle_difference(b['yaw'], a['yaw'])
        report['tests']['right_turn_rad'] = right
        assert right < -0.25, 'Right turn command did not produce right yaw rotation'
        node.phase('right_stop_settle', 2)
        node.phase('watchdog_command', 3, forward=0.15)
        node.phase('watchdog_settle', 2, publish=False)
        a, b = node.phase('watchdog_hold', 2, publish=False)
        watchdog_drift = math.hypot(b['x']-a['x'], b['y']-a['y'])
        report['tests']['watchdog_drift_m'] = watchdog_drift
        assert watchdog_drift < 0.10, 'Robot kept moving after command stream ended'
        report['tests']['joint_count'] = len(node.joints.name)
        report['tests']['min_body_height_m'] = min(row['z'] for row in node.trace)
        report['tests']['max_abs_roll_rad'] = max(abs(row['roll']) for row in node.trace)
        report['tests']['max_abs_pitch_rad'] = max(abs(row['pitch']) for row in node.trace)
        report['result'] = 'PASS'
    except Exception as error:
        report['error'] = str(error)
    finally:
        for _ in range(5):
            node.command()
            node.pump()
            time.sleep(0.05)
        output = Path.home() / 'walking-verification.json'
        output.write_text(json.dumps(report, indent=2))
        if node.trace:
            report['simulation_seconds'] = node.trace[-1]['t']-node.trace[0]['t']
            report['wall_seconds'] = time.monotonic()-evaluation_started
            report['real_time_factor'] = report['simulation_seconds']/report['wall_seconds']
            output.write_text(json.dumps(report, indent=2))
            with (Path.home()/'walking-telemetry.csv').open('w', newline='') as stream:
                writer = csv.DictWriter(stream, fieldnames=node.trace[0].keys())
                writer.writeheader()
                writer.writerows(node.trace)
        print(json.dumps(report, indent=2))
        node.destroy_node()
        rclpy.shutdown()
    return 0 if report['result'] == 'PASS' else 1

if __name__ == '__main__':
    raise SystemExit(main())
