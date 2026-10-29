#!/usr/bin/env python3
"""Validate live sensor/TF chains, optionally move the physical robot to evaluate odometry."""
import argparse
from collections import defaultdict, deque
import csv
import fcntl
import json
import math
import os
from pathlib import Path
import signal
import subprocess
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
from tf2_ros import Buffer, TransformListener, TransformException
from sensor_measurements import (depth_array, motion_metrics, plane_intersections, rotation,
                                 motion_sequence, cumulative_motion_metrics, stream_timing)


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
        self.truth_history = deque(maxlen=2000)
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
        if name == 'truth':
            self.truth_history.append((stamp, msg))

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
            timing = stream_timing(self.stamps[name])
            hz = timing['sim_hz']
            assert not self.nonmonotonic[name], 'Time moved backwards: '+topic
            assert time.monotonic()-self.received[name] < 4, 'Stale '+topic
            assert abs(stamps[-1]-seconds(self.latest['clock'].clock)) < 0.4, 'Clock mismatch: '+topic
            frame = '' if name=='clock' else self.latest[name].header.frame_id
            if name in expected:
                assert frame == expected[name], f'{topic}: wrong frame {frame}'
            if name in minimum_hz:
                assert hz >= minimum_hz[name], f'{topic}: only {hz:.2f} sim Hz'
            report[topic] = {**timing, 'frame': frame, 'nonmonotonic': self.nonmonotonic[name]}
            if name == 'imu':
                assert timing['duplicate_timestamps'] <= len(stamps)*.01, 'Duplicate IMU measurement timestamps'
        rgb, depth, info = [self.latest[key] for key in ('rgb','depth','camera_info')]
        assert rgb.encoding=='rgb8' and depth.encoding=='32FC1'
        assert (rgb.width,rgb.height)==(depth.width,depth.height)==(info.width,info.height)
        assert (rgb.width,rgb.height) in ((320,240),(640,480)), 'Unexpected camera resolution'
        assert info.k[0]>0 and info.k[4]>0 and 0<info.k[2]<info.width and 0<info.k[5]<info.height
        assert abs(seconds(rgb.header.stamp)-seconds(info.header.stamp))<0.15
        colors = np.frombuffer(bytes(rgb.data), np.uint8)
        assert np.std(colors)>5, 'Blank/constant RGB image'
        depths = depth_array(depth)
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

    def sensor_pose(self, message):
        """Time-aligned ground truth is used only by this independent evaluator."""
        stamp = seconds(message.header.stamp)
        truth_time, truth = min(self.truth_history, key=lambda item: abs(item[0]-stamp))
        if abs(truth_time-stamp) > .05:
            raise ValueError('No time-aligned truth for range evaluation')
        fixed = self.buffer.lookup_transform('base_link', message.header.frame_id,
                                             rclpy.time.Time.from_msg(message.header.stamp)).transform
        p, q = truth.pose.pose.position, truth.pose.pose.orientation
        base_rotation = rotation((q.x,q.y,q.z,q.w))
        t, q = fixed.translation, fixed.rotation
        origin = base_rotation @ [t.x,t.y,t.z] + [p.x,p.y,p.z]
        return origin, base_rotation @ rotation((q.x,q.y,q.z,q.w)), abs(truth_time-stamp)

    def check_ranges(self, report):
        """Measure two temporary real scene walls; never move/set robot poses."""
        def service(action, kind, request):
            result = subprocess.run(['gz','service','-s','/world/default/'+action,
                '--reqtype',kind,'--reptype','gz.msgs.Boolean','--timeout','5000','--req',request],
                capture_output=True,text=True,timeout=10)
            if result.returncode or 'true' not in result.stdout:
                raise RuntimeError(action+': '+result.stdout+result.stderr)
        report['range_tests'] = []
        for distance in (1.5, 3.0):
            start = self.sample()
            heading = start['truth_yaw']
            normal = np.array([math.cos(heading),math.sin(heading),0.])
            tangent = np.array([-normal[1],normal[0],0.])
            centre = np.array([start['truth_x'],start['truth_y'],0.]) + (distance+.02)*normal
            offset = float(normal @ centre-.02)
            name = 'range_reference_'+str(time.time_ns())
            item = {'nominal_body_plane_m':distance, 'reference':'known static wall plane + time-aligned truth for evaluation only',
                    'depth_z_tolerance_m':.03,'lidar_range_tolerance_m':.04,'wall_removed':False,'result':'FAIL'}
            report['range_tests'].append(item)
            sdf = f'''<sdf version="1.8"><model name="{name}"><static>true</static>
<pose>{centre[0]} {centre[1]} 1 0 0 {heading}</pose><link name="wall">
<collision name="collision"><geometry><box><size>0.04 4 2</size></box></geometry></collision>
<visual name="visual"><geometry><box><size>0.04 4 2</size></box></geometry>
<material><ambient>0.1 0.5 0.8 1</ambient><diffuse>0.1 0.5 0.8 1</diffuse></material></visual>
</link></model></sdf>'''
            created = False
            try:
                service('create','gz.msgs.EntityFactory','sdf: '+json.dumps(sdf))
                created = True
                minimum_stamp = start['t']+.5
                errors = {'depth':[], 'cloud':[]}
                measured = {'depth':[], 'cloud':[]}
                expected = {'depth':[], 'cloud':[]}
                frames = {'depth':0,'cloud':0}
                last = {}
                alignment = []
                deadline=time.monotonic()+100
                while min(frames.values())<5:
                    self.command()
                    self.pump()
                    for key in frames:
                        if frames[key]>=5: continue
                        message=self.latest[key]
                        stamp=seconds(message.header.stamp)
                        if stamp<minimum_stamp or stamp==last.get(key): continue
                        try:
                            origin, matrix, skew = self.sensor_pose(message)
                        except (ValueError, TransformException) as error:
                            # TF/truth can arrive one callback after the image; retry it.
                            if time.monotonic()>deadline: raise RuntimeError('Range pose unavailable: '+str(error)) from error
                            continue
                        if key=='depth':
                            values=depth_array(message)
                            info=self.latest['camera_info']
                            if (info.width,info.height)!=(message.width,message.height):
                                raise ValueError('Depth and camera intrinsics size mismatch')
                            u,v=np.meshgrid(np.arange(message.width//2-4,message.width//2+5),
                                            np.arange(message.height//2-4,message.height//2+5))
                            rays=np.column_stack(((u.ravel()-info.k[2])/info.k[0],
                                                   (v.ravel()-info.k[5])/info.k[4],np.ones(u.size))) @ matrix.T
                            actual=values[v.ravel(),u.ravel()]
                        else:
                            points=point_cloud2.read_points_numpy(message,field_names=('x','y','z'),skip_nans=True).reshape(-1,3)
                            points=points[np.isfinite(points).all(axis=1)]
                            actual=np.linalg.norm(points,axis=1)
                            good=actual>.8
                            actual,points=actual[good],points[good]
                            rays=(points/actual[:,None]) @ matrix.T
                        reference=plane_intersections(origin,rays,normal,offset)
                        intersections=origin+reference[:,None]*rays
                        lateral=(intersections-centre) @ tangent
                        valid=(np.isfinite(reference) & np.isfinite(actual) & (actual>0)
                               & (np.abs(lateral)<1.8) & (intersections[:,2]>.15) & (intersections[:,2]<1.85))
                        if np.count_nonzero(valid)<10:
                            last[key]=stamp
                            continue
                        errors[key].extend(np.abs(actual[valid]-reference[valid]).tolist())
                        measured[key].append(float(np.median(actual[valid])))
                        expected[key].append(float(np.median(reference[valid])))
                        frames[key]+=1
                        last[key]=stamp
                        alignment.append(skew)
                    if time.monotonic()>deadline:
                        raise TimeoutError('Not enough reference-wall returns: '+str(frames))
                    time.sleep(.025)
                for key,label,tolerance in [('depth','depth_optical_z',.03),('cloud','lidar_radial_range',.04)]:
                    item[label]={'frames':frames[key], 'samples':len(errors[key]),
                        'measured_m':float(np.median(measured[key])), 'expected_m':float(np.median(expected[key])),
                        'median_absolute_error_m':float(np.median(errors[key])),
                        'p95_absolute_error_m':float(np.percentile(errors[key],95))}
                    assert item[label]['median_absolute_error_m']<tolerance, label+' distance error'
                    assert item[label]['p95_absolute_error_m']<tolerance*2, label+' unstable distance returns'
                item['max_truth_alignment_s']=max(alignment)
                item['result']='PASS'
            finally:
                if created:
                    service('remove','gz.msgs.Entity',f'name: "{name}" type: MODEL')
                    item['wall_removed']=True
            print('range',json.dumps(item),flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--motion', action='store_true', help='Actually drive forward/back/left/right for odometry evaluation')
    parser.add_argument('--motion-profile', choices=('standard','reverse-first'), default='standard',
                        help='Independent motion order, duration and speed for odometry regression')
    parser.add_argument('--max-cumulative-error', type=float,
                        help='Fail if cumulative 2D endpoint error exceeds this many metres; requires --motion')
    parser.add_argument('--ranges', action='store_true', help='Check RGB-D optical Z and lidar range against two temporary walls')
    parser.add_argument('--output', default='/home/robot/sensor-verification')
    args = parser.parse_args()
    if args.max_cumulative_error is not None:
        if not args.motion or not math.isfinite(args.max_cumulative_error) or args.max_cumulative_error <= 0:
            parser.error('--max-cumulative-error requires --motion and a finite positive limit')
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    lock_path=Path.home()/'.cache/robot-dog-sensor-verification.lock'
    lock_path.parent.mkdir(exist_ok=True)
    lock=lock_path.open('a')
    try:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:
        parser.error('已有传感器验证正在运行，请等待它完成。')
    rclpy.init()
    node = Verification()
    signal.signal(signal.SIGTERM, lambda *_: setattr(node, 'interrupted', True))
    signal.signal(signal.SIGINT, lambda *_: setattr(node, 'interrupted', True))
    signal.signal(signal.SIGHUP, lambda *_: setattr(node, 'interrupted', True))
    report = {'result':'FAIL', 'scope':'sensor streams, timestamps, TF, feedback odometry',
              'motion_test':args.motion, 'motion_profile':args.motion_profile if args.motion else None,
              'truth_usage':'evaluation only; never fused',
              'thresholds': {'distance_error_m':'max(0.12, 0.35 * actual displacement)',
                             'distance_error_definition':'2D endpoint displacement in each source starting-body frame',
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
        if args.ranges:
            node.check_ranges(report)
        if args.motion:
            report['motion'] = {}
            motion_origin=node.sample()
            for name, duration, forward, turn in motion_sequence(args.motion_profile):
                a,b = node.phase(name,duration,forward,turn)
                values = motion_metrics(a,b)
                report['motion'][name]=values
                assert values['yaw_error_rad']<0.15, 'Excessive yaw error'
                if forward:
                    assert values['truth_forward_m']*forward > 0.01, 'Actual motion direction failed'
                    assert values['odom_forward_m']*forward > 0.01, 'Odometry direction failed'
                    travel=math.hypot(values['truth_forward_m'],values['truth_lateral_m'])
                    assert values['position_error_m'] < max(0.12, travel*0.35), 'Excessive 2D distance error'
                else:
                    assert values['truth_yaw_rad']*turn > 0.02 and values['odom_yaw_rad']*turn > 0.02
                node.phase(name+'_settle',2)
            a,b=node.phase('stop_hold',3)
            drift=math.hypot(b['odom_x']-a['odom_x'],b['odom_y']-a['odom_y'])
            report['stop_odom_drift_m']=drift
            report['stop_truth_drift_m']=math.hypot(b['truth_x']-a['truth_x'],b['truth_y']-a['truth_y'])
            assert report['stop_truth_drift_m']<0.08,'Actual robot drifts at rest'
            assert drift<0.08, 'Odometry drifts at rest'
            report['motion_summary']=cumulative_motion_metrics(motion_origin,b,args.max_cumulative_error)
            if args.max_cumulative_error is not None:
                assert report['motion_summary']['within_limit'], 'Excessive cumulative 2D endpoint error'
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
