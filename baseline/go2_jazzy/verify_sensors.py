#!/usr/bin/env python3
"""Validate live sensor/TF chains, optionally move the physical robot to evaluate odometry."""
import argparse
from collections import defaultdict
import csv
import json
import math
import os
from pathlib import Path
import signal
import time
import numpy as np
os.environ['ROS_DOMAIN_ID'] = '42'
import rclpy
from rclpy.node import Node
from rclpy.parameter import Parameter
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import Image, CameraInfo, PointCloud2, Imu, JointState
from sensor_msgs_py import point_cloud2
from rosgraph_msgs.msg import Clock
from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry
from champ_msgs.msg import ContactsStamped
from tf2_ros import Buffer, TransformListener


def seconds(stamp):
    return stamp.sec + stamp.nanosec * 1e-9


def yaw(q):
    return math.atan2(2*(q.w*q.z+q.x*q.y), 1-2*(q.y*q.y+q.z*q.z))


def delta(a, b):
    return math.atan2(math.sin(a-b), math.cos(a-b))


class Verification(Node):
    def __init__(self):
        super().__init__('go2_sensor_verification', parameter_overrides=[Parameter('use_sim_time', value=True)])
        self.latest, self.received = {}, {}
        self.stamps = defaultdict(list)
        self.nonmonotonic = defaultdict(int)
        self.trace = []
        self.interrupted = False
        self.topics = [
            ('cloud', PointCloud2, '/unitree_lidar/points'),
            ('rgb', Image, '/d455/image'), ('depth', Image, '/d455/depth_image'),
            ('camera_info', CameraInfo, '/d455/camera_info'),
            ('imu', Imu, '/imu/data'), ('joints', JointState, '/joint_states'),
            ('contacts', ContactsStamped, '/foot_contacts'), ('leg', Odometry, '/odom/leg'),
            ('odom', Odometry, '/odom'), ('truth', Odometry, '/odom/ground_truth'),
            ('clock', Clock, '/clock')]
        for name, kind, topic in self.topics:
            self.create_subscription(kind, topic, lambda msg, name=name: self.remember(name, msg), qos_profile_sensor_data)
        self.command_pub = self.create_publisher(Twist, '/cmd_vel', 10)
        self.buffer = Buffer(node=self)
        self.listener = TransformListener(self.buffer, self)

    def remember(self, name, msg):
        stamp = seconds(msg.clock if name == 'clock' else msg.header.stamp)
        if self.stamps[name] and stamp < self.stamps[name][-1]-1e-8:
            self.nonmonotonic[name] += 1
        self.stamps[name].append(stamp)
        self.latest[name] = msg
        self.received[name] = time.monotonic()

    def pump(self):
        if self.interrupted:
            raise InterruptedError('Verification interrupted; stopping')
        # Bounded drain: image traffic must not starve command/watchdog updates.
        for _ in range(70):
            rclpy.spin_once(self, timeout_sec=0)

    def command(self, forward=0.0, turn=0.0):
        msg = Twist()
        msg.linear.x, msg.angular.z = float(forward), float(turn)
        self.command_pub.publish(msg)

    def sample(self):
        out = {'t': seconds(self.latest['clock'].clock)}
        for name in ('odom', 'truth'):
            pose = self.latest[name].pose.pose
            out.update({name+'_x': pose.position.x, name+'_y': pose.position.y,
                        name+'_z': pose.position.z, name+'_yaw': yaw(pose.orientation)})
        assert all(math.isfinite(value) for value in out.values()), 'Non-finite odometry'
        return out

    def phase(self, name, duration, forward=0.0, turn=0.0):
        self.pump()
        start = self.sample()
        deadline = time.monotonic()+180
        while True:
            self.command(forward, turn)
            self.pump()
            current = self.sample()
            self.trace.append({'phase': name, **current})
            for key in ('odom', 'truth', 'imu', 'joints'):
                assert time.monotonic()-self.received[key] < 4, 'Stale '+key
            q = self.latest['imu'].orientation
            roll = math.atan2(2*(q.w*q.x+q.y*q.z), 1-2*(q.x*q.x+q.y*q.y))
            pitch = math.asin(max(-1, min(1, 2*(q.w*q.y-q.z*q.x))))
            assert abs(roll)<0.45 and abs(pitch)<0.45, 'Body tilted; stopping'
            if current['t']-start['t'] >= duration:
                print(name, json.dumps(current), flush=True)
                return start, current
            if time.monotonic() > deadline:
                raise TimeoutError('Clock stalled / simulation too slow')
            time.sleep(0.025)

    def check_streams(self):
        expected = {'cloud': 'lidar_l1_link', 'rgb': 'd455_camera_optical_frame',
                    'depth': 'd455_camera_optical_frame', 'camera_info': 'd455_camera_optical_frame',
                    'imu': 'imu_link', 'odom': 'odom', 'leg': 'odom'}
        minimum_hz = {'cloud': 5, 'rgb': 6, 'depth': 6, 'camera_info': 6,
                      'imu': 40, 'joints': 30, 'contacts': 40, 'leg': 25, 'odom': 20}
        report = {}
        for name, kind, topic in self.topics:
            stamps = np.array(self.stamps[name])
            assert len(stamps) > 3 and stamps[-1]>stamps[0], 'No advancing stream: '+topic
            elapsed = stamps[-1]-stamps[0]
            hz = (len(stamps)-1)/elapsed
            assert not self.nonmonotonic[name], 'Time moved backwards: '+topic
            assert time.monotonic()-self.received[name] < 4, 'Stale '+topic
            assert abs(stamps[-1]-seconds(self.latest['clock'].clock)) < 0.4, 'Clock mismatch: '+topic
            frame = '' if name=='clock' else self.latest[name].header.frame_id
            if name in expected:
                assert frame == expected[name], f'{topic}: wrong frame {frame}'
            if name in minimum_hz:
                assert hz >= minimum_hz[name], f'{topic}: only {hz:.2f} sim Hz'
            report[topic] = {'messages': len(stamps), 'sim_hz': hz, 'frame': frame,
                              'nonmonotonic': self.nonmonotonic[name]}
        rgb, depth, info = [self.latest[key] for key in ('rgb','depth','camera_info')]
        assert rgb.encoding=='rgb8' and depth.encoding=='32FC1'
        assert (rgb.width,rgb.height)==(depth.width,depth.height)==(info.width,info.height)
        assert (rgb.width,rgb.height) in ((320,240),(640,480)), 'Unexpected camera resolution'
        assert info.k[0]>0 and info.k[4]>0 and 0<info.k[2]<info.width and 0<info.k[5]<info.height
        assert abs(seconds(rgb.header.stamp)-seconds(info.header.stamp))<0.15
        colors = np.frombuffer(bytes(rgb.data), np.uint8)
        assert np.std(colors)>5, 'Blank/constant RGB image'
        depths = np.frombuffer(bytes(depth.data), '>f4' if depth.is_bigendian else '<f4')
        valid = depths[np.isfinite(depths) & (depths>0)]
        assert len(valid)>depth.width*depth.height*0.02, 'Empty depth image'
        points = point_cloud2.read_points_numpy(self.latest['cloud'], field_names=('x','y','z'), skip_nans=True)
        points = points[np.isfinite(points).all(axis=1)]
        assert len(points)>100, 'Empty lidar cloud'
        assert len(self.latest['joints'].name)==12
        assert self.latest['odom'].child_frame_id=='base_link'
        checks = {}
        for target in ('lidar_l1_link','d455_camera_optical_frame','imu_link'):
            # A buffered past timestamp proves a time-valid dynamic chain, not only static connectivity.
            stamp = rclpy.time.Time(seconds=seconds(self.latest['clock'].clock)-0.25)
            transform = self.buffer.lookup_transform('odom', target, stamp)
            translation = transform.transform.translation
            assert all(math.isfinite(v) for v in (translation.x,translation.y,translation.z))
            checks[target] = {'from':'odom', 'at_sim_time': stamp.nanoseconds*1e-9}
        return {'streams':report, 'tf':checks, 'rgb': {'stddev':float(np.std(colors))},
                'depth':{'finite_pixels':int(len(valid)), 'min_m':float(valid.min()), 'max_m':float(valid.max())},
                'lidar':{'finite_points':int(len(points)), 'width':self.latest['cloud'].width,
                         'height':self.latest['cloud'].height},
                'camera_intrinsics':list(info.k), 'camera_size':[rgb.width,rgb.height]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--motion', action='store_true', help='Actually drive forward/back/left/right for odometry evaluation')
    parser.add_argument('--output', default='/home/robot/sensor-verification')
    args = parser.parse_args()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    rclpy.init()
    node = Verification()
    signal.signal(signal.SIGTERM, lambda *_: setattr(node, 'interrupted', True))
    signal.signal(signal.SIGINT, lambda *_: setattr(node, 'interrupted', True))
    report = {'result':'FAIL', 'scope':'sensor streams, timestamps, TF, feedback odometry',
              'motion_test':args.motion, 'truth_usage':'evaluation only; never fused',
              'thresholds': {'distance_error_m':'max(0.12, 0.35 * actual displacement)',
                             'yaw_error_rad':0.15, 'stop_drift_m':0.08}}
    started = time.monotonic()
    try:
        deadline = time.monotonic()+100
        while len(node.latest)<len(node.topics):
            node.pump()
            if time.monotonic()>deadline:
                raise TimeoutError('Missing: '+str([name for name,_,_ in node.topics if name not in node.latest]))
            time.sleep(0.03)
        first_t = node.sample()['t']
        node.phase('stationary', 5)
        if args.motion:
            report['motion'] = {}
            for name, duration, forward, turn in [('forward',3,0.15,0), ('reverse',3,-0.12,0),
                                                  ('left',2,0,0.25), ('right',2,0,-0.25)]:
                a,b = node.phase(name,duration,forward,turn)
                values = {}
                for source in ('odom','truth'):
                    dx,dy=b[source+'_x']-a[source+'_x'], b[source+'_y']-a[source+'_y']
                    heading=a[source+'_yaw']
                    values[source+'_forward_m']=math.cos(heading)*dx+math.sin(heading)*dy
                    values[source+'_yaw_rad']=delta(b[source+'_yaw'],heading)
                values['distance_error_m']=abs(values['odom_forward_m']-values['truth_forward_m'])
                values['yaw_error_rad']=abs(delta(values['odom_yaw_rad'],values['truth_yaw_rad']))
                report['motion'][name]=values
                if forward:
                    assert values['truth_forward_m']*forward > 0.01, 'Actual motion direction failed'
                    assert values['odom_forward_m']*forward > 0.01, 'Odometry direction failed'
                    assert values['distance_error_m'] < max(0.12, abs(values['truth_forward_m'])*0.35), 'Excessive distance error'
                else:
                    assert values['truth_yaw_rad']*turn > 0.02 and values['odom_yaw_rad']*turn > 0.02
                    assert values['yaw_error_rad']<0.15, 'Excessive yaw error'
                node.phase(name+'_settle',2)
            a,b=node.phase('stop_hold',3)
            drift=math.hypot(b['odom_x']-a['odom_x'],b['odom_y']-a['odom_y'])
            report['stop_odom_drift_m']=drift
            assert drift<0.08, 'Odometry drifts at rest'
        report.update(node.check_streams())
        report['height_difference_m']=abs(node.latest['odom'].pose.pose.position.z-node.latest['truth'].pose.pose.position.z)
        assert report['height_difference_m']<0.06, 'Incorrect body height / TF'
        report['sim_seconds']=node.sample()['t']-first_t
        report['result']='PASS'
    except (Exception, KeyboardInterrupt) as error:
        report['error']=str(error)
        report['received']=list(node.latest)
    finally:
        # Always send a zero command, including failed assertions and interruption.
        for _ in range(5):
            try:
                node.command()
            except Exception:
                break
            time.sleep(0.03)
        report['wall_seconds']=time.monotonic()-started
        if report.get('sim_seconds'):
            report['average_real_time_factor']=report['sim_seconds']/report['wall_seconds']
        (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        if node.trace:
            with (output/'trace.csv').open('w',newline='') as stream:
                writer=csv.DictWriter(stream,fieldnames=node.trace[0].keys())
                writer.writeheader()
                writer.writerows(node.trace)
        node.destroy_node()
        rclpy.shutdown()
    print(json.dumps(report,ensure_ascii=False,indent=2))
    raise SystemExit(0 if report['result']=='PASS' else 1)


if __name__=='__main__':
    main()
