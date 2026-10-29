#!/usr/bin/env python3
"""Drive a physical loop using feedback odometry; independently evaluate SLAM."""
import argparse
import csv
import datetime
import json
import math
from pathlib import Path
import time
import xml.etree.ElementTree as ET

import numpy as np
import rclpy
from rclpy.node import Node
from rclpy.parameter import Parameter
from rclpy.qos import QoSProfile, DurabilityPolicy
from geometry_msgs.msg import Twist
from nav_msgs.msg import OccupancyGrid, Odometry
from sensor_msgs.msg import LaserScan, JointState, Imu
from tf2_ros import Buffer, TransformListener, TransformException
from slam_session import save_session, load_session


def yaw(q):
    return math.atan2(2*(q.w*q.z+q.x*q.y),1-2*(q.y*q.y+q.z*q.z))


def wrap(angle):
    return math.atan2(math.sin(angle),math.cos(angle))


class Verification(Node):
    def __init__(self):
        super().__init__('robotdog_slam_verification',parameter_overrides=[Parameter('use_sim_time',value=True)])
        self.latest,self.received,self.counts={},{},{}
        self.trace=[]
        self.pub=self.create_publisher(Twist,'/cmd_vel',10)
        self.buffer=Buffer(node=self)
        self.listener=TransformListener(self.buffer,self)
        for name,kind,topic in [('scan',LaserScan,'/slam/scan'),('odom',Odometry,'/odom'),
                ('truth',Odometry,'/odom/ground_truth'),('joints',JointState,'/joint_states'),('imu',Imu,'/imu/data')]:
            self.create_subscription(kind,topic,lambda msg,n=name:self.remember(n,msg),rclpy.qos.qos_profile_sensor_data)
        self.create_subscription(OccupancyGrid,'/map',lambda msg:self.remember('map',msg),
            QoSProfile(depth=1,durability=DurabilityPolicy.TRANSIENT_LOCAL))

    def remember(self,name,msg):
        self.latest[name]=msg
        self.received[name]=time.monotonic()
        self.counts[name]=self.counts.get(name,0)+1

    def pump(self):
        for _ in range(150):
            rclpy.spin_once(self,timeout_sec=0)

    def now(self):
        return self.get_clock().now().nanoseconds*1e-9

    def pose(self,name):
        if time.monotonic()-self.received.get(name,0)>3:
            raise RuntimeError('Stale '+name)
        p=self.latest[name].pose.pose
        return p.position.x,p.position.y,yaw(p.orientation)

    def map_pose(self):
        p=self.buffer.lookup_transform('map','base_footprint',rclpy.time.Time()).transform
        return p.translation.x,p.translation.y,yaw(p.rotation)

    def snapshot(self):
        o,t,m=self.pose('odom'),self.pose('truth'),self.map_pose()
        row={'t':self.now(),**{prefix+'_'+key:value for prefix,values in [('odom',o),('truth',t),('slam',m)]
                            for key,value in zip(('x','y','yaw'),values)}}
        self.trace.append(row)
        return row

    def wait_ready(self):
        deadline=time.monotonic()+180
        while time.monotonic()<deadline:
            self.pump()
            if all(name in self.latest for name in ('scan','odom','truth','map','joints','imu')):
                try:
                    self.map_pose()
                    scan=self.latest['scan']
                    assert scan.header.frame_id=='base_footprint'
                    assert sum(math.isfinite(v) for v in scan.ranges)>=15
                    assert len(self.latest['joints'].position)==12
                    assert self.latest['map'].header.frame_id=='map'
                    return
                except (TransformException,AssertionError):
                    pass
            time.sleep(.03)
        raise TimeoutError('SLAM inputs/map/TF not ready: '+str(self.counts))

    def drive(self,distance=None,turn=None):
        # Control consumes feedback /odom only. Ground truth is never read here.
        start=self.pose('odom')
        start_sim=self.now()
        deadline=time.monotonic()+180
        while time.monotonic()<deadline:
            self.pump()
            current=self.pose('odom')
            command=Twist()
            if distance is not None:
                travelled=math.hypot(current[0]-start[0],current[1]-start[1])
                if travelled>=distance:
                    break
                command.linear.x=.18
                command.angular.z=max(-.12,min(.12,1.0*wrap(start[2]-current[2])))
            else:
                remaining=turn-wrap(current[2]-start[2])
                if abs(remaining)<.025:
                    break
                command.angular.z=math.copysign(min(.25,max(.07,abs(remaining)*.8)),remaining)
            assert self.now()-start_sim<30, 'Motion did not finish in simulation time'
            q=self.latest['imu'].orientation
            roll=math.atan2(2*(q.w*q.x+q.y*q.z),1-2*(q.x*q.x+q.y*q.y))
            pitch=math.asin(max(-1,min(1,2*(q.w*q.y-q.z*q.x))))
            assert abs(roll)<.45 and abs(pitch)<.45, 'Unsafe body inclination'
            self.pub.publish(command)
            self.snapshot()
            time.sleep(.04)
        else:
            raise TimeoutError('Physical motion timed out')
        self.hold(1.0)

    def hold(self,seconds):
        end=self.now()+seconds
        deadline=time.monotonic()+90
        while self.now()<end:
            self.pump()
            self.pub.publish(Twist())
            self.snapshot()
            if time.monotonic()>deadline:
                raise TimeoutError('Simulation clock stalled')
            time.sleep(.04)

    def statistics(self):
        message=self.latest['map']
        data=np.asarray(message.data,dtype=np.int16)
        return {'width':message.info.width,'height':message.info.height,'resolution_m':message.info.resolution,
                'occupied_cells':int(np.count_nonzero(data>=65)),
                'free_cells':int(np.count_nonzero(data==0)),
                'unknown_cells':int(np.count_nonzero(data<0)),
                'map_messages':self.counts.get('map',0),'scan_messages':self.counts.get('scan',0),
                'finite_scan_beams':sum(math.isfinite(v) for v in self.latest['scan'].ranges)}

    def geometry(self, initial):
        """Compare measured occupied cells with independent static SDF geometry."""
        from ament_index_python.packages import get_package_share_directory
        path=Path(get_package_share_directory('unitree_go2_description'))/'worlds/default.sdf'
        message=self.latest['map']
        cells=np.asarray(message.data).reshape(message.info.height,message.info.width)
        rows,cols=np.where(cells>=65)
        points=np.column_stack(((cols+.5)*message.info.resolution+message.info.origin.position.x,
                                (rows+.5)*message.info.resolution+message.info.origin.position.y))
        angle=initial['truth_yaw']-initial['slam_yaw']
        rotation=np.array([[math.cos(angle),-math.sin(angle)],[math.sin(angle),math.cos(angle)]])
        points=(points-(initial['slam_x'],initial['slam_y']))@rotation.T+(initial['truth_x'],initial['truth_y'])
        distances=[]
        per_object={}
        for model in ET.parse(path).findall('.//world/model'):
            geometry=model.find('link/collision/geometry')
            if geometry is None:continue
            pose=[float(v) for v in model.findtext('pose','0 0 0 0 0 0').split()]
            ca,sa=math.cos(pose[5]),math.sin(pose[5])
            local=(points-pose[:2])@np.array([[ca,-sa],[sa,ca]])
            if geometry.find('box') is not None:
                size=[float(v) for v in geometry.findtext('box/size').split()]
                d=np.abs(local)-np.asarray(size[:2])/2
                error=np.abs(np.linalg.norm(np.maximum(d,0),axis=1)+np.minimum(np.max(d,axis=1),0))
            elif geometry.find('cylinder') is not None:
                radius=float(geometry.findtext('cylinder/radius'))
                error=np.abs(np.linalg.norm(local,axis=1)-radius)
            else:continue
            distances.append(error)
            per_object[model.attrib['name']]=int(np.count_nonzero(error<.12))
        assert len(points)>0 and len(distances)>0,'No real obstacle cells to evaluate'
        nearest=np.min(distances,axis=0)
        result={'occupied_cells':len(points),'median_boundary_error_m':float(np.median(nearest)),
                'p90_boundary_error_m':float(np.percentile(nearest,90)),
                'cells_within_12cm_by_object':per_object,
                'method':'SDF boundaries and independent initial world/map alignment for evaluation only'}
        assert result['p90_boundary_error_m']<.20,'Map obstacle boundary error exceeds 20 cm'
        assert sum(v>=3 for v in per_object.values())>=4,'Fewer than four scene obstacles mapped'
        return result


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,required=True,help='A new evidence directory; never reuse an old PASS')
    parser.add_argument('--motion',action='store_true',help='Physically drive a small rectangle using feedback odometry')
    parser.add_argument('--load',type=Path,help='Load a saved graph into a freshly started mapper near its saved position')
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    rclpy.init()
    node=Verification()
    report={'result':'FAIL','motion':args.motion,'truth_usage':'evaluation only; never commands or SLAM inputs',
            'algorithm':'slam_toolbox','loop_constraint_event_confirmed':False}
    began=time.monotonic()
    try:
        node.wait_ready()
        if args.load:
            load_session(node,args.load)
            node.hold(3)
            report['loaded_previous_session']=str(args.load)
        node.hold(2)
        initial=node.snapshot()
        before=node.statistics()
        report['before']=before
        if args.motion:
            for side in (1.3,1.0,1.3,1.0):
                node.drive(distance=side)
                node.drive(turn=math.pi/2)
        node.hold(3)
        final=node.snapshot()
        after=node.statistics()
        assert after['occupied_cells']>=30 and after['free_cells']>=300,'Insufficient observed map geometry'
        assert after['map_messages']>=before['map_messages']+2,'Map did not update'
        if args.motion:
            travelled=sum(math.hypot(b['truth_x']-a['truth_x'],b['truth_y']-a['truth_y'])
                          for a,b in zip(node.trace,node.trace[1:]))
            assert travelled>=3.5,'No substantial physical motion'
            report['physical_path_length_m']=travelled
            report['physical_return_error_m']=math.hypot(final['truth_x']-initial['truth_x'],final['truth_y']-initial['truth_y'])
            report['slam_return_error_m']=math.hypot(final['slam_x']-initial['slam_x'],final['slam_y']-initial['slam_y'])
            assert report['physical_return_error_m']<.35,'Physical loop did not return near start'
            assert report['slam_return_error_m']<.35,'SLAM loop endpoint excessive'
        report['after']=after
        report['start']=initial
        report['end']=final
        report['geometry']=node.geometry(initial)
        session=save_session(node,args.output/'saved_map',node.map_pose())
        report['saved_session']=str(session)
        report['save_files']={p.name:p.stat().st_size for p in session.parent.iterdir() if p.is_file()}
        # Real deserialize API, followed by new live map and TF observations.
        old_count=node.counts['map']
        load_session(node,session)
        node.hold(3)
        assert node.counts['map']>old_count,'No fresh map after deserialization'
        node.map_pose()
        report['deserialize_verified']=True
        report['simulation_seconds']=node.now()-initial['t']
        report['wall_seconds']=time.monotonic()-began
        report['real_time_factor']=report['simulation_seconds']/report['wall_seconds']
        report['result']='PASS'
    except Exception as error:
        report['error']=repr(error)
        raise
    finally:
        for _ in range(5):
            node.pub.publish(Twist())
            time.sleep(.05)
        (args.output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        if node.trace:
            with (args.output/'trace.csv').open('w',newline='') as stream:
                writer=csv.DictWriter(stream,fieldnames=list(node.trace[0]));writer.writeheader();writer.writerows(node.trace)
        node.destroy_node()
        rclpy.shutdown()
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
